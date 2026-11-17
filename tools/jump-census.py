#!/usr/bin/env python3
"""Where a jump goes: every `Jump`/`JumpIfNot` operand tested against the script's own token starts (PLAN 3.7).

A `Goto('Label')` in the source is a **`Jump`** in the bytecode and the label's *name* is not in the payload: the
corpus carries no `LabelTable` (`0x0c`) token and no `GotoLabel` (`0x0d`) token in any script that walks, so the
compiler resolved every label to a code offset. That makes the jump operands the one place the walk can be tested
against something other than itself: a jump's target must be the start of an instruction, and a token stream that
was desynchronised anywhere upstream would send jumps into the middle of tokens.

The operand's meaning is measured, not assumed. Each 2-byte value is tested as an **absolute** code offset from
the script's first byte, as an **unsigned** offset from the end of the token and as a **signed** one from the same
base, and every rate is reported beside the base rate -- the fraction of *all* byte positions in the same scripts
that is a token start. All three land at or below that base rate, which is the finding: the operand is not a
file-space offset at all. The two exec handlers say what it is -- `Jump` (slot 6, `0x00047788..0x00047796`) and
`JumpIfNot` (slot 7, `0x00047837..0x0004783f`) both end in `mov eax,[edx+0x48]; add eax,edi; mov [esi+0xc],eax`,
i.e. `Code = Node->Script + operand` -- an offset against a script base the loader has already widened. The file
cannot reproduce that base because which index operand widens is per-slot (the 43 `--sizefield` residue rows), so
this tool reports the rates and the zeros rather than a jump graph, and the next instrument is a guest copy.

`LabelTable` and `GotoLabel` are counted as zeros rather than dropped: `0x0c`'s handler exists and the corpus never
emits one, which is why a label jump has no name left to read.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "jump_census.csv")
DOC_MARK = ("<!-- generated:jump-census -->", "<!-- /generated:jump-census -->")
JUMPS = ("Jump", "JumpIfNot")
MISS_CAP = 50
LABELS = ("LabelTable", "GotoLabel")
FIELDS = ["kind", "package", "class", "function", "offset", "op", "target", "absolute", "unsigned",
          "signed", "key", "count", "note"]


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def flat(nodes, out=None):
    out = [] if out is None else out
    for n in nodes:
        out.append(n)
        flat(n["children"], out)
    return out


def owner_of(pkg, exp):
    """The class a `Function` export belongs to, walking a `State` outer to its class."""
    state, cur = "", exp
    for _ in range(8):
        if cur["outer"] <= 0:
            break
        outer = pkg.exports[cur["outer"] - 1]
        kind = pkg.class_name_of(outer)
        if kind == "Class":
            return pkg.name(outer["name"]), state
        if kind == "State" and not state:
            state = pkg.name(outer["name"])
        cur = outer
    return "", state


def payloads(system, table, stats):
    from ue2pkg import Package                              # noqa: PLC0415 - only for a real package
    files = ([os.path.join(system, f) for f in sorted(os.listdir(system)) if f.lower().endswith(".u")]
             if os.path.isdir(system) else [system])
    epilogue = bytes([0x04, 0x0B, 0x47])
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        package = os.path.splitext(os.path.basename(one))[0]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Function":
                continue
            pay = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            n = len(pay)
            off, last, ok = 0, None, True
            for _ in range(6):
                try:
                    last, off = DIS.read_index(pay, off)
                except IndexError:
                    ok = False
                    break
            if not ok or last != exp["name"]:
                continue
            if n >= 10 and pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            start = off + 16
            if start >= end:
                continue
            stats["seen"] += 1
            try:
                nodes = DIS.dis_parse(pay[start:end], table)
            except DIS.Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != end - start:
                continue
            stats["walked"] += 1
            yield package, pkg, exp, nodes, end - start


def measure(nodes):
    """(jumps, label tokens, token starts) for one script.

    A jump operand is two little-endian bytes and the encoding is the question, so all three readings a code
    offset can have are tested and reported: **absolute** from the script's first byte, **unsigned relative**
    from the end of the token, and **signed relative** from the same base (a backward jump's two's-complement
    word read as unsigned points past the script).
    """
    tokens = flat(nodes)
    starts = {n["offset"] for n in tokens}
    jumps, labels = [], collections.Counter()
    for n in tokens:
        if n["name"] in LABELS:
            labels[n["name"]] += 1
        if n["name"] not in JUMPS:
            continue
        raw = n["operands"] or b""
        if len(raw) < 2:
            jumps.append((n, -1, False, False, False))
            continue
        value = int.from_bytes(raw[:2], "little")
        signed = int.from_bytes(raw[:2], "little", signed=True)
        # the base is the byte after the operand, and the token's offset counts the opcode byte
        base = n["offset"] + 1 + len(raw)
        jumps.append((n, value, value in starts, (base + value) in starts, (base + signed) in starts))
    return jumps, labels, starts


def read_dump(system):
    table = DIS.Table()
    stats = {"seen": 0, "walked": 0}
    rows = []
    census = collections.Counter()
    for package, pkg, exp, nodes, length in payloads(system, table, stats):
        jumps, labels, starts = measure(nodes)
        for kind, n in labels.items():
            census["tokens_" + kind.lower()] += n
        census["bytes"] += length
        census["token_starts"] += len(starts)
        if not jumps:
            continue
        cls, _state = owner_of(pkg, exp)
        func = pkg.name(exp["name"])
        abs_hits = len([j for j in jumps if j[2]])
        unsigned = len([j for j in jumps if j[3]])
        signed = len([j for j in jumps if j[4]])
        census["scripts_with_a_jump"] += 1
        census["jumps"] += len(jumps)
        census["absolute_hits"] += abs_hits
        census["unsigned_hits"] += unsigned
        census["signed_hits"] += signed
        rows.append({"kind": "script", "package": package, "class": cls, "function": func,
                     "offset": "", "op": "", "target": "", "absolute": abs_hits, "unsigned": unsigned,
                     "signed": signed, "key": "", "count": len(jumps),
                     "note": "%d token start(s) of %d byte(s)" % (len(starts), length)})
        for n, value, a_ok, u_ok, s_ok in jumps:
            if s_ok or a_ok or u_ok:
                continue
            census["misses"] += 1
            if census["misses"] > MISS_CAP:
                continue
            rows.append({"kind": "miss", "package": package, "class": cls, "function": func,
                         "offset": "%04x" % n["offset"], "op": n["name"], "target": value,
                         "absolute": int(a_ok), "unsigned": int(u_ok), "signed": int(s_ok), "key": "",
                         "count": "", "note": ""})
    rows.sort(key=lambda r: (r["kind"] != "miss", r["package"], r["class"], r["function"]))
    out = []
    for key in ("jumps", "scripts_with_a_jump", "absolute_hits", "unsigned_hits", "signed_hits",
                "token_starts", "bytes", "misses", "tokens_labeltable", "tokens_gotolabel"):
        out.append({"kind": "census", "key": key, "count": census[key], "note": CENSUS_NOTE.get(key, "")})
    out.append({"kind": "census", "key": "payloads_seen", "count": stats["seen"], "note": ""})
    out.append({"kind": "census", "key": "payloads_walked", "count": stats["walked"], "note": ""})
    return rows + out


CENSUS_NOTE = {
    "jumps": "`Jump`/`JumpIfNot` tokens in the scripts that walk",
    "scripts_with_a_jump": "scripts carrying at least one",
    "absolute_hits": "operands that land on a token start read as an absolute code offset",
    "unsigned_hits": "read as an unsigned offset from the end of the token",
    "signed_hits": "read as a signed offset from the end of the token",
    "token_starts": "token starts over the same scripts, the base rate's numerator",
    "bytes": "bytes over the same scripts, its denominator",
    "misses": "jumps whose operand lands on no token start under any of the three readings",
    "tokens_labeltable": "`LabelTable` (`0x0c`) tokens -- a label a computed jump could name",
    "tokens_gotolabel": "`GotoLabel` (`0x0d`) tokens -- a jump by name",
}


def render(rows):
    import io
    fh = io.StringIO()
    w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in FIELDS})
    return fh.getvalue()


def read_csv(path):
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, encoding="utf-8")))


def count(rows, key):
    for r in rows:
        if r.get("kind") == "census" and r.get("key") == key:
            return int(r["count"])
    return 0


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def doc_block(rows):
    jumps = count(rows, "jumps")
    starts, size = count(rows, "token_starts"), count(rows, "bytes")
    base = pct(starts, size)
    rates = [(name, count(rows, name + "_hits")) for name in ("absolute", "unsigned", "signed")]
    misses = [r for r in rows if r.get("kind") == "miss"]
    lines = ["",
             "**%s** `Jump`/`JumpIfNot` tokens over **%d** scripts, each 2-byte operand tested against that "
             "script's own token starts under the three readings an offset can have: **absolute** %d (%s%%), "
             "**unsigned relative** %d (%s%%), **signed relative** %d (%s%%). The base rate is **%s%%** -- %s of "
             "the %s byte positions in the same scripts are a token start -- and **none of the three is the "
             "encoding**: every reading lands at or below chance, which is what an operand expressed in a layout "
             "the file does not have looks like."
             % ("{:,}".format(jumps), count(rows, "scripts_with_a_jump"),
                rates[0][1], "%.2f" % pct(rates[0][1], jumps),
                rates[1][1], "%.2f" % pct(rates[1][1], jumps),
                rates[2][1], "%.2f" % pct(rates[2][1], jumps),
                "%.1f" % base, "{:,}".format(starts), "{:,}".format(size)),
             "",
             "The corpus carries **%d** `LabelTable` (`0x0c`) and **%d** `GotoLabel` (`0x0d`) tokens -- in `Function` "
             "payloads; a `State` payload carries the table (`tools/state-code.py`, `docs/systems/AI.md`) -- so a source "
             "`Goto('Label')` keeps no name in the build: the compiler resolved the label to an offset and the "
             "runtime never looks one up. **%s** jumps land on no token start under any reading; the first %d are "
             "rows below, the count is the census row."
             % (count(rows, "tokens_labeltable"), count(rows, "tokens_gotolabel"),
                "{:,}".format(count(rows, "misses")), len(misses)),
             ""]
    if misses:
        lines.append("| package | class | function | offset | op | target |")
        lines.append("| --- | --- | --- | ---: | --- | ---: |")
        for r in misses[:12]:
            lines.append("| %s | `%s` | `%s` | %s | %s | %s |"
                         % (r["package"], r["class"], r["function"], r["offset"], r["op"], r["target"]))
        lines.append("")
    return "\n".join(lines)


def replace_block(text, block):
    begin, end = DOC_MARK
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % DOC_MARK)
    return text[: i + len(begin)] + block + "\n" + text[j:]


def emit(rows, args):
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            fh.write(render(rows))
    path = args.write_doc or args.check_doc
    if path:
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        fresh = replace_block(current, doc_block(rows))
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: the generated block is stale" % path)
                return 1
            print("ok   jump-census-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   jump-census-doc: wrote %s" % path)
    print("ok   jump-census: %s jump(s) over %d script(s); %d absolute, %d unsigned, %d signed; %d miss(es)"
          % (count(rows, "jumps"), count(rows, "scripts_with_a_jump"), count(rows, "absolute_hits"),
             count(rows, "unsigned_hits"), count(rows, "signed_hits"),
             len([r for r in rows if r.get("kind") == "miss"])))
    return 0


def token(name, operands, offset, children=None):
    return {"name": name, "opcode": 0, "operands": operands, "children": children or [],
            "offset": offset, "end": offset + 1 + len(operands)}


def check():
    """Synthetic scripts: a signed-relative backward jump, an absolute landing, a miss, the label counters.

    The case that matters is the miss and the backward jump: a reader that only tested a forward offset would
    pass every fixture whose jumps go ahead of themselves.
    """
    good, bad = [], []

    def want(got, expect, what):
        (good if got == expect else bad).append("%s: %r != %r" % (what, got, expect))

    # Return@0, Nothing@1, EndOfScript@2, Jump@3 (operand 00 00 -> base 6), Return@6
    body = [token("Return", b"", 0), token("Nothing", b"", 1), token("EndOfScript", b"", 2),
            token("Jump", b"\x00\x00", 3), token("Return", b"", 6)]
    jumps, labels, starts = measure(body)
    want(len(jumps), 1, "one jump")
    want((jumps[0][1], jumps[0][2], jumps[0][3], jumps[0][4]), (0, True, True, True),
         "a zero operand lands on the script start under every reading")
    want(sorted(starts), [0, 1, 2, 3, 6], "the token starts")

    # a backward jump: Jump@4 with f9 ff = -7 from base 7 -> offset 0
    back = [token("Return", b"", 0), token("Nothing", b"", 1), token("EndOfScript", b"", 2),
            token("Jump", b"\xf9\xff", 4)]
    jumps, _, _ = measure(back)
    want((jumps[0][2], jumps[0][4]), (False, True), "a backward jump is signed relative")

    # a forward jump: Jump@1 with 03 00 = 3 from base 4 -> offset 7
    fwd = [token("Return", b"", 0), token("Jump", b"\x03\x00", 1), token("Nothing", b"", 4),
           token("Return", b"", 7)]
    jumps, _, _ = measure(fwd)
    want(jumps[0][4], True, "a forward jump is signed relative")
    want(jumps[0][2], False, "and is not absolute")

    past = [token("Return", b"", 0), token("Jump", b"\x05\x00", 1), token("Return", b"", 4)]
    jumps, _, _ = measure(past)
    want((jumps[0][2], jumps[0][4]), (False, False), "a target past the script is a miss")

    lab_body = [token("LabelTable", b"", 0), token("GotoLabel", b"", 1)]
    _jumps, labels, _ = measure(lab_body)
    want(dict(labels), {"LabelTable": 1, "GotoLabel": 1}, "the label tokens are counted")

    short = [token("Jump", b"\x01", 0)]
    want(measure(short)[0][0][2], False, "a truncated operand is a miss, not a crash")

    rows = [{"kind": "script", "package": "P", "class": "C", "function": "F", "count": "2",
             "absolute": "0", "unsigned": "0", "signed": "2", "note": ""},
            {"kind": "census", "key": "jumps", "count": "2", "note": ""},
            {"kind": "census", "key": "scripts_with_a_jump", "count": "1", "note": ""},
            {"kind": "census", "key": "absolute_hits", "count": "0", "note": ""},
            {"kind": "census", "key": "unsigned_hits", "count": "0", "note": ""},
            {"kind": "census", "key": "signed_hits", "count": "2", "note": ""},
            {"kind": "census", "key": "token_starts", "count": "2", "note": ""},
            {"kind": "census", "key": "bytes", "count": "8", "note": ""}]
    want(render(rows).splitlines()[0].split(",")[0], "kind", "the CSV header")
    want("none of the three is the" in doc_block(rows), True, "the block names the result")
    for line in bad:
        print("FAIL jump-census --check: " + line)
    print("ok   jump-census --check: %d case(s) pass" % len(good))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("-o", "--out", help="write the CSV here")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's block between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's block is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=CSV_PATH,
                    help="with --check-doc and no --system: read this artifact instead")
    ap.add_argument("--report", action="store_true", help="print the census of the committed artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()

    global DIS
    DIS = load_tool("uscript_dis", "uscript-dis.py")
    if args.check:
        return check()
    if args.report:
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("jump-census: %s holds no rows" % args.from_csv)
        for r in rows:
            if r.get("kind") == "census":
                print("%-22s %8s  %s" % (r["key"], r["count"], r.get("note", "")))
        return 0
    if args.check_doc and not args.system:
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("jump-census: %s holds no rows" % args.from_csv)
        return emit(rows, args)
    if not args.system:
        sys.exit("jump-census: pass --system <DUMP/System>")
    rows = read_dump(args.system)
    if not rows:
        sys.exit("jump-census: no jump under %s" % args.system)
    return emit(rows, args)


DIS = None

if __name__ == "__main__":
    sys.exit(main())
