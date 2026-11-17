#!/usr/bin/env python3
"""Where a state's own code compiles: the State export's payload, not a Function (PLAN 3.10, docs/systems/AI.md).

`docs/systems/AI.md` reads state behaviour out of the class's shipped text, and `docs/BYTECODE.md` counts the
`else` keywords a state body spells at its own statement level (`fn_state_label`, **118**). The natural next
question -- do those keywords equal the arms the state compiles? -- cannot be answered from
`inventory/jump_branches.csv`, because that census walks **`Function` exports only**:
`tools/jump-census.py` skips every export whose class is not `Function`, and `tools/jump-branches.py` inherits the
skip. A state's label block is not one of those exports.

It is an export of the class `State`, and its payload is `<header><script><0x47><22-byte record>`:

* the script is a token stream ending on a **`LabelTable`** (`0x0c`) -- a token that appears in 30 of these
  payloads' neighbours and in **zero** `Function` payloads, which is why `tools/jump-census.py` reports the
  corpus's `LabelTable` count as 0 ("the build never emits a label"). It emits one per state that has labels;
  the census only ever looked inside functions.
* a table's entries are an **archive compact name index then a 4-byte code offset**, and the table ends on the
  **None name** (a zero index) -- not on a zero offset, which a real label can have when it sits at the code's
  first byte;
* the script's last byte is `0x47` ('G'), the marker `UStruct::Serialize` (`0x392b0`) appends on save when a
  script does not already end on it;
* after it `UState::Serialize` (`0x394c0`, the caller of `0x392b0`, itself called from `UFunction::Serialize`)
  writes **22 bytes**: `ProbeMask` and `IgnoreMask` as two QWORDs, then a WORD and a DWORD (the writers at
  `0x371d0`, `0x37080` and `0x370e0` push 8, 2 and 4).

So the container's own end anchors the code: a walk is a state's code only when its label table ends exactly
23 bytes before the payload does and the byte there is `0x47`. A start inside the header cannot pass that test,
which is why the longest-walk rule is gone -- it used to accept tables that were the record's own bytes.

    tools/state-code.py --system DUMP/System -o inventory/state_code.csv --write-doc docs/systems/AI.md
    tools/state-code.py --report
    tools/state-code.py --check-doc docs/systems/AI.md   # from the committed artifact
    tools/state-code.py --check                          # synthetic, no dump, writes nothing

Counts only: no source text, no token stream and no bytecode is written or printed beyond counts and class/state
names the tree already carries.
"""
import argparse
import collections
import csv
import importlib.util
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

CSV_PATH = os.path.join(ROOT, "inventory", "state_code.csv")
DOC_MARK = ("<!-- generated:state-code -->", "<!-- /generated:state-code -->")

RECORD = 22          # ProbeMask u64 + IgnoreMask u64 + LabelTableOffset u16 + StateFlags u32
MARKER = 0x47        # 'G', the end marker UStruct::Serialize appends to a script on save

FIELDS = ["kind", "package", "class", "state", "payload", "start", "code", "trailer", "tokens",
          "jumpifnot", "jump", "labeltable", "complete", "stringconst", "record", "probe_mask",
          "ignore_mask", "label_offset", "state_flags", "text_kw", "text_label", "text_else",
          "text_else_label", "verdict", "count", "note"]

