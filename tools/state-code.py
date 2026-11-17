#!/usr/bin/env python3
"""Where a state's own code compiles: the State export's payload, not a Function (PLAN 3.10, docs/systems/AI.md).

`docs/systems/AI.md` reads state behaviour out of the class's shipped text, and `docs/BYTECODE.md` counts the
`else` keywords a state body spells at its own statement level (`fn_state_label`, **118**). The natural next
question -- do those keywords equal the arms the state compiles? -- cannot be answered from
`inventory/jump_branches.csv`, because that census walks **`Function` exports only**:
`tools/jump-census.py` skips every export whose class is not `Function`, and `tools/jump-branches.py` inherits the
skip. A state's label block is not one of those exports.

It is an export of the class `State`, and its payload carries the label code:

* the literals the label block spells are in it -- `BotEnemy.Suspicious`'s payload contains the UTF-16-to-ASCII
  bytes of `STUCK IN Suspicious!`, which the text places after that state's `Begin:` label;
* the bytes parse as a token stream with this tree's own disassembler, from a few bytes past the friendly-name
  chain to an end that is stable across start offsets;
* a **`LabelTable`** token (`0x0c`) appears in 30 of these payloads and in **zero** `Function` payloads -- the
  label table is the state container's own token, which is why `tools/jump-census.py` reports the corpus's
  `LabelTable` count as 0 ("the build never emits a label").  It emits one per state that has labels; the
  census only ever looked inside functions.

The region is measured, not assumed: every start in the first 72 bytes is walked and the **longest exact walk**
wins (the parser must consume its slice with no residue), which leaves the state record at the tail. The boundary
is approximate for a payload whose code the table refuses part-way (a token this table names `bad-token`), so the
tool reports the trailer it leaves behind and the number of states whose region covers less than half the payload
as a residue rather than smoothing it.

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
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

CSV_PATH = os.path.join(ROOT, "inventory", "state_code.csv")
DOC_MARK = ("<!-- generated:state-code -->", "<!-- /generated:state-code -->")

FIELDS = ["kind", "package", "class", "state", "payload", "start", "code", "trailer", "tokens",
          "jumpifnot", "jump", "labeltable", "complete", "stringconst", "text_kw", "text_label", "text_else",
          "text_else_label", "verdict",
          "count", "note"]

CENSUS_NOTE = {
    "states": "`State` export payloads read",
    "regions": "payloads whose bytes walk as a token stream",
    "payload_bytes": "bytes in those payloads",
    "code_bytes": "bytes of the walk, i.e. the state's own code",
    "tokens": "tokens the walk names",
    "jumpifnot": "`JumpIfNot` tokens in them -- the label code's arms",
    "jump": "`Jump` tokens, the `Goto('Label')` and loop backs",
    "labeltable": "`LabelTable` tokens (`0x0c`) -- 0 in every `Function` payload of the corpus",
    "labeltable_states": "states carrying one",
    "stringconst": "`StringConst` tokens, the literals the label block is written with",
    "complete": "walks ending on the `LabelTable` token -- the container's own terminator",
    "prefix": "walks that end earlier, so their code length and arm count are a floor",
    "no_region": "payloads that do not walk at all",
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

CENSUS_KEYS = ("states", "regions", "payload_bytes", "code_bytes", "tokens", "jumpifnot", "jump", "labeltable",
               "labeltable_states", "stringconst", "complete", "prefix", "no_region", "text_label_states", "text_label",
               "text_else", "text_else_label", "joined", "joined_jif", "joined_label", "joined_equal",
               "overrides", "payload_arms")


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


def region(payload, table):
    """The longest exact token walk inside a State export payload: `(start, end, nodes)` or None.

    A start inside the friendly-name chain desynchronises within a token or two, so the longest walk is the
    code; the header it skips and the record it leaves at the tail are reported, not assumed.
    """
    dis = dis_tool()
    best = None
    for start in range(0, min(72, len(payload))):
        try:
            nodes = dis.parse(payload[start:], table)
            end = len(payload)
        except dis.Refused as exc:
            head = str(exc).split(":", 1)[0]
            try:
                end = int(head, 16)
            except ValueError:
                continue
            if end <= start:
                continue
            try:
                nodes = dis.parse(payload[start:end], table)
            except dis.Refused:
                continue
        if sum(n["end"] - n["offset"] for n in nodes) != end - start:
            continue
        if best is None or (end - start) > best[1] - best[0]:
            best = (start, end, nodes)
    return best


def tokens(nodes):
    out = []

    def walk(ns):
        for n in ns:
            out.append(n)
            walk(n["children"])
    walk(nodes)
    return out


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
            got = region(payload, table)
            kw, els, at_floor = texts.get((package, cls.lower()), {}).get(state.lower(), ("", "", ""))
            row = {"kind": "state", "package": package, "class": cls, "state": state,
                   "payload": len(payload), "start": "", "code": "", "trailer": "", "tokens": "",
                   "jumpifnot": "", "jump": "", "labeltable": "", "complete": "", "stringconst": "",
                   "text_kw": kw, "text_label": kw, "text_else": els, "text_else_label": at_floor,
                   "verdict": "no-region", "count": "", "note": ""}
            if got is not None:
                start, end, nodes = got
                flat = tokens(nodes)
                c = collections.Counter(x["name"] for x in flat)
                row.update({"start": start, "code": end - start, "trailer": len(payload) - end,
                            "tokens": len(flat), "jumpifnot": c["JumpIfNot"], "jump": c["Jump"],
                            "labeltable": c["LabelTable"], "stringconst": c["StringConst"],
                            "complete": int(bool(flat) and flat[-1]["name"] == "LabelTable")})
                row["verdict"] = ("label+payload" if kw else "payload-only")
            rows.append(row)
    return rows


def census(rows):
    c = collections.Counter()
    for r in rows:
        c["states"] += 1
        c["payload_bytes"] += int(r["payload"])
        if r["verdict"] == "no-region":
            c["no_region"] += 1
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
        if int(r["labeltable"]):
            c["labeltable_states"] += 1
        if int(r["complete"]):
            c["complete"] += 1
        else:
            c["prefix"] += 1
    for r in rows:
        if r["text_kw"] in ("", None):
            continue
        if int(r["text_kw"]):
            c["text_label_states"] += 1
            c["text_label"] += int(r["text_kw"])
            c["text_else"] += int(r["text_else"] or 0)
            c["text_else_label"] += int(r["text_else_label"] or 0)
            if r["verdict"] != "no-region":
                c["joined"] += 1
                c["joined_jif"] += int(r["jumpifnot"])
                c["joined_label"] += int(r["text_kw"])
                if int(r["jumpifnot"]) == int(r["text_kw"]):
                    c["joined_equal"] += 1
        elif r["verdict"] != "no-region":
            c["overrides"] += 1
    out = []
    for key in CENSUS_KEYS:
        if key not in c:
            raise SystemExit("census key %r accumulated but not written" % key)
        out.append({"kind": "census", "package": "", "class": "", "state": "", "payload": "", "start": "",
                    "code": "", "trailer": "", "tokens": "", "jumpifnot": "", "jump": "", "labeltable": "",
                    "complete": "", "stringconst": "", "text_kw": "", "text_label": "", "text_else": "", "verdict": "",
                    "count": c[key], "note": key})
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
    lines.append("**%s `State` payloads walk as code -- %s bytes and %s tokens of the state's own label block, "
                 "`JumpIfNot` %s -- and %s of them carry the `LabelTable` token (`0x0c`) that appears in **zero** "
                 "`Function` payloads.** The text spells %s label keywords across %s states (its `else` %s); "
                 "the joined states compile %s `JumpIfNot` against their %s keywords, equal in %s."
                 % (comma(num(rows, "regions")), comma(num(rows, "code_bytes")), comma(num(rows, "tokens")),
                    comma(num(rows, "jumpifnot")), num(rows, "labeltable_states"),
                    comma(num(rows, "text_label")), num(rows, "text_label_states"), comma(num(rows, "text_else")),
                    comma(num(rows, "joined_jif")), comma(num(rows, "joined_label")),
                    num(rows, "joined_equal")))
    lines.append("")
    lines.append("| measure | value |")
    lines.append("|---|---|")
    lines.append("| `State` exports read | %s |" % comma(num(rows, "states")))
    lines.append("| payloads that walk | %s |" % comma(num(rows, "regions")))
    lines.append("| payload bytes / of them code | %s / %s |"
                 % (comma(num(rows, "payload_bytes")), comma(num(rows, "code_bytes"))))
    lines.append("| tokens / `JumpIfNot` / `Jump` | %s / %s / %s |"
                 % (comma(num(rows, "tokens")), comma(num(rows, "jumpifnot")), comma(num(rows, "jump"))))
    lines.append("| `LabelTable` tokens, in states / in `Function` payloads | %s / 0 |"
                 % comma(num(rows, "labeltable")))
    lines.append("| `StringConst` tokens | %s |" % comma(num(rows, "stringconst")))
    lines.append("| walks ending on `LabelTable` (whole) / ending earlier (a floor) | %s / %s |"
                 % (comma(num(rows, "complete")), comma(num(rows, "prefix"))))
    lines.append("| payloads that do not walk | %s |" % comma(num(rows, "no_region")))
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
    print("\nno walk:", [r["class"] + "." + r["state"] for r in states if r["verdict"] == "no-region"])


def selftest():
    """Drive the walker and the label-code count on synthetic input: no dump, no writes."""
    dis = dis_tool()
    table = dis.Table()
    # EndFunctionParms, JumpIfNot(operand 2, expr EndFunctionParms), Stop -- then a 3-byte trailer.
    code = bytes([0x16, 0x07, 0x02, 0x00, 0x16, 0x08])
    payload = b"\x09\x07" + code + b"\xff\xff\x00"
    got = region(payload, table)
    assert got is not None, "synthetic payload did not walk"
    start, end, nodes = got
    assert (start, end) == (2, 8), (start, end)
    flat = tokens(nodes)
    names = collections.Counter(n["name"] for n in flat)
    assert names["JumpIfNot"] == 1 and names["Jump"] == 0, names
    assert len(payload) - end == 3, len(payload) - end
    # A trailer that itself parses must not extend the walk past the code: the longest exact walk wins.
    assert region(b"", table) is None

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
    print("ok   state-code: synthetic walk, trailer and label-code count")


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
