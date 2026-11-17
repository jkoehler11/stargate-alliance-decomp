#!/usr/bin/env python3
"""Where a jump goes: every `Jump`/`JumpIfNot` operand tested against the script's token starts in both layouts (PLAN 3.7).

A `Goto('Label')` in the source is a **`Jump`** in the bytecode and the label's *name* is not in the payload: the
corpus carries no `LabelTable` (`0x0c`) token and no `GotoLabel` (`0x0d`) token in any script that walks, so the
compiler resolved every label to a code offset. That makes the jump operands the one place the walk can be tested
against something other than itself: a jump's target must be the start of an instruction, and a token stream that
was desynchronised anywhere upstream would send jumps into the middle of tokens.

**Four readings, one of them holds.** Each 2-byte operand is tested as an **absolute** file offset from the script's
first byte, as an **unsigned** and a **signed** offset from the end of the token, and as an offset into the script
**as the loader rebuilds it in memory** -- the file with every compact `index` operand widened to four bytes, which
is the layout `FFrame::Code = Node->Script + operand` (`Jump` `0x00047788`, `JumpIfNot` `0x00047837`) computes
against. The three file readings land at or below the file's own chance rate; the widened reading lands on
**23,778 of 23,778** operands, and `inventory/jump_graph.csv` (a separate walk, `tools/jump-graph.py`) puts the same
number on the same sites -- `--cross-check` is the gate that holds the two artifacts together.

The widening map is computed here independently of `tools/jump-graph.py` (`guest_starts`, ten lines over
`uscript-dis.py`'s spans): the two artifacts are meant to be two measurements of one fact, and a shared helper would
make the comparison a file against itself.

`LabelTable` and `GotoLabel` are counted as zeros rather than dropped: `0x0c`'s handler exists and the corpus never
emits one in a `Function`, which is why a label jump has no name left to read. A `State` payload does carry the
table -- `tools/state-code.py` -- and its operands land the same way (863 of 863).
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "jump_census.csv")
GRAPH_PATH = os.path.join(ROOT, "inventory", "jump_graph.csv")
DOC_MARK = ("<!-- generated:jump-census -->", "<!-- /generated:jump-census -->")
JUMPS = ("Jump", "JumpIfNot")
MISS_CAP = 50
LABELS = ("LabelTable", "GotoLabel")
FIELDS = ["kind", "package", "class", "function", "offset", "op", "target", "absolute", "unsigned",
          "signed", "guest", "key", "count", "note"]


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


def guest_starts(spans):
    """`({guest offset: file offset}, guest length)` for every token start under the widening model.

    The model is the one `--sizefield` measures from a second field: the dword at `name_end + 12` equals the file
    length plus `4 - width` over every compact `index` operand, so the memory image is the file with those operands
    at four bytes and every other span unchanged. Written out here rather than imported from `tools/jump-graph.py`
    so that the two artifacts this tool cross-checks are independent computations.
    """
    starts, at = {}, 0
    for a, b, kind, _op in spans:
        if kind == "opcode":
            starts[at] = a
        at += 4 if kind == "index" else (b - a)
    return starts, at


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
            body = pay[start:end]
            try:
                nodes = DIS.dis_parse(body, table)
                spans = DIS._spans_with_op(body, table)
            except (DIS.Refused, IndexError):
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != len(body):
                continue
            stats["walked"] += 1
            yield package, pkg, exp, body, nodes, spans


def measure(nodes, spans):
    """`(jumps, label tokens, file starts, guest starts, guest length)` for one script.

    A jump operand is two little-endian bytes and the encoding is the question, so every reading a code offset can
    have is tested and reported: **absolute** from the script's first byte, **unsigned relative** from the end of
    the token, **signed relative** from the same base, and **guest** -- the same value read as an offset into the
    widened script. `at_end`/`past_end` are the guest reading's two boundary answers, the same rule
    `tools/jump-graph.py` uses: an operand equal to the widened length is a jump to the epilogue.
    """
    tokens = flat(nodes)
    starts = {n["offset"] for n in tokens}
    gstarts, glen = guest_starts(spans)
    jumps, labels = [], collections.Counter()
    for n in tokens:
        if n["name"] in LABELS:
            labels[n["name"]] += 1
        if n["name"] not in JUMPS:
            continue
        raw = n["operands"] or b""
        if len(raw) < 2:
            jumps.append((n, -1, False, False, False, False, None, ""))
            continue
        value = int.from_bytes(raw[:2], "little")
        signed = int.from_bytes(raw[:2], "little", signed=True)
        # the base is the byte after the operand, and the token's offset counts the opcode byte
        base = n["offset"] + 1 + len(raw)
        at_end = glen <= value <= glen + 2
        target = gstarts.get(value)
        jumps.append((n, value, value in starts, (base + value) in starts, (base + signed) in starts,
                      target is not None, target, "past_end" if value > glen + 2 else ("at_end" if at_end else "")))
    return jumps, labels, starts, gstarts, glen


def read_dump(system):
    table = DIS.Table()
    stats = {"seen": 0, "walked": 0}
    rows = []
    census = collections.Counter()
    for package, pkg, exp, body, nodes, spans in payloads(system, table, stats):
        jumps, labels, starts, gstarts, glen = measure(nodes, spans)
        for kind, n in labels.items():
            census["tokens_" + kind.lower()] += n
        if not jumps:
            continue
        # The base rates are counted over the scripts the hits come from -- a chance level taken over the whole
        # corpus answers for a population that has no jump in it (the first version did, and quoted 45.6%).
        census["bytes"] += len(body)
        census["token_starts"] += len(starts)
        census["guest_bytes"] += glen
        census["guest_token_starts"] += len(gstarts)
        cls, _state = owner_of(pkg, exp)
        func = pkg.name(exp["name"])
        abs_hits = len([j for j in jumps if j[2]])
        unsigned = len([j for j in jumps if j[3]])
        signed = len([j for j in jumps if j[4]])
        guest = len([j for j in jumps if j[5]])
        census["scripts_with_a_jump"] += 1
        census["jumps"] += len(jumps)
        census["absolute_hits"] += abs_hits
        census["unsigned_hits"] += unsigned
        census["signed_hits"] += signed
        census["guest_hits"] += guest
        census["at_end"] += len([j for j in jumps if j[7] == "at_end"])
        census["past_end"] += len([j for j in jumps if j[7] == "past_end"])
        rows.append({"kind": "script", "package": package, "class": cls, "function": func,
                     "offset": "", "op": "", "target": "", "absolute": abs_hits, "unsigned": unsigned,
                     "signed": signed, "guest": guest, "key": "", "count": len(jumps),
                     "note": "%d token start(s) of %d byte(s) in the file, %d of %d widened"
                     % (len(starts), len(body), len(gstarts), glen)})
        for n, value, a_ok, u_ok, s_ok, g_ok, _target, edge in jumps:
            if g_ok or a_ok or u_ok or s_ok:
                continue
            census["misses"] += 1
            if census["misses"] > MISS_CAP:
                continue
            rows.append({"kind": "miss", "package": package, "class": cls, "function": func,
                         "offset": "%04x" % n["offset"], "op": n["name"], "target": value,
                         "absolute": int(a_ok), "unsigned": int(u_ok), "signed": int(s_ok), "guest": int(g_ok),
                         "key": "", "count": "", "note": edge})
    rows.sort(key=lambda r: (r["kind"] != "miss", r["package"], r["class"], r["function"]))
    out = []
    for key in ("jumps", "scripts_with_a_jump", "guest_hits", "absolute_hits", "unsigned_hits", "signed_hits",
                "at_end", "past_end", "token_starts", "bytes", "guest_token_starts", "guest_bytes", "misses",
                "tokens_labeltable", "tokens_gotolabel"):
        out.append({"kind": "census", "key": key, "count": census[key], "note": CENSUS_NOTE.get(key, "")})
    out.append({"kind": "census", "key": "payloads_seen", "count": stats["seen"], "note": ""})
    out.append({"kind": "census", "key": "payloads_walked", "count": stats["walked"], "note": ""})
    return rows + out


CENSUS_NOTE = {
    "jumps": "`Jump`/`JumpIfNot` tokens in the scripts that walk",
    "scripts_with_a_jump": "scripts carrying at least one",
    "guest_hits": "operands that equal a token start's offset in the widened script (the layout the handler uses)",
    "absolute_hits": "operands that land on a file-space token start read as an absolute code offset (control)",
    "unsigned_hits": "read as an unsigned offset from the end of the token (control)",
    "signed_hits": "read as a signed offset from the end of the token (control)",
    "at_end": "operands equal to the widened length -- a jump to the epilogue",
    "past_end": "operands past the widened length -- a dangling edge",
    "token_starts": "file-space token starts over the scripts that carry a jump, the file base rate's numerator",
    "bytes": "file bytes of those scripts, its denominator",
    "guest_token_starts": "the same token starts in the widened layout, the guest base rate's numerator",
    "guest_bytes": "widened bytes of those scripts, its denominator",
    "misses": "jumps whose operand is no token start under any of the four readings",
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


def comma(n):
    return "{:,}".format(int(n))


def doc_block(rows):
    jumps = count(rows, "jumps")
    gs, gb = count(rows, "guest_token_starts"), count(rows, "guest_bytes")
    fs, fb = count(rows, "token_starts"), count(rows, "bytes")
    rates = [(name, count(rows, name + "_hits")) for name in ("absolute", "unsigned", "signed")]
    misses = [r for r in rows if r.get("kind") == "miss"]
    lines = ["",
             "**%s of %s** `Jump`/`JumpIfNot` operands (**%s%%**) equal the **guest-space offset** of a token start"
             " -- an offset into the script as the loader widens it -- over **%s** scripts. The three file-space"
             " readings are the control, on the same sites: **absolute** %s (%s%%), **unsigned relative** %s (%s%%),"
             " **signed relative** %s (%s%%). The chance levels are counted over those same scripts, not the whole"
             " corpus: a token start is **%s%%** of a byte in the widened layout (%s of %s) and **%s%%** in the file"
             " (%s of %s), so the guest reading clears chance by the whole population while every file reading lands"
             " at or below it -- the operand is written in the layout `FFrame::Code = Node->Script + operand`"
             " computes against, and the file is not that layout."
             % (comma(count(rows, "guest_hits")), comma(jumps),
                "%.2f" % pct(count(rows, "guest_hits"), jumps), comma(count(rows, "scripts_with_a_jump")),
                comma(rates[0][1]), "%.2f" % pct(rates[0][1], jumps),
                comma(rates[1][1]), "%.2f" % pct(rates[1][1], jumps),
                comma(rates[2][1]), "%.2f" % pct(rates[2][1], jumps),
                "%.2f" % pct(gs, gb), comma(gs), comma(gb), "%.2f" % pct(fs, fb), comma(fs), comma(fb)),
             "",
             "**%s** operands land on the epilogue's first byte and **%s** run past the script's widened length, so"
             " no edge dangles; **%s** land on no token start under any of the four readings. The corpus carries"
             " **%d** `LabelTable` (`0x0c`) and **%d** `GotoLabel` (`0x0d`) tokens -- in `Function` payloads; a"
             " `State` payload carries the table (`tools/state-code.py`, `docs/systems/AI.md`) -- so a source"
             " `Goto('Label')` keeps no name in the build: the compiler resolved the label to an offset and the"
             " runtime never looks one up."
             % (comma(count(rows, "at_end")), comma(count(rows, "past_end")), comma(count(rows, "misses")),
                count(rows, "tokens_labeltable"), count(rows, "tokens_gotolabel")),
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


def cross_check(census_csv, graph_csv):
    """Hold this artifact to `inventory/jump_graph.csv`: one fact, two walks, and they must agree row for row.

    A census whose `misses` row reached zero would also pass a check that only reads its own file, so the second
    claim is the graph's count of the same population. The base rates are compared too, because they are the two
    walks' token sets and a table change that moved one would show up here first.
    """
    a, b = read_csv(census_csv), read_csv(graph_csv)
    if not a or not b:
        print("FAIL jump-census --cross-check: %s or %s holds no rows"
              % (census_csv, graph_csv))
        return 1
    pairs = [("jumps", "jumps"), ("scripts_with_a_jump", "scripts_with_a_jump"),
             ("guest_hits", "guest_hits"), ("at_end", "at_end"), ("past_end", "past_end"),
             ("misses", "misses"), ("token_starts", "file_token_starts"), ("bytes", "file_bytes"),
             ("guest_token_starts", "guest_token_starts"), ("guest_bytes", "guest_bytes"),
             ("payloads_walked", "payloads_walked")]
    bad = 0
    for mine, theirs in pairs:
        x, y = count(a, mine), count(b, theirs)
        if x != y:
            bad += 1
            print("FAIL jump-census --cross-check: %s is %d here, %d in %s"
                  % (mine, x, y, os.path.basename(graph_csv)))
    if bad:
        return 1
    print("ok   jump-census --cross-check: %d row(s) of census agree with %s"
          % (len(pairs), os.path.basename(graph_csv)))
    return 0


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
    print("ok   jump-census: %s jump(s) over %d script(s); %d guest, %d absolute, %d unsigned, %d signed; "
          "%d miss(es)"
          % (count(rows, "jumps"), count(rows, "scripts_with_a_jump"), count(rows, "guest_hits"),
             count(rows, "absolute_hits"), count(rows, "unsigned_hits"), count(rows, "signed_hits"),
             len([r for r in rows if r.get("kind") == "miss"])))
    return 0


def token(name, operands, offset, children=None):
    return {"name": name, "opcode": 0, "operands": operands, "children": children or [],
            "offset": offset, "end": offset + 1 + len(operands)}


def check():
    """Synthetic scripts: a signed-relative backward jump, an absolute landing, a miss, the label counters, and the
    guest reading on a widened operand.

    The case that matters is the miss and the backward jump: a reader that only tested a forward offset would
    pass every fixture whose jumps go ahead of themselves. The guest case is the one this run added: a script whose
    index operand widens by three bytes, so the guest offset of a later token is four greater than its file offset.
    """
    good, bad = [], []

    def want(got, expect, what):
        (good if got == expect else bad).append("%s: %r != %r" % (what, got, expect))

    # Return@0, Nothing@1, EndOfScript@2, Jump@3 (operand 00 00 -> base 6), Return@6
    body = [token("Return", b"", 0), token("Nothing", b"", 1), token("EndOfScript", b"", 2),
            token("Jump", b"\x00\x00", 3), token("Return", b"", 6)]
    # one file span set with no index operand: the guest layout is the file
    spans = [(n["offset"], n["end"], "opcode", 0) for n in body]
    jumps, labels, starts, gstarts, glen = measure(body, spans)
    want(len(jumps), 1, "one jump")
    want((jumps[0][1], jumps[0][2], jumps[0][3], jumps[0][4], jumps[0][5]), (0, True, True, True, True),
         "a zero operand lands on the script start under every reading")
    want((sorted(starts), glen), ([0, 1, 2, 3, 6], 7), "the token starts and the guest length")

    # a backward jump: Jump@4 with f9 ff = -7 from base 7 -> offset 0
    back = [token("Return", b"", 0), token("Nothing", b"", 1), token("EndOfScript", b"", 2),
            token("Jump", b"\xf9\xff", 4)]
    jumps, _, _st, _gs, _gl = measure(back, [(n["offset"], n["end"], "opcode", 0) for n in back])
    want((jumps[0][2], jumps[0][3], jumps[0][4]), (False, False, True), "a backward jump is signed relative")

    # a forward jump: Jump@1 with 03 00 = 3 from base 4 -> offset 7
    fwd = [token("Return", b"", 0), token("Jump", b"\x03\x00", 1), token("Nothing", b"", 4),
           token("Return", b"", 7)]
    jumps, _, _st, _gs, _gl = measure(fwd, [(n["offset"], n["end"], "opcode", 0) for n in fwd])
    want(jumps[0][4], True, "a forward jump is signed relative")
    want(jumps[0][2], False, "and is not absolute")

    # a widened script: `LocalVariable` byte 0 with a two-byte index (0..2), `Return` at 3, `Jump` at 4 whose
    # operand is the guest offset of that `Return` -- 3 in the file, 3 + (4 - 2) = 5 in the guest.
    wide = [token("LocalVariable", b"\x00\x00", 0), token("Return", b"", 3), token("Jump", b"\x05\x00", 4)]
    wspan = [(0, 1, "opcode", 0), (1, 3, "index", 0), (3, 4, "opcode", 0), (4, 5, "opcode", 0), (5, 7, "fixed", 0)]
    jumps, _, st, gst, gl = measure(wide, wspan)
    want(gl, 9, "the widened length is the file's plus the index's slack")
    want(sorted(gst), [0, 5, 6], "the guest starts are the file starts plus the widening in front of them")
    want(jumps[0][5], True, "an operand in widened space is a guest hit")
    want(jumps[0][2], False, "and is not a file-space absolute hit")
    want(jumps[0][7], "", "and is not at or past the end")

    past = [token("Return", b"", 0), token("Jump", b"\x09\x00", 1), token("Return", b"", 4)]
    jumps, _, _st, _gs, _gl = measure(past, [(n["offset"], n["end"], "opcode", 0) for n in past])
    want((jumps[0][2], jumps[0][5], jumps[0][7]), (False, False, "past_end"),
         "a target past the script is a miss and reads as past the end")

    lab_body = [token("LabelTable", b"", 0), token("GotoLabel", b"", 1)]
    _j, labels, _st, _gs, _gl = measure(lab_body, [(n["offset"], n["end"], "opcode", 0) for n in lab_body])
    want(dict(labels), {"LabelTable": 1, "GotoLabel": 1}, "the label tokens are counted")

    short = [token("Jump", b"\x01", 0)]
    want(measure(short, [(0, 2, "opcode", 0)])[0][0][2], False, "a truncated operand is a miss, not a crash")

    def rows_with(guest_hits, misses, jumps_n):
        out = [{"kind": "census", "key": "jumps", "count": str(jumps_n), "note": ""},
               {"kind": "census", "key": "guest_hits", "count": str(guest_hits), "note": ""},
               {"kind": "census", "key": "scripts_with_a_jump", "count": "1", "note": ""},
               {"kind": "census", "key": "at_end", "count": "0", "note": ""},
               {"kind": "census", "key": "past_end", "count": "0", "note": ""},
               {"kind": "census", "key": "misses", "count": str(misses), "note": ""},
               {"kind": "census", "key": "token_starts", "count": "3", "note": ""},
               {"kind": "census", "key": "bytes", "count": "8", "note": ""},
               {"kind": "census", "key": "guest_token_starts", "count": "3", "note": ""},
               {"kind": "census", "key": "guest_bytes", "count": "10", "note": ""}]
        if misses:
            out.append({"kind": "miss", "package": "P", "class": "C", "function": "F", "offset": "0001",
                        "op": "Jump", "target": "9", "absolute": "0", "unsigned": "0", "signed": "0",
                        "guest": "0", "count": "", "note": "past_end"})
        return out

    want(render(rows_with(2, 0, 2)).splitlines()[0].split(",")[0], "kind", "the CSV header")
    block = doc_block(rows_with(2, 0, 2))
    want("**2 of 2**" in block, True, "the block leads with the guest reading")
    want("land on no token start under any of the four readings" in block, True, "and names the residue rule")
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
    ap.add_argument("--cross-check", action="store_true",
                    help="hold inventory/jump_census.csv to inventory/jump_graph.csv (no dump)")
    ap.add_argument("--from-csv", metavar="PATH", default=CSV_PATH,
                    help="with --check-doc and no --system: read this artifact instead")
    ap.add_argument("--graph-csv", metavar="PATH", default=GRAPH_PATH,
                    help="with --cross-check: the graph artifact to compare against")
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
    if args.cross_check:
        return cross_check(args.from_csv, args.graph_csv)
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