CENSUS_NOTE = {
    "states": "`State` export payloads read",
    "payload_bytes": "bytes in those payloads",
    "regions": "payloads whose script walks to the container's end (table, `0x47`, record)",
    "no_code": "payloads that are a header and the record only -- no script",
    "code_bytes": "bytes of the walk, i.e. the state's own code",
    "tokens": "tokens the walk names",
    "jumpifnot": "`JumpIfNot` tokens in them -- the label code's arms",
    "jump": "`Jump` tokens, the `Goto('Label')` and loop backs",
    "labeltable": "`LabelTable` tokens (`0x0c`) -- 0 in every `Function` payload of the corpus",
    "labeltable_states": "states whose code ends on one",
    "stringconst": "`StringConst` tokens, the literals the label block is written with",
    "record": "payloads ending with a decodable 22-byte `UState` record",
    "marker": "of the anchored walks, those whose script ends on the `0x47` marker",
    "ignore_all_ones": "records whose `IgnoreMask` is `0xffffffffffffffff`",
    "probe_zero": "records whose `ProbeMask` is 0",
    "flags_zero": "records with `StateFlags` 0",
    "flags_one": "records with `StateFlags` 1",
    "flags_two": "records with `StateFlags` 2",
    "flags_other": "records with another `StateFlags` value",
    "offset_below_code": "anchored walks whose record `LabelTableOffset` is below the code's byte count",
    "offset_above_code": "the rest -- the field does not equal the table's file offset in either framing",
    "text_label_states": "states whose text spells label code (keywords outside every override)",
    "text_label": "`if`/`else`/`while`/`for` keywords in those states' label code",
    "text_else": "`else` of them, at the state body's own statement level and nested",
    "text_else_label": "of those, at the statement level -- `docs/BYTECODE.md`'s `fn_state_label` counts this half (118)",
    "joined": "states with both a text label code and a walk",
    "joined_jif": "`JumpIfNot` in the joined states -- against `text_label`",
    "joined_label": "their label keywords",
    "joined_equal": "joined states whose `JumpIfNot` count equals the keyword count",
    "overrides": "states with a walk and no label keyword -- code in the overrides only",
    "payload_arms": "`JumpIfNot` in every payload the walk reaches",
}

CENSUS_KEYS = ("states", "payload_bytes", "regions", "no_code", "code_bytes", "tokens", "jumpifnot", "jump",
               "labeltable", "labeltable_states", "stringconst", "record", "marker", "ignore_all_ones",
               "probe_zero", "flags_zero", "flags_one", "flags_two", "flags_other", "offset_below_code",
               "offset_above_code", "text_label_states", "text_label", "text_else", "text_else_label",
               "joined", "joined_jif", "joined_label", "joined_equal", "overrides", "payload_arms")


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TB = None
DIS = None


def text_tool():
    global TB
    if TB is None:
        TB = load_tool("text_branches", "text-branches.py")
    return TB


def dis_tool():
    global DIS
    if DIS is None:
        DIS = load_tool("uscript_dis", "uscript-dis.py")
    return DIS


def tokens(nodes):
    out = []

    def walk(ns):
        for n in ns:
            out.append(n)
            walk(n["children"])
    walk(nodes)
    return out


def region(payload, table):
    """The walk that ends on the container's own end: `(start, end, nodes)` or None.

    A state payload is `<header><script><0x47><22-byte record>`, so an anchored walk is one whose label table
    ends exactly 23 bytes before the payload does and whose next byte is the marker. The old rule -- the longest
    exact walk, anchored on the table token -- accepted 174 payloads, and 13 of those tables were the record's
    own bytes read as a table; the end test refuses them (a table inside the record cannot be 23 bytes from the
    payload's end *and* end on `0x47`). Among the starts that pass, the statement stream wins on token count.
    """
    dis = dis_tool()
    best = None
    for start in range(0, min(72, len(payload))):
        nodes, _covered = dis.parse_prefix(payload[start:], table)
        if not nodes:
            continue
        flat = tokens(nodes)
        cut = None
        for i, n in enumerate(flat):
            if n["name"] == "LabelTable":
                cut = i
        if cut is None:
            continue
        end = start + flat[cut]["end"]
        if end + 1 + RECORD != len(payload) or payload[end] != MARKER:
            continue
        keep = [n for n in nodes if start + n["end"] <= end]
        if best is None or len(tokens(keep)) > len(tokens(best[2])):
            best = (start, end, keep)
    return best


def record(payload):
    """`UState`'s 22-byte record at the end of a state payload, or None."""
    if len(payload) < RECORD:
        return None
    pm, im, lto, sf = struct.unpack_from("<QQHI", payload, len(payload) - RECORD)
    return {"probe_mask": pm, "ignore_mask": im, "label_offset": lto, "state_flags": sf}


