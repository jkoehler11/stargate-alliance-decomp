#!/usr/bin/env python3
"""The state machine's native half: what `GotoState`, `Goto`, `IsInState` and `GetStateName` do to a state.

`docs/systems/AI.md` measures the state machine from the *outside* -- the text's `state` blocks, the payloads'
`GotoState`/`GotoLabel` operands and the `LabelTable`s they resolve against.  This tool reads the *inside*: the four
script natives, the state setter and the label lookup that consume those tables, out of `default.xbe`, and records
one row per claim with the instruction that carries it.  Nothing here publishes script or a decompiled body: a row
names a function, a field offset and a log line.

    python3 tools/state-natives.py --xbe DUMP/default.xbe -o inventory/state_natives.csv \\
        --write-doc docs/systems/STATE_NATIVES.md
    python3 tools/state-natives.py --check-doc docs/systems/STATE_NATIVES.md   # no dump, no write
    python3 tools/state-natives.py --check                                    # no dump, no write

The `--verify` pass re-reads every claim out of the image; `--check` is the committed-CSV half, which is what the
per-commit gate can answer without the dump.  A claim is a `(function, claim)` pair with the instruction address it
was read at, so a wrong offset fails on the bytes rather than in prose.
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "state_natives.csv")
DOC_PATH = os.path.join(ROOT, "docs", "systems", "STATE_NATIVES.md")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
MARK_BEGIN = "<!-- generated:state-natives -->"
MARK_END = "<!-- /generated:state-natives -->"

FIELDS = ["function", "slot", "address", "size", "claim", "value", "evidence"]

# The six bodies.  `slot` is the id the engine reaches the body through: a native index for the four script
# functions, a dispatch index for `GotoLabel`, and the class table's slot for the two helpers -- every one of the
# 492 class tables carries the same pair at slots 13 and 14 (`inventory/xbe_class_table_slots.csv`).
FUNCS = [
    {"name": "execGotoState", "va": 0x486F0, "slot": "native 113"},
    {"name": "execIsInState", "va": 0x46830, "slot": "native 281"},
    {"name": "execGetStateName", "va": 0x468C0, "slot": "native 284"},
    {"name": "execGotoLabel", "va": 0x47850, "slot": "gnatives[13] (0x0d)"},
    {"name": "state_set", "va": 0x48E90, "slot": "class table 13 (+0x34)"},
    {"name": "label_lookup", "va": 0x46F00, "slot": "class table 14 (+0x38)"},
]

# The strings each body logs, by the VA of the literal.  A row is emitted only when the body pushes that VA, so a
# body that stops logging a line drops its row instead of keeping a stale claim.
STRINGS = {
    0x4D9D50: "GotoState (%s %s): Label not found",
    0x4D9CF0: "GotoState (%s %s): State not found in class %s!",
    0x4D9C08: "GotoState (%s %s): State not found in class %s!\nRefer to Log for Script Stack Trace.",
    0x4D9CB4: "Stack when GotoState failed.",
    0x4D9AC8: "GotoLabel (%s): Label not found",
    0x4D9D98: "Enable: '%s' is not a probe function",
}


def die(msg):
    sys.exit("state-natives: " + msg)


def symbol_sizes():
    """`{VA: size}` from the committed symbol map -- the body bounds, never a `0xcc` guess."""
    out = {}
    if not os.path.exists(SYMBOLS):
        return out
    with open(SYMBOLS, encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 3:
                continue
            try:
                out[int(row[0], 16)] = int(row[2])
            except ValueError:
                continue
    return out


class Image:
    """The dump's `default.xbe`, plus a disassembler for one window."""

    def __init__(self, path):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import xbe as X

        self.x = X.Xbe(path)
        self.sizes = symbol_sizes()

    def dis(self, va, size):
        blob = self.x.read_va(va, size)
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(blob)
            path = fh.name
        try:
            out = subprocess.run(
                ["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                 "--adjust-vma", hex(va), path],
                capture_output=True, text=True, check=True).stdout
        finally:
            os.unlink(path)
        rows = []
        for line in out.splitlines():
            m = re.match(r"^\s+([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*(\S+)\s*(.*)$", line)
            if not m:
                continue
            ops = " ".join(m.group(4).split())
            rows.append({"va": int(m.group(1), 16), "mnem": m.group(3), "ops": ops,
                         "full": (m.group(3) + " " + ops).strip(),
                         "text": " ".join(line.split()[1:])})
        return rows

    def u16(self, va):
        import struct
        return struct.unpack("<H", self.x.read_va(va, 2))[0]

    def utf16(self, va, limit=160):
        off = self.x.va_to_offset(va)
        raw = self.x.data[off:off + limit]
        cut = raw.find(b"\x00\x00\x00")
        if cut > 0:
            raw = raw[:cut + 2]
        try:
            return raw.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            return None


def decode(img, spec):
    """One body's claims, each tied to the instruction it was read at."""
    va = spec["va"]
    size = img.sizes.get(va)
    if not size:
        die("%s: no size in %s" % (hex(va), SYMBOLS))
    ins = img.dis(va, size)
    claims = []

    def add(claim, value, at, text):
        claims.append({"claim": claim, "value": str(value).replace("\n", "\\n"),
                       "evidence": "0x%06x %s" % (at, text)})

    add("body_size", size, va, "symbol map")

    # Argument evaluation: the interpreter's `gnatives[opcode]` dispatch, once per script expression.  A dispatch
    # that is handed a local's address (`lea reg,[esp+N]` then `push reg`) evaluates an expression; one handed a
    # literal zero is the DebugInfo skip, which every call site may carry too.
    evals = []
    for idx, i in enumerate(ins):
        if not re.search(r"call\s+DWORD PTR \[e\w+\*4\+0x6313e8\]", i["full"]):
            continue
        window = ins[max(0, idx - 10):idx]
        pushed = {m.group(1) for j in window
                  for m in [re.search(r"^push\s+(e\w+)$", j["full"])] if m}
        local = {m.group(1) for j in window
                 for m in [re.search(r"^lea\s+(e\w+),\[esp", j["full"])] if m}
        if pushed & local:
            evals.append(i)
    add("arg_expressions", len(evals), evals[0]["va"] if evals else va,
        evals[0]["text"] if evals else "no expression evaluated")

    # A call's operand stream may end on a DebugInfo token, which the native itself consumes.
    deb = [i for i in ins if re.search(r"cmp\s+BYTE PTR \[e\w+\],0x42", i["full"])]
    skip = [i for i in ins if re.search(r"call\s+DWORD PTR ds:0x6314f0", i["full"])]
    if deb and skip:
        add("debuginfo_skip", "0x42", deb[0]["va"], deb[0]["text"])

    # Virtual calls: the class table's slots, which is how the two helpers are reached.
    for i in ins:
        m = re.search(r"call\s+DWORD PTR \[e\w+\+0x([0-9a-f]+)\]", i["full"])
        if m:
            add("vtable_call", "0x" + m.group(1), i["va"], i["text"])

    # Constants compared against, and the immediates that stand for names.
    for i in ins:
        m = re.search(r"^cmp\s+e\w+,0x([0-9a-f]+)$", i["full"])
        if m:
            add("constant", "0x" + m.group(1), i["va"], i["text"])

    # Field offsets touched: `[reg+0xNN]` on a structure pointer, which is how the record's shape is read.
    seen = set()
    for i in ins:
        if i["mnem"] == "call":
            continue
        for m in re.finditer(r"\[(e(?!sp)\w+)\+0x([0-9a-f]+)\]", i["full"]):
            off = "0x" + m.group(2)
            if off in seen or off in ("0x6313e8",):
                continue
            seen.add(off)
            add("field", off, i["va"], i["text"])

    # Log sites: the wrapper takes (frame, source line, format, args), and the line is an immediate.
    logs = [i for i in ins if re.search(r"call\s+0x57440$", i["full"])]
    for i in logs:
        line = None
        for back in reversed(ins):
            if back["va"] >= i["va"]:
                continue
            m = re.search(r"^push\s+0x([0-9a-f]+)$", back["full"])
            if not m:
                continue
            val = int(m.group(1), 16)
            if line is None and 0x100 < val < 0x10000 and back["va"] > i["va"] - 40:
                line = val
                break
        if line:
            add("log_line", line, i["va"], "debugf at line %d" % line)

    for i in ins:
        m = re.search(r"^push\s+0x([0-9a-f]+)$", i["full"])
        if not m:
            continue
        target = int(m.group(1), 16)
        if target not in STRINGS:
            continue
        add("log_text", STRINGS[target], i["va"], i["text"])

    # The default label a `GotoState` with no label jumps to: an FName index fixed in the engine.
    for i in ins:
        if i["full"] == "mov eax,0x64":
            add("default_label", 100, i["va"], i["text"])

    # The label table's entry stride, which is what makes the record an array of `(name, offset)` pairs.
    if spec["name"] == "label_lookup":
        for i in ins:
            m = re.search(r"^add\s+e\w+,0x([0-9a-f]+)$", i["full"])
            if m:
                add("entry_stride", int(m.group(1), 16), i["va"], i["text"])
                break

    # The built-in events the state change fires around the switch, by FName index.
    for i in ins:
        m = re.search(r"^mov\s+DWORD PTR \[e\w+\],0x(13[cd])$", i["full"])
        if m:
            add("event_id", int(m.group(1), 16), i["va"], i["text"])

    return claims, ins


def read(image_path):
    img = Image(image_path)
    rows = []
    for spec in FUNCS:
        claims, ins = decode(img, spec)
        for c in claims:
            rows.append({"function": spec["name"], "slot": spec["slot"], "address": "0x%06x" % spec["va"],
                         "size": img.sizes.get(spec["va"], ""), **c})
    return rows


def census(rows):
    """The counts the page quotes, derived from the rows rather than typed into it."""
    out = {spec["name"]: {"arg_expressions": 0, "debuginfo_skip": 0, "constants": set(),
                          "fields": set(), "vtable": [], "log_lines": [], "log_text": [], "default_label": ""}
           for spec in FUNCS}
    for r in rows:
        d = out.setdefault(r["function"], {"arg_expressions": 0, "debuginfo_skip": 0, "constants": set(),
                                           "fields": set(), "vtable": [], "log_lines": [], "log_text": [],
                                           "default_label": ""})
        if r["claim"] == "arg_expressions":
            d["arg_expressions"] = int(r["value"])
        elif r["claim"] == "debuginfo_skip":
            d["debuginfo_skip"] = 1
        elif r["claim"] == "constant":
            d["constants"].add(r["value"])
        elif r["claim"] == "field":
            d["fields"].add(r["value"])
        elif r["claim"] == "vtable_call":
            if r["value"] not in d["vtable"]:
                d["vtable"].append(r["value"])
        elif r["claim"] == "log_line":
            d["log_lines"].append(int(r["value"]))
        elif r["claim"] == "log_text":
            d["log_text"].append(r["value"])
        elif r["claim"] == "default_label":
            d["default_label"] = r["value"]
    return out


NOTES = {
    "execGotoState": "two name expressions, the label defaulting to FName 100; the state change is class table 13 "
                     "and the label jump class table 14, and only an explicit label that misses is logged",
    "execGotoLabel": "one name expression, the same class table 14 the state entry uses -- `Goto('L')` and "
                     "`GotoState('S','L')` share one lookup",
    "execIsInState": "one name expression; the walk follows the state chain, so a state the current one extends "
                     "answers too",
    "execGetStateName": "no expression at all: the call takes no argument and only consumes a trailing DebugInfo",
    "state_set": "resolves the name, fires the end/begin events around the switch, then clears the code pointer and "
                 "recomputes the frame's probe masks",
    "label_lookup": "walks the state chain's `LabelTable`s -- 8-byte entries, a zero name ending the table, the "
                    "offset relative to the state's own code -- and on a miss clears the frame's code pointer",
}


def render(rows):
    c = census(rows)
    out = []
    out.append("| body | reached through | args evaluated | debug info | class-table slots | log lines |")
    out.append("| --- | --- | ---: | :---: | --- | --- |")
    for spec in FUNCS:
        d = c[spec["name"]]
        slots = " ".join(d["vtable"]) or "-"
        lines = ", ".join(str(x) for x in sorted(set(d["log_lines"]))) or "-"
        out.append("| `%s` | %s | %d | %s | %s | %s |" %
                   (spec["name"], spec["slot"], d["arg_expressions"], "eats `0x42`" if d["debuginfo_skip"] else "-",
                    slots, lines))
    out.append("")
    for spec in FUNCS:
        d = c[spec["name"]]
        fields = " ".join(sorted(d["fields"], key=lambda s: int(s, 16)))
        out.append("* `%s` -- %s. Fields touched: %s. Constants compared: %s." %
                   (spec["name"], NOTES[spec["name"]], fields,
                    " ".join(sorted(d["constants"], key=lambda s: int(s, 16))) or "-"))
    out.append("")
    out.append("Log text, verbatim:")
    out.append("")
    for spec in FUNCS:
        for text in census(rows)[spec["name"]]["log_text"]:
            out.append("* `%s`: `%s`" % (spec["name"], text.replace("\n", "\\n")))
    return "\n".join(out)


def write_doc(path, rows):
    block = render(rows)
    if not os.path.exists(path):
        die("%s does not exist" % path)
    text = open(path, encoding="utf-8").read()
    if MARK_BEGIN not in text or MARK_END not in text:
        die("%s has no generated:state-natives block" % path)
    head, rest = text.split(MARK_BEGIN, 1)
    _old, tail = rest.split(MARK_END, 1)
    open(path, "w", encoding="utf-8").write(head + MARK_BEGIN + "\n" + block + "\n" + MARK_END + tail)


def check_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    if MARK_BEGIN not in text or MARK_END not in text:
        die("%s has no generated:state-natives block" % path)
    have = text.split(MARK_BEGIN, 1)[1].split(MARK_END, 1)[0].strip("\n")
    want = render(rows)
    if have.strip("\n") != want:
        h = have.splitlines()
        w = want.splitlines()
        diff = next((i for i in range(min(len(h), len(w))) if h[i] != w[i]), min(len(h), len(w)))
        die("%s: the state-natives block differs at line %d -- `%s` on disk, `%s` from the CSV" %
            (path, diff + 1, h[diff] if diff < len(h) else "<end>", w[diff] if diff < len(w) else "<end>"))


def read_csv(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if not rows:
        die("%s holds no rows" % path)
    return rows


def render_csv(rows):
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in FIELDS})
    return buf.getvalue()