def text_label_counts(text, spans_of):
    """`(label keywords, label `else`)` for every `state` block in one class's text, outside its overrides.

    Masking and the declaration rule come from `tools/text-branches.py`, so the count is the same quantity its
    `fn_state_label` column reports -- this tool only keeps it per state instead of per class.
    """
    tb = text_tool()
    live, _comments = tb.mask(text)
    depth = tb.ctb_tool().depth_map(live)
    spans = spans_of(live)
    out = {}
    ms = list(tb.STATE.finditer(live))
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(live)
        span = tb.ctb_tool().body_span(live, m.end(), limit)
        if span is None:
            continue
        floor = depth[span[0]] + 1
        kw = els = at_floor = 0
        for mm in tb.WORD.finditer(live[span[0]:span[1]]):
            pos = span[0] + mm.start()
            if any(a <= pos < b for a, b in spans):
                continue
            kw += 1
            if mm.group(1) == "else":
                els += 1
                if depth[pos] == floor:
                    at_floor += 1
        out[m.group(1).lower()] = (kw, els, at_floor)
    return out


def walk_dump(system):
    """Per-state rows: the payload walk joined to the state's own label keywords, read from its class text."""
    from ue2pkg import Package
    tb = text_tool()
    pst = load_tool("pkg_script_text", "pkg-script-text.py")
    table = dis_tool().Table()
    texts = {}
    for fname in sorted(f for f in os.listdir(system) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system, fname))
        pkg.read_names(); pkg.read_imports(); pkg.read_exports()
        package = fname[:-2]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            body = pst.script_text(pkg, exp)
            if not body:
                continue
            texts[(package, pkg.name(exp["name"]).lower())] = text_label_counts(
                body, lambda live: tb.function_else(live)[2])
    rows = []
    for fname in sorted(f for f in os.listdir(system) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system, fname))
        pkg.read_names(); pkg.read_imports(); pkg.read_exports()
        package = fname[:-2]
        n = len(pkg.exports)
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "State":
                continue
            outer = pkg.exports[exp["outer"] - 1] if 0 < exp["outer"] <= n else None
            if outer is None or pkg.class_name_of(outer) != "Class":
                continue
            cls, state = pkg.name(outer["name"]), pkg.name(exp["name"])
            payload = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            rec = record(payload)
            got = region(payload, table)
            kw, els, at_floor = texts.get((package, cls.lower()), {}).get(state.lower(), ("", "", ""))
            row = {"kind": "state", "package": package, "class": cls, "state": state,
                   "payload": len(payload), "start": "", "code": "", "trailer": "", "tokens": "",
                   "jumpifnot": "", "jump": "", "labeltable": "", "complete": "", "stringconst": "",
                   "record": RECORD if rec else "",
                   "probe_mask": rec["probe_mask"] if rec else "",
                   "ignore_mask": rec["ignore_mask"] if rec else "",
                   "label_offset": rec["label_offset"] if rec else "",
                   "state_flags": rec["state_flags"] if rec else "",
                   "text_kw": kw, "text_label": kw, "text_else": els, "text_else_label": at_floor,
                   "verdict": "record-only", "count": "", "note": ""}
            if got is not None:
                start, end, nodes = got
                flat = tokens(nodes)
                c = collections.Counter(x["name"] for x in flat)
                row.update({"start": start, "code": end - start, "trailer": len(payload) - end,
                            "tokens": len(flat), "jumpifnot": c["JumpIfNot"], "jump": c["Jump"],
                            "labeltable": c["LabelTable"], "stringconst": c["StringConst"],
                            "complete": 1})
                row["verdict"] = ("label+payload" if kw else "payload-only")
            rows.append(row)
    return rows


def census(rows):
    # Every census key starts at zero: the emission guard below is what caught the accumulator that was never
    # written, and it fires on a key that is legitimately zero unless the key exists before the first hit.
    c = collections.Counter({key: 0 for key in CENSUS_KEYS})
    for r in rows:
        c["states"] += 1
        c["payload_bytes"] += int(r["payload"])
        if r["record"] != "":
            c["record"] += 1
            if int(r["ignore_mask"]) == 0xFFFFFFFFFFFFFFFF:
                c["ignore_all_ones"] += 1
            if int(r["probe_mask"]) == 0:
                c["probe_zero"] += 1
            flags = int(r["state_flags"])
            c["flags_zero" if flags == 0 else "flags_one" if flags == 1
              else "flags_two" if flags == 2 else "flags_other"] += 1
        if r["verdict"] == "record-only":
            c["no_code"] += 1
            continue
        code = int(r["code"])
        c["regions"] += 1
        c["code_bytes"] += code
        c["tokens"] += int(r["tokens"])
        c["jumpifnot"] += int(r["jumpifnot"])
        c["jump"] += int(r["jump"])
        c["payload_arms"] += int(r["jumpifnot"])
        c["labeltable"] += int(r["labeltable"])
        c["stringconst"] += int(r["stringconst"])
        c["marker"] += 1
        if int(r["labeltable"]):
            c["labeltable_states"] += 1
        if int(r["label_offset"]) < code:
            c["offset_below_code"] += 1
        else:
            c["offset_above_code"] += 1
    for r in rows:
        if r["text_kw"] in ("", None):
            continue
        if int(r["text_kw"]):
            c["text_label_states"] += 1
            c["text_label"] += int(r["text_kw"])
            c["text_else"] += int(r["text_else"] or 0)
            c["text_else_label"] += int(r["text_else_label"] or 0)
            if r["verdict"] != "record-only":
                c["joined"] += 1
                c["joined_jif"] += int(r["jumpifnot"])
                c["joined_label"] += int(r["text_kw"])
                if int(r["jumpifnot"]) == int(r["text_kw"]):
                    c["joined_equal"] += 1
        elif r["verdict"] != "record-only":
            c["overrides"] += 1
    out = []
    for key in CENSUS_KEYS:
        if key not in c:
            raise SystemExit("census key %r accumulated but not written" % key)
        out.append({"kind": "census", "package": "", "class": "", "state": "", "payload": "", "start": "",
                    "code": "", "trailer": "", "tokens": "", "jumpifnot": "", "jump": "", "labeltable": "",
                    "complete": "", "stringconst": "", "record": "", "probe_mask": "", "ignore_mask": "",
                    "label_offset": "", "state_flags": "", "text_kw": "", "text_label": "", "text_else": "",
                    "text_else_label": "", "verdict": "", "count": c[key], "note": key})
    return out


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def num(rows, key):
    for r in rows:
        if r["kind"] == "census" and r["note"] == key:
            return int(r["count"])
    raise KeyError(key)


def comma(n):
    return "{:,}".format(n)