def check(rows):
    """The dump-free half: the CSV's shape and the claims every body must carry."""
    if list(rows[0].keys()) != FIELDS:
        die("the CSV header is %r, not %r" % (list(rows[0].keys()), FIELDS))
    by = census(rows)
    for spec in FUNCS:
        if spec["name"] not in by:
            die("%s has no rows" % spec["name"])
        d = by[spec["name"]]
        if not d["fields"]:
            die("%s carries no field rows" % spec["name"])
        for r in rows:
            if r["function"] == spec["name"] and not r["evidence"].startswith("0x"):
                die("%s/%s has no instruction address" % (spec["name"], r["claim"]))
    # The two claims the page quotes by name: the label default and the shared lookup.
    if by["execGotoState"]["default_label"] != "100":
        die("execGotoState's default label is %r, not 100" % by["execGotoState"]["default_label"])
    if "0x38" not in by["execGotoState"]["vtable"] or "0x38" not in by["execGotoLabel"]["vtable"]:
        die("the label lookup is not class table +0x38 in both bodies")
    return 0


def selftest():
    """Synthetic rows: the renderer, the doc block and the check must agree."""
    rows = [
        {"function": "execGotoState", "slot": "native 113", "address": "0x0486f0", "size": "518",
         "claim": "arg_expressions", "value": "2", "evidence": "0x048736 call DWORD PTR [edx*4+0x6313e8]"},
        {"function": "execGotoState", "slot": "native 113", "address": "0x0486f0", "size": "518",
         "claim": "field", "value": "0x24", "evidence": "0x048702 cmp eax,DWORD PTR [edi+0x24]"},
        {"function": "execGotoState", "slot": "native 113", "address": "0x0486f0", "size": "518",
         "claim": "vtable_call", "value": "0x38", "evidence": "0x0487b9 call DWORD PTR [edx+0x38]"},
        {"function": "execGotoState", "slot": "native 113", "address": "0x0486f0", "size": "518",
         "claim": "default_label", "value": "100", "evidence": "0x0487af mov eax,0x64"},
    ]
    body = render(rows)
    assert body.splitlines()[0].startswith("| body |"), "the first line is a table header"
    assert "`execGotoState`" in body and "0x38" in body, "the renderer names the body and its slot"
    text = "intro\n" + MARK_BEGIN + "\nstale\n" + MARK_END + "\ntail\n"
    head, rest = text.split(MARK_BEGIN, 1)
    _old, tail = rest.split(MARK_END, 1)
    fresh = head + MARK_BEGIN + "\n" + body + "\n" + MARK_END + tail
    assert fresh.startswith("intro\n") and fresh.endswith("tail\n"), "the doc block keeps its surroundings"
    assert render_csv(rows).splitlines()[0] == ",".join(FIELDS), "the CSV header is the field list"
    print("state-natives: selftest ok (renderer, doc block, CSV header)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xbe", metavar="PATH", help="the dump's default.xbe (the full read)")
    ap.add_argument("-o", "--out", metavar="PATH", default=CSV_PATH, help="the CSV to write")
    ap.add_argument("--write-doc", metavar="PATH")
    ap.add_argument("--check-doc", metavar="PATH")
    ap.add_argument("--check", action="store_true", help="validate the committed CSV without the dump")
    ap.add_argument("--verify", action="store_true", help="re-read every claim from the image and diff")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return 0
    if args.check_doc:
        rows = read_csv(args.out if os.path.exists(args.out) else CSV_PATH)
        check_doc(args.check_doc, rows)
        print("ok   state-natives: %s matches %s" % (args.check_doc, os.path.relpath(CSV_PATH, ROOT)))
        return 0
    if args.check:
        check(read_csv(args.out if os.path.exists(args.out) else CSV_PATH))
        print("ok   state-natives: %d claims over %d bodies" % (len(read_csv(CSV_PATH)), len(FUNCS)))
        return 0
    if args.verify:
        rows = read(args.xbe or die("--verify needs --xbe"))
        have = read_csv(CSV_PATH)
        if render_csv(rows) != render_csv(have):
            for a, b in zip(rows, have):
                if render_csv([a]) != render_csv([b]):
                    die("claim differs: %r on disk, %r from the image" % (b, a))
            die("row count differs: %d on disk, %d from the image" % (len(have), len(rows)))
        print("ok   state-natives: %d claims re-read from the image" % len(rows))
        return 0
    if not args.xbe:
        ap.error("one of --xbe, --check, --check-doc, --verify or --selftest is required")

    rows = read(args.xbe)
    check(rows)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write(render_csv(rows))
    print("wrote %s (%d claims over %d bodies)" % (os.path.relpath(args.out, ROOT), len(rows), len(FUNCS)))
    if args.write_doc:
        write_doc(args.write_doc, rows)
        print("wrote %s" % os.path.relpath(args.write_doc, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