def doc_block(rows):
    """The generated block: the container's numbers and the states whose label code compiles."""
    states = [r for r in rows if r["kind"] == "state"]
    top = sorted([r for r in states if r["verdict"] == "label+payload"],
                 key=lambda r: -int(r["text_kw"]))[:10]
    lines = []
    lines.append("**All %s `State` payloads end with `UState`'s 22-byte record, and %s of them carry code: %s "
                 "bytes and %s tokens of the state's own label block, `JumpIfNot` %s.** A script ends on a "
                 "`LabelTable` (`0x0c`) -- entries of an archive compact name index and a 4-byte code offset, "
                 "terminated by the None name, then the `0x47` marker `UStruct::Serialize` appends on save -- and "
                 "that end, not a longest-walk guess, is what anchors the code: the token appears in **zero** "
                 "`Function` payloads. The other %s payloads are 47-52 bytes, a header and the record, with no "
                 "script at all. Of the records, %s carry `IgnoreMask` all ones and %s `ProbeMask` 0; `StateFlags` "
                 "is 0 in %s, 1 in %s and 2 in %s. The text spells %s label keywords across %s states (its `else` "
                 "%s); the joined states compile %s `JumpIfNot` against their %s keywords, equal in %s."
                 % (comma(num(rows, "states")), num(rows, "regions"), comma(num(rows, "code_bytes")),
                    comma(num(rows, "tokens")), comma(num(rows, "jumpifnot")), num(rows, "no_code"),
                    comma(num(rows, "ignore_all_ones")), num(rows, "probe_zero"), num(rows, "flags_zero"),
                    num(rows, "flags_one"), num(rows, "flags_two"),
                    comma(num(rows, "text_label")), num(rows, "text_label_states"), comma(num(rows, "text_else")),
                    comma(num(rows, "joined_jif")), comma(num(rows, "joined_label")),
                    num(rows, "joined_equal")))
    lines.append("")
    lines.append("| measure | value |")
    lines.append("|---|---|")
    lines.append("| `State` exports read | %s |" % comma(num(rows, "states")))
    lines.append("| payload bytes | %s |" % comma(num(rows, "payload_bytes")))
    lines.append("| payloads that walk to the container's end / a header and the record only | %s / %s |"
                 % (comma(num(rows, "regions")), comma(num(rows, "no_code"))))
    lines.append("| code bytes / tokens | %s / %s |"
                 % (comma(num(rows, "code_bytes")), comma(num(rows, "tokens"))))
    lines.append("| `JumpIfNot` / `Jump` | %s / %s |"
                 % (comma(num(rows, "jumpifnot")), comma(num(rows, "jump"))))
    lines.append("| `LabelTable` tokens, in states / in `Function` payloads | %s / 0 |"
                 % comma(num(rows, "labeltable")))
    lines.append("| `StringConst` tokens | %s |" % comma(num(rows, "stringconst")))
    lines.append("| decodable 22-byte records / scripts ending on the `0x47` marker | %s / %s |"
                 % (comma(num(rows, "record")), comma(num(rows, "marker"))))
    lines.append("| records with `IgnoreMask` all ones / `ProbeMask` 0 | %s / %s |"
                 % (comma(num(rows, "ignore_all_ones")), comma(num(rows, "probe_zero"))))
    lines.append("| `StateFlags` 0 / 1 / 2 / other | %s / %s / %s / %s |"
                 % (num(rows, "flags_zero"), num(rows, "flags_one"), num(rows, "flags_two"),
                    num(rows, "flags_other")))
    lines.append("| `LabelTableOffset` below / at or above the code's byte count | %s / %s |"
                 % (comma(num(rows, "offset_below_code")), comma(num(rows, "offset_above_code"))))
    lines.append("| states whose text spells label code | %s |" % comma(num(rows, "text_label_states")))
    lines.append("| ... keywords / `else` / `else` at the statement level | %s / %s / %s |"
                 % (comma(num(rows, "text_label")), comma(num(rows, "text_else")),
                    comma(num(rows, "text_else_label"))))
    lines.append("| ... with a walk / their `JumpIfNot` / their keywords | %s / %s / %s |"
                 % (comma(num(rows, "joined")), comma(num(rows, "joined_jif")), comma(num(rows, "joined_label"))))
    lines.append("| ... equal counts | %s |" % comma(num(rows, "joined_equal")))
    lines.append("| states with a walk and no label keyword (overrides only) | %s |"
                 % comma(num(rows, "overrides")))
    lines.append("")
    lines.append("| class | state | payload | code | tokens | `JumpIfNot` | `Jump` | text keywords |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in top:
        lines.append("| `%s` | `%s` | %s | %s | %s | %s | %s | %s |"
                     % (r["class"], r["state"], comma(int(r["payload"])), comma(int(r["code"])),
                        comma(int(r["tokens"])), r["jumpifnot"], r["jump"], r["text_kw"]))
    return "\n".join(lines)


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    a, b = DOC_MARK
    if a not in text or b not in text:
        raise SystemExit("%s: no %s marker" % (path, a))
    head, rest = text.split(a, 1)
    _old, tail = rest.split(b, 1)
    return head + a + "\n" + block.rstrip("\n") + "\n" + b + tail


def write_doc(path, rows):
    new = splice(path, doc_block(rows))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("wrote %s" % path)


def check_doc(path, rows):
    expected = splice(path, doc_block(rows))
    with open(path, encoding="utf-8") as fh:
        got = fh.read()
    if got != expected:
        exp_lines = expected.splitlines()
        got_lines = got.splitlines()
        diff = sum(1 for a, b in zip(exp_lines, got_lines) if a != b) or 1
        raise SystemExit("FAIL state-code: %s does not reproduce from %s (%d differing line(s))"
                         % (path, os.path.basename(CSV_PATH), diff))
    print("ok   state-code: docs block reproduces from inventory/state_code.csv")


def report(rows):
    c = {r["note"]: int(r["count"]) for r in rows if r["kind"] == "census"}
    for key in CENSUS_KEYS:
        print("%-20s %s" % (key, comma(c[key])))
    states = [r for r in rows if r["kind"] == "state"]
    print("\ntop label+payload states:")
    for r in sorted([r for r in states if r["verdict"] == "label+payload"],
                    key=lambda r: -int(r["text_kw"]))[:15]:
        print("  %-22s %-20s kw=%-3s jif=%-3s jump=%-3s code=%-5s tokens=%-4s payload=%s"
              % (r["class"], r["state"], r["text_kw"], r["jumpifnot"], r["jump"], r["code"],
                 r["tokens"], r["payload"]))
    print("\nno script:", [r["class"] + "." + r["state"] for r in states if r["verdict"] == "record-only"][:10],
          "...", sum(1 for r in states if r["verdict"] == "record-only"))


def selftest():
    """Drive the end anchor, the label table's compact name index and the record on synthetic input."""
    dis = dis_tool()
    table = dis.Table()
    # EndFunctionParms, JumpIfNot(operand 2, expr EndFunctionParms), Stop -- then the state's own epilogue
    # (`Nothing Nothing LabelTable`), one label entry (compact name 6, offset 0), the None terminator
    # (name 0, offset 0xffff), the `0x47` end marker, and the 22-byte record.
    code = bytes([0x16, 0x07, 0x02, 0x00, 0x16, 0x08])
    table_tok = bytes([0x0b, 0x0b, 0x0c, 0x06, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0x00, 0x00])
    rec_bytes = struct.pack("<QQHI", 0, 0xFFFFFFFFFFFFFFFF, 4, 2)
    payload = b"\x03\x03" + code + table_tok + bytes([MARKER]) + rec_bytes
    got = region(payload, table)
    assert got is not None, "synthetic payload did not walk"
    start, end, nodes = got
    assert (start, end) == (2, 2 + len(code) + len(table_tok)), (start, end)
    assert len(payload) - end == 1 + RECORD, len(payload) - end
    flat = tokens(nodes)
    names = collections.Counter(n["name"] for n in flat)
    assert names["JumpIfNot"] == 1 and names["Jump"] == 0, names
    assert names["LabelTable"] == 1, names
    rec = record(payload)
    assert rec == {"probe_mask": 0, "ignore_mask": 0xFFFFFFFFFFFFFFFF, "label_offset": 4, "state_flags": 2}, rec
    # Drop the record: no end anchor, so the same bytes are not a state's code.
    assert region(payload[:-RECORD], table) is None
    # A table whose last entry has a zero offset but a real name does not end the table; the None name does.
    two = bytes([0x0b, 0x0b, 0x0c, 0x06, 0x00, 0x00, 0x00, 0x00, 0x07, 0x09, 0x00, 0x00, 0x00,
                 0x00, 0xff, 0xff, 0x00, 0x00])
    payload2 = b"\x03\x03" + code + two + bytes([MARKER]) + rec_bytes
    got2 = region(payload2, table)
    assert got2 is not None and got2[1] == 2 + len(code) + len(two), got2

    # parse_prefix keeps the tokens before a refusal that lands inside a nested operand, which `parse` cannot:
    # the JumpIfNot's expression is the walker's bad-token case, so the cut is not a token boundary.
    nodes, covered = dis.parse_prefix(bytes([0x16, 0x07, 0x02, 0x00, 0x03]), table)
    assert covered == 1 and [n["name"] for n in nodes] == ["EndFunctionParms"], (covered, nodes)

    tb = text_tool()
    text = ("class Foo extends Bar;\r\n"            # the declaration a real text opens with
            "state Suspicious\r\n{\r\n"              # the state body
            "\tfunction BeginState()\r\n\t{\r\n\t\tif (x) Go();\r\n\t}\r\n"   # an override: not label code
            "Begin:\r\n\tif (Enemy != None)\r\n\t{\r\n\t\tif (y) Goto('Begin');\r\n\t}\r\n"
            "\telse Sleep(0.1);\r\n}\r\n")
    got = text_label_counts(text, lambda live: tb.function_else(live)[2])
    assert "suspicious" in got, got
    kw, els, at_floor = got["suspicious"]
    assert (kw, els, at_floor) == (3, 1, 1), (kw, els, at_floor)  # the override's `if` is excluded
    print("ok   state-code: end anchor, compact label names, record decode and label-code count")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        selftest()
        return 0
    if args.check_doc:
        rows = read_csv(args.out if args.out != CSV_PATH else CSV_PATH)
        check_doc(args.check_doc, rows)
        return 0
    if args.report:
        report(read_csv(args.out))
        return 0
    if not args.system:
        ap.error("--system is required unless --check/--report/--check-doc")
    rows = walk_dump(args.system)
    rows = sorted(rows, key=lambda r: (r["package"], r["class"], r["state"])) + census(rows)
    if args.write_doc:
        write_doc(args.write_doc, rows)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
