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

**The record anchors the start too, and the WORD is what does it.** `LabelTableOffset` is the label table's
offset in the code as the *loader* rebuilds it in memory, where every compact index operand is widened back to
four bytes -- not in the payload's bytes, which is why no offset framing of the stored script ever matched it.
Measured: the field equals the widened offset of the table's **first entry** (one byte past the `0x0c` opcode)
in **166 of 166** payloads that carry a table, and it is `0xffff` in **all 387** that do not. Because the widened
offset depends on where the walk starts, the field also names the start: taking the later start it supports
(the widened offset counts the code, not the header's tail) is what `region()` now does, and the junk
`LocalVariable` tokens the earlier starts produced disappear from the token stream.

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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

CSV_PATH = os.path.join(ROOT, "inventory", "state_code.csv")
DOC_MARK = ("<!-- generated:state-code -->", "<!-- /generated:state-code -->")

RECORD = 22          # ProbeMask u64 + IgnoreMask u64 + LabelTableOffset u16 + StateFlags u32
MARKER = 0x47        # 'G', the end marker UStruct::Serialize appends to a script on save
NO_TABLE = 0xFFFF    # what `LabelTableOffset` reads when the state carries no label table -- and no code

FIELDS = ["kind", "package", "class", "state", "payload", "start", "code", "trailer", "tokens",
          "jumpifnot", "jump", "arms_else", "arms_if", "arms_empty", "arms_leave", "arms_expr",
          "labeltable", "complete", "stringconst", "record", "probe_mask",
          "ignore_mask", "label_offset", "state_flags", "table_w", "start_by", "jump_on", "code_lv", "walk_lv",
          "override_of", "text_kw",
          "text_label", "text_else", "text_else_label", "labels", "labels_on", "labels_named", "labels_last",
          "labels_spelled", "labels_unspelled", "labels_text_only", "verdict", "count", "note"]

CENSUS_NOTE = {
    "states": "`State` export payloads read",
    "payload_bytes": "bytes in those payloads",
    "regions": "payloads whose script walks to the container's end (table, `0x47`, record)",
    "no_code": "payloads that are a header and the record only -- no script",
    "no_table": "records whose `LabelTableOffset` is `0xffff`: no table, so no code",
    "code_bytes": "bytes of the walk, i.e. the state's own code",
    "tokens": "tokens the walk names",
    "jumpifnot": "`JumpIfNot` tokens in them -- the label code's arms",
    "jump": "`Jump` tokens, the `Goto('Label')` and loop backs",
    "arms_else": "`else` arms in those payloads, by `tools/jump-branches.py`'s rule -- the branch census never read a `State`",
    "arms_if": "conditional jumps over a straight arm in them",
    "arms_empty": "empty then-arms in them",
    "arms_leave": "arms that end by leaving their own loop or `switch` -- a `break`/`continue`",
    "arms_expr": "sub-jumps inside a condition",
    "arm_join_states": "states whose text block and walk both exist, so its `else` count can be compared",
    "arm_join_equal": "of those, the ones whose text spells exactly the `else` arms the payload carries",
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
    "offset_entry": "records whose `LabelTableOffset` is the widened offset of the table's first entry",
    "offset_other": "anchored walks whose start the field fits at none of -- the widening model is wrong there",
    "offset_unmeasured": "anchored walks whose operand spans refuse, so the field cannot be tested",
    "start_by_field": "anchored walks whose start the record's field chose",
    "start_shift": "bytes between that start and the first offset of the payload that tiles",
    "override_state": "record-only states an ancestor class declares by the same name",
    "override_code": "of those, the ones whose ancestor state carries a label table (the code is inherited)",
    "override_none": "record-only states no ancestor declares -- a state whose body compiled to nothing",
    "code_local": "`LocalVariable` tokens left in the chosen streams -- a state's code declares none",
    "walk_local": "`LocalVariable` tokens at the most-tokens start, i.e. the header's tail the field drops",
    "jumps": "`Jump`/`JumpIfNot` operands in the anchored walks",
    "jumps_on_boundary": "of those, the ones that land on a token start in the widened code",
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
    "label_tables": "walks whose table decoded to entries",
    "label_entries": "label-table entries, i.e. names a `Goto` can use in that state",
    "label_entries_on_boundary": "entries whose offset is a token start in the widened code",
    "label_entries_named": "entries whose compact name index resolves in the package's name table",
    "label_ends_zero": "tables whose last entry is the code's first byte -- the entry point",
    "label_join_states": "states with both a table and a text block to compare it with",
    "label_spelled": "entries (besides `Begin`) the state's own text spells as a label",
    "label_unspelled": "entries (besides `Begin`) the state's own text does not spell -- code in an override",
    "label_text_only": "labels the text spells that the table has no entry for",
    "label_join_equal": "joined states whose every text label has an entry (the table is a superset)",
}

CENSUS_KEYS = ("states", "payload_bytes", "regions", "no_code", "no_table", "code_bytes", "tokens",
               "jumpifnot", "jump", "arms_else", "arms_if", "arms_empty", "arms_leave", "arms_expr",
               "arm_join_states", "arm_join_equal",
               "labeltable", "labeltable_states", "stringconst", "record", "marker",
               "ignore_all_ones", "probe_zero", "flags_zero", "flags_one", "flags_two", "flags_other",
               "offset_entry", "offset_other", "offset_unmeasured", "start_by_field",
               "start_shift", "override_state", "override_code", "override_none", "code_local", "walk_local",
               "jumps", "jumps_on_boundary", "label_tables", "label_entries", "label_entries_on_boundary",
               "label_entries_named", "label_ends_zero", "label_join_states", "label_spelled",
               "label_unspelled", "label_text_only", "label_join_equal", "text_label_states", "text_label", "text_else",
               "text_else_label", "joined", "joined_jif", "joined_label", "joined_equal", "overrides",
               "payload_arms")


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


def widened_at(spans, pos):
    """One code offset in the loader's memory space: each compact `index` operand takes four bytes there.

    `spans` is `uscript_dis.operand_spans`, whose kind is `index` exactly for those operands. A position inside
    one is not a token boundary and answers None.
    """
    extra = 0
    for a, b, kind in spans:
        if kind != "index":
            continue
        if b <= pos:
            extra += 4 - (b - a)
        elif a < pos < b:
            return None
    return pos + extra


def candidates(payload, table):
    """Every start whose walk ends on the container's end, with the table's widened offset where it is known."""
    dis = dis_tool()
    out = []
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
        t0 = flat[cut]["offset"]
        try:
            spans = dis.operand_spans(payload[start:end], table)
        except Exception:                     # a slice the span walker will not tile: no widening map
            spans = None
        wid = widened_at(spans, t0) if spans is not None else None
        lv = sum(1 for nd in tokens(keep) if nd["name"] == "LocalVariable")
        on = 0
        if spans is not None:
            flat_keep = tokens(keep)
            woffs = set()
            for nd in flat_keep:
                w = widened_at(spans, nd["offset"])
                if w is not None:
                    woffs.add(w)
            on = sum(1 for nd in flat_keep
                     if nd["name"] in ("Jump", "JumpIfNot") and len(nd["operands"]) == 2
                     and (nd["operands"][0] | (nd["operands"][1] << 8)) in woffs)
        out.append({"start": start, "end": end, "nodes": keep, "t0": t0, "spans": spans, "table_w": wid,
                    "jump_on": on, "local": lv, "ntok": len(tokens(keep))})
    return out


def arm_split(nodes, spans):
    """`(arms_else, arms_if, arms_empty, arms_leave, arms_expr)` for one state's code, by the branch census's rule.

    The category rule is `tools/jump-branches.py`'s `classify`, applied to the state's own top-level statements
    instead of a `Function` script's: a state's label block is not a `Function` export, so the branch census skips
    it (`tools/jump-census.py` walks `Function` exports only) and the class-level `else` join had no arms to put
    against the keywords a state body spells. The one difference is the offset space: a state's jump operands are
    in the loader's **widened** code, so each is mapped back to the payload offset it lands on before the rule
    sees it. An operand that lands in no token start is passed as `-1`, so the rule files it under `expression`
    -- the bucket it already has for a jump that names no statement -- and the five categories still partition
    the walk's `JumpIfNot` tokens. The corpus puts every operand on a boundary (`jumps_on_boundary`), so that
    bucket is empty here and the identity is checked rather than assumed.
    """
    jb = load_tool("jump_branches", "jump-branches.py")
    inv = {}
    for nd in tokens(nodes):
        w = widened_at(spans, nd["offset"])
        if w is not None:
            inv[w] = nd["offset"]
    edges = []
    for nd in tokens(nodes):
        if nd["name"] in ("Jump", "JumpIfNot") and len(nd["operands"]) == 2:
            val = nd["operands"][0] | (nd["operands"][1] << 8)
            edges.append((nd["offset"], nd["name"], val, inv.get(val, -1)))
    cats, _stmts = jb.classify(nodes, edges)
    c = collections.Counter(cat for _o, cat, _v, _t in cats)
    return c["else"], c["if"], c["empty"], c["leave"], c["expression"]


def region(payload, table, want=None):
    """The walk that ends on the container's own end: a dict, or None.

    A state payload is `<header><script><0x47><22-byte record>`, so an anchored walk is one whose label table
    ends exactly 23 bytes before the payload does and whose next byte is the marker. The old rule -- the longest
    exact walk, anchored on the table token -- accepted 174 payloads, and 13 of those tables were the record's
    own bytes read as a table; the end test refuses them (a table inside the record cannot be 23 bytes from the
    payload's end *and* end on `0x47`).

    The start is the record's, not a search result. `want` is the record's `LabelTableOffset`, the table's first
    entry in the loader's widened code; among the starts that tile, the one whose widened table offset fits it
    (exactly, or at the `0x0c` opcode for the odd payload where the model and the compiler agree one byte apart)
    is the state's code, and the *latest* such start wins because anything earlier is the header's tail reading
    as `LocalVariable` tokens. Without a field -- a payload with no table, or a caller with no record -- the
    most-tokens rule stays as the fallback.
    """
    cands = candidates(payload, table)
    if not cands:
        return None
    first = min(c["start"] for c in cands)
    if want is not None and want != NO_TABLE:
        entry = [c for c in cands if c["table_w"] is not None and c["table_w"] + 1 == want]
        if entry:
            # A start one byte earlier can fit the same number, so the stream decides: the start that leaves
            # no `LocalVariable` token wins (a state's code declares no locals, and in every one of the 166
            # starts the old rule chose, the prefix it kept was exactly such a token), then the start that puts
            # the most `Jump`/`JumpIfNot` operands on a token boundary in the widened code, then the later start.
            best = max(entry, key=lambda c: (-c["local"], c["jump_on"], c["start"]))
            best["start_by"] = "field"
            best["first"] = first
            best["walk_lv"] = max(cands, key=lambda c: c["ntok"])["local"]
            return best
    best = max(cands, key=lambda c: c["ntok"])
    best["start_by"] = "walk"
    best["first"] = first
    best["walk_lv"] = best["local"]
    return best


def record(payload):
    """`UState`'s 22-byte record at the end of a state payload, or None."""
    if len(payload) < RECORD:
        return None
    pm, im, lto, sf = struct.unpack_from("<QQHI", payload, len(payload) - RECORD)
    return {"probe_mask": pm, "ignore_mask": im, "label_offset": lto, "state_flags": sf}


def label_entries(data):
    """`([(name index, offset)], terminator)` for a label table's bytes, i.e. what follows its `0x0c` opcode.

    An entry is the archive's compact **name index** then a 4-byte code offset, and the table ends on the **None
    name** (index 0) -- not on a zero offset, because a label at the code's first byte has one. The offsets are in
    the loader's widened space like every other code offset here, which is what makes them testable against the
    walk's token starts: an entry names a place the runtime can enter, so it has to be a token boundary.
    """
    dis = dis_tool()
    out, i = [], 0
    while i < len(data):
        try:
            name, j = dis.read_index(data, i)
        except IndexError:
            return out, "ran-off"
        if not name:
            return out, "none-name"
        if j + 4 > len(data):
            return out, "short"
        out.append((name, struct.unpack_from("<I", data, j)[0]))
        i = j + 4
    return out, "end"


def text_label_counts(text, spans_of):
    """`(label keywords, label `else`)` for every `state` block in one class's text, outside its overrides.

    Masking comes from `tools/text-branches.py` and both the block spans and the header rule from
    `class-text-bodies.state_declarations` -- the one rule (UNKNOWNS U24), so this count and the `fn_state*`
    columns `docs/BYTECODE.md` publishes are one reading of the same text. The local pattern this function
    used to take from `text-branches` matched 513 of the corpus's 554 blocks, so a state spelled `state()
    Name`, `state(0) Name`, `simulated state X` or with a qualified `extends` had no label code here at all.
    """
    tb = text_tool()
    live, _comments = tb.mask(text)
    depth = tb.ctb_tool().depth_map(live)
    spans = spans_of(live)
    out = {}
    for decl in tb.ctb_tool().state_declarations(live):
        a, b = decl["start"], decl["end"]
        floor = depth[a] + 1
        kw = els = at_floor = 0
        for mm in tb.WORD.finditer(live[a:b]):
            pos = a + mm.start()
            if any(x <= pos < y for x, y in spans):
                continue
            kw += 1
            if mm.group(1) == "else":
                els += 1
                if depth[pos] == floor:
                    at_floor += 1
        # The labels the body spells at its own statement level: `Name:` at the body's depth and outside every
        # override. `default`/`case` are keywords, not labels, and `Begin` is the state's implicit entry point --
        # the table carries it whether or not the text does, so it is left out of the comparison.
        names = []
        for lm in tb.LABEL.finditer(live[a:b]):
            pos = a + lm.start()
            if depth[pos] != floor or any(a <= pos < b for a, b in spans):
                continue
            if lm.group(1).lower() in ("default", "case"):
                continue
            names.append(lm.group(1).lower())
        out[decl["name"].lower()] = (kw, els, at_floor, names)
    return out


def walk_dump(system):
    """Per-state rows (and the junk-prefix total): the payload walk joined to its class's label keywords."""
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
    shift = 0
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
            got = region(payload, table, rec["label_offset"] if rec else None)
            if got is not None and got["start_by"] != "walk":
                shift += got["start"] - got["first"]
            kw, els, at_floor, tl_names = texts.get((package, cls.lower()), {}).get(
                state.lower(), ("", "", "", []))
            row = {"kind": "state", "package": package, "class": cls, "state": state,
                   "payload": len(payload), "start": "", "code": "", "trailer": "", "tokens": "",
                   "jumpifnot": "", "jump": "", "arms_else": "", "arms_if": "", "arms_empty": "",
                   "arms_leave": "", "arms_expr": "",
                   "labeltable": "", "complete": "", "stringconst": "", "record": RECORD if rec else "",
                   "probe_mask": rec["probe_mask"] if rec else "",
                   "ignore_mask": rec["ignore_mask"] if rec else "",
                   "label_offset": rec["label_offset"] if rec else "",
                   "state_flags": rec["state_flags"] if rec else "",
                   "table_w": "", "start_by": "", "jump_on": "", "code_lv": "", "walk_lv": "", "override_of": "",
                   "text_kw": kw, "text_label": kw, "text_else": els, "text_else_label": at_floor,
                   "labels": "", "labels_on": "", "labels_named": "", "labels_last": "",
                   "labels_spelled": "", "labels_unspelled": "", "labels_text_only": "",
                   "verdict": "record-only", "count": "", "note": ""}
            if got is not None:
                flat = tokens(got["nodes"])
                c = collections.Counter(x["name"] for x in flat)
                row.update({"start": got["start"], "code": got["end"] - got["start"],
                            "trailer": len(payload) - got["end"], "tokens": len(flat),
                            "jumpifnot": c["JumpIfNot"], "jump": c["Jump"],
                            "labeltable": c["LabelTable"], "stringconst": c["StringConst"],
                            "complete": 1, "table_w": got["table_w"] if got["table_w"] is not None else "",
                            "start_by": got["start_by"], "code_lv": got["local"], "walk_lv": got["walk_lv"]})
                if got["spans"] is not None:
                    a_else, a_if, a_empty, a_leave, a_expr = arm_split(got["nodes"], got["spans"])
                    row.update({"arms_else": a_else, "arms_if": a_if, "arms_empty": a_empty,
                                "arms_leave": a_leave, "arms_expr": a_expr})
                woffs = set()
                if got["spans"] is not None:
                    for nd in flat:
                        w = widened_at(got["spans"], nd["offset"])
                        if w is not None:
                            woffs.add(w)
                    row["jump_on"] = sum(1 for nd in flat
                                         if nd["name"] in ("Jump", "JumpIfNot") and len(nd["operands"]) == 2
                                         and (nd["operands"][0] | (nd["operands"][1] << 8)) in woffs)
                # The table's own entries: each one is a label's name and the widened offset its code starts at,
                # so it is testable against the walk's token starts exactly as a jump operand is.
                tabs = [nd for nd in flat if nd["name"] == "LabelTable"]
                if tabs:
                    t = tabs[-1]
                    ents, stop = label_entries(payload[got["start"] + t["offset"] + 1:
                                                       got["start"] + t["end"]])
                    names = [pkg.name(nm) for nm, _off in ents]
                    row["labels"] = len(ents)
                    row["labels_named"] = sum(1 for nm in names if nm)
                    if woffs:
                        row["labels_on"] = sum(1 for _nm, off in ents if off in woffs)
                    if ents:
                        row["labels_last"] = ents[-1][1]
                    if tl_names:
                        spelled = set(tl_names) - {"begin"}
                        table_names = {nm.lower() for nm in names} - {"begin"}
                        row["labels_spelled"] = len(table_names & spelled)
                        row["labels_unspelled"] = len(table_names - spelled)
                        row["labels_text_only"] = len(spelled - table_names)
                    row["note"] = stop
                row["verdict"] = ("label+payload" if kw else "payload-only")
            rows.append(row)
    return rows, shift


def supers():
    """`(package, class)` -> `(super_package, super_class)`, lower-cased, from the committed class table."""
    out = {}
    with open(os.path.join(ROOT, "inventory", "classes.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[(r["package"].lower(), r["class"].lower())] = (r["package_of_super"].lower(),
                                                              (r["super"] or "").lower())
    return out


def inherited(rows):
    """Fill `override_of` on the record-only rows: the nearest ancestor declaring a state of the same name.

    A state that declares no table has no code of its own; whether the code is *inherited* is a question about
    the class tree, and the tree is committed (`inventory/classes.csv`), so this needs no second read of the
    packages. The value names the ancestor, and says when that ancestor has no table either.
    """
    have = {}
    for r in rows:
        have[(r["package"].lower(), r["class"].lower(), r["state"].lower())] = r["verdict"] != "record-only"
    up = supers()
    for r in rows:
        if r["verdict"] != "record-only":
            continue
        pkg, cls, state = r["package"].lower(), r["class"].lower(), r["state"].lower()
        for _hop in range(40):
            edge = up.get((pkg, cls))
            if not edge or not edge[1]:
                break
            pkg, cls = edge
            if (pkg, cls, state) in have:
                r["override_of"] = "%s.%s%s" % (pkg, cls, "" if have[(pkg, cls, state)] else " (no table)")
                break
    return rows


def census(rows, shift=None):
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
            if int(r["label_offset"]) == NO_TABLE:
                c["no_table"] += 1
        if r["verdict"] == "record-only":
            c["no_code"] += 1
            if r["override_of"]:
                c["override_state"] += 1
                if "(no table)" not in r["override_of"]:
                    c["override_code"] += 1
            else:
                c["override_none"] += 1
            continue
        code = int(r["code"])
        c["regions"] += 1
        c["code_bytes"] += code
        c["tokens"] += int(r["tokens"])
        c["jumpifnot"] += int(r["jumpifnot"])
        c["jump"] += int(r["jump"])
        for key in ("arms_else", "arms_if", "arms_empty", "arms_leave", "arms_expr"):
            c[key] += int(r[key] or 0)
        c["payload_arms"] += int(r["jumpifnot"])
        c["labeltable"] += int(r["labeltable"])
        c["stringconst"] += int(r["stringconst"])
        c["marker"] += 1
        if int(r["labeltable"]):
            c["labeltable_states"] += 1
        if r["table_w"] == "":
            c["offset_unmeasured"] += 1
        elif int(r["table_w"]) + 1 == int(r["label_offset"]):
            c["offset_entry"] += 1
        else:
            c["offset_other"] += 1
        if r["start_by"].startswith("field"):
            c["start_by_field"] += 1
        c["code_local"] += int(r["code_lv"] or 0)
        c["walk_local"] += int(r["walk_lv"] or 0)
        c["jumps"] += int(r["jump"]) + int(r["jumpifnot"])
        if r["jump_on"] != "":
            c["jumps_on_boundary"] += int(r["jump_on"])
        if r["labels"] != "":
            c["label_tables"] += 1
            c["label_entries"] += int(r["labels"])
            c["label_entries_named"] += int(r["labels_named"] or 0)
            c["label_entries_on_boundary"] += int(r["labels_on"] or 0)
            if r["labels_last"] != "" and int(r["labels_last"]) == 0:
                c["label_ends_zero"] += 1
        if r["labels_spelled"] != "":
            c["label_join_states"] += 1
            c["label_spelled"] += int(r["labels_spelled"])
            c["label_unspelled"] += int(r["labels_unspelled"] or 0)
            c["label_text_only"] += int(r["labels_text_only"] or 0)
            if int(r["labels_text_only"] or 0) == 0:
                c["label_join_equal"] += 1
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
                if r["arms_else"] != "" and r["verdict"] == "label+payload":
                    c["arm_join_states"] += 1
                    if int(r["arms_else"]) == int(r["text_else"] or 0):
                        c["arm_join_equal"] += 1
        elif r["verdict"] != "record-only":
            c["overrides"] += 1
    if shift is not None:
        c["start_shift"] = shift
    out = []
    for key in CENSUS_KEYS:
        if key not in c:
            raise SystemExit("census key %r accumulated but not written" % key)
        out.append({"kind": "census", "package": "", "class": "", "state": "", "payload": "", "start": "",
                    "code": "", "trailer": "", "tokens": "", "jumpifnot": "", "jump": "", "labeltable": "",
                    "complete": "", "stringconst": "", "record": "", "probe_mask": "", "ignore_mask": "",
                    "label_offset": "", "state_flags": "", "table_w": "", "start_by": "", "jump_on": "",
                    "code_lv": "", "walk_lv": "", "text_kw": "", "text_label": "", "text_else": "",
                    "text_else_label": "", "labels": "", "labels_on": "", "labels_named": "", "labels_last": "",
                    "labels_spelled": "", "labels_unspelled": "", "labels_text_only": "", "verdict": "",
                    "count": c[key],
                    "note": key})
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
    """The generated block: the container's numbers, the record's field, and the states whose label code compiles."""
    states = [r for r in rows if r["kind"] == "state"]
    top = sorted([r for r in states if r["verdict"] == "label+payload"],
                 key=lambda r: -int(r["text_kw"]))[:10]
    lines = []
    lines.append("**All %s `State` payloads end with `UState`'s 22-byte record, %s of them carry code, and the "
                 "record is what anchors it at both ends.** The %s payloads with no script are a header and the "
                 "record whose `LabelTableOffset` is `0xffff` -- no table, no code pointer at all -- and %s of "
                 "them are a state an ancestor class declares by the same name (%s of those ancestors carry the "
                 "table, so the code is inherited), while %s name a state no ancestor declares. Where there is a "
                 "table the field is that table's first entry in the **loader's** code (every compact index "
                 "operand widened back to four bytes), exact in **%s of %s** payloads (%s fit none at all and keep "
                 "the most-tokens fallback). Because the widened offset depends on where the walk starts, the "
                 "field names the start too: it chose **%s** of the %s walks, and they begin **%s bytes** past "
                 "the first offset of the payload that tiles -- the %s `LocalVariable` tokens the most-tokens "
                 "start kept are the header's tail, and **no** state's code keeps one. The code's own jump "
                 "operands live in the same space, so with that start **%s of %s** land on a token start in the "
                 "widened code. The walks carry %s bytes and %s tokens of the state's own label block, "
                 "`JumpIfNot` %s. The table those walks end on is the state's **label map**, and it is readable: "
                 "**%s** entries over %s tables, every one of them a token start in the widened code (**%s**), "
                 "every name index resolving (**%s**), and the last entry at the code's first byte in **%s** "
                 "tables -- so a `Goto('Label')` in a `State` does keep its name, and the runtime can look it up "
                 "(the `Function` half has no table at all). Against the state's own text, **%s** entries besides "
                 "`Begin` are labels that state spells and **%s** are not, while **%s** labels the text spells have "
                 "no entry -- so the table is a superset of the text, and all %s joined states are covered: the "
                 "extra entries are the labels whose code lives in a state override."
                 % (comma(num(rows, "states")), num(rows, "regions"), num(rows, "no_code"),
                    comma(num(rows, "override_state")), comma(num(rows, "override_code")),
                    comma(num(rows, "override_none")),
                    comma(num(rows, "offset_entry")), num(rows, "regions"), num(rows, "offset_other"),
                    comma(num(rows, "start_by_field")), num(rows, "regions"),
                    comma(num(rows, "start_shift")), comma(num(rows, "walk_local")),
                    comma(num(rows, "jumps_on_boundary")), comma(num(rows, "jumps")),
                    comma(num(rows, "code_bytes")), comma(num(rows, "tokens")),
                    comma(num(rows, "jumpifnot")),
                    comma(num(rows, "label_entries")), comma(num(rows, "label_tables")),
                    comma(num(rows, "label_entries_on_boundary")), comma(num(rows, "label_entries_named")),
                    comma(num(rows, "label_ends_zero")), comma(num(rows, "label_spelled")),
                    comma(num(rows, "label_unspelled")), comma(num(rows, "label_text_only")),
                    comma(num(rows, "label_join_equal"))))
    lines.append("")
    lines.append("**The `else` keywords a state body spells are the arms its payload compiles.** The branch census's "
                 "category rule (`tools/jump-branches.py`'s `classify`, which is what reads a `Function` script) run "
                 "over each state's own statements splits its **%s** `JumpIfNot` into **%s** `else` arms, **%s** "
                 "conditional jumps over a straight arm, **%s** empty then-arms, **%s** that leave their own loop or "
                 "`switch`, and **%s** condition sub-jumps. Against the text: **%s** states carry both a readable "
                 "label block and a walk, and **%s of those %s** spell exactly the `else` arms their payload "
                 "carries -- no state disagrees. That is the same **%s** keywords `docs/BYTECODE.md` counts "
                 "*outside every function body*, which is what the class-level `more` verdict compared against "
                 "`Function`-only arms: `tools/jump-census.py` walks `Function` exports and never a `State`, so the "
                 "class count was missing this half. Read here, the population is the state code, arm for arm."
                 % (comma(num(rows, "jumpifnot")), comma(num(rows, "arms_else")), comma(num(rows, "arms_if")),
                    comma(num(rows, "arms_empty")), comma(num(rows, "arms_leave")), comma(num(rows, "arms_expr")),
                    comma(num(rows, "arm_join_states")), comma(num(rows, "arm_join_equal")),
                    comma(num(rows, "arm_join_states")), comma(num(rows, "text_else"))))
    lines.append("")
    lines.append("| measure | value |")
    lines.append("|---|---|")
    lines.append("| `State` exports read | %s |" % comma(num(rows, "states")))
    lines.append("| payload bytes | %s |" % comma(num(rows, "payload_bytes")))
    lines.append("| payloads that walk to the container's end / a header and the record only | %s / %s |"
                 % (comma(num(rows, "regions")), comma(num(rows, "no_code"))))
    lines.append("| records whose `LabelTableOffset` is `0xffff` (no table, no code) | %s |"
                 % comma(num(rows, "no_table")))
    lines.append("| ... a state an ancestor declares / that ancestor carries the table / no ancestor state "
                 "| %s / %s / %s |"
                 % (comma(num(rows, "override_state")), comma(num(rows, "override_code")),
                    comma(num(rows, "override_none"))))
    lines.append("| code bytes / tokens | %s / %s |"
                 % (comma(num(rows, "code_bytes")), comma(num(rows, "tokens"))))
    lines.append("| `JumpIfNot` / `Jump` | %s / %s |"
                 % (comma(num(rows, "jumpifnot")), comma(num(rows, "jump"))))
    lines.append("| ... `else` arms they compile / states whose text spells exactly them | %s / %s |"
                 % (comma(num(rows, "arms_else")), comma(num(rows, "arm_join_equal"))))
    lines.append("| `LabelTable` tokens, in states / in `Function` payloads | %s / 0 |"
                 % comma(num(rows, "labeltable")))
    lines.append("| label-table entries / on a widened token start / named | %s / %s / %s |"
                 % (comma(num(rows, "label_entries")), comma(num(rows, "label_entries_on_boundary")),
                    comma(num(rows, "label_entries_named"))))
    lines.append("| ... tables whose last entry is the code's first byte | %s |" % comma(num(rows, "label_ends_zero")))
    lines.append("| ... entries the state's text spells / does not (besides `Begin`) / text labels with no entry "
                 "| %s / %s / %s |"
                 % (comma(num(rows, "label_spelled")), comma(num(rows, "label_unspelled")),
                    comma(num(rows, "label_text_only"))))
    lines.append("| `StringConst` tokens | %s |" % comma(num(rows, "stringconst")))
    lines.append("| decodable 22-byte records / scripts ending on the `0x47` marker | %s / %s |"
                 % (comma(num(rows, "record")), comma(num(rows, "marker"))))
    lines.append("| records with `IgnoreMask` all ones / `ProbeMask` 0 | %s / %s |"
                 % (comma(num(rows, "ignore_all_ones")), comma(num(rows, "probe_zero"))))
    lines.append("| `StateFlags` 0 / 1 / 2 / other | %s / %s / %s / %s |"
                 % (num(rows, "flags_zero"), num(rows, "flags_one"), num(rows, "flags_two"),
                    num(rows, "flags_other")))
    lines.append("| field = the table's first entry in the widened code / no start fits / unmeasured | %s / %s / %s |"
                 % (comma(num(rows, "offset_entry")), num(rows, "offset_other"), num(rows, "offset_unmeasured")))
    lines.append("| walks whose start the record chose / bytes past the first tiling offset | %s / %s |"
                 % (comma(num(rows, "start_by_field")), comma(num(rows, "start_shift"))))
    lines.append("| `LocalVariable` tokens in the chosen code / at the most-tokens start | %s / %s |"
                 % (comma(num(rows, "code_local")), comma(num(rows, "walk_local"))))
    lines.append("| jump operands on a widened token start | %s / %s |"
                 % (comma(num(rows, "jumps_on_boundary")), comma(num(rows, "jumps"))))
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
    """Drive the end anchor, the compact label names, the record, and the start the record's field chooses."""
    dis = dis_tool()
    table = dis.Table()
    # EndFunctionParms, JumpIfNot(operand 2, expr EndFunctionParms), Stop -- then the state's own epilogue
    # (`Nothing Nothing LabelTable`), one label entry (compact name 6, offset 0), the None terminator
    # (name 0, offset 0xffff), the `0x47` end marker, and the 22-byte record.
    code = bytes([0x16, 0x07, 0x02, 0x00, 0x16, 0x08])
    table_tok = bytes([0x0b, 0x0b, 0x0c, 0x06, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0x00, 0x00])
    head = b"\x03\x03"
    placeholder = struct.pack("<QQHI", 0, 0xFFFFFFFFFFFFFFFF, 0, 2)
    bare = head + code + table_tok + bytes([MARKER]) + placeholder
    # No field: the most-tokens start is the script's own start, and its table offset is the widened one.
    alone = region(bare, table)
    assert alone is not None and alone["start"] == 2, alone
    assert alone["start_by"] == "walk", alone
    entry_at = alone["table_w"] + 1                  # the record's u16 on the true start
    rec_bytes = struct.pack("<QQHI", 0, 0xFFFFFFFFFFFFFFFF, entry_at, 2)
    payload = head + code + table_tok + bytes([MARKER]) + rec_bytes
    got = region(payload, table, entry_at)
    assert got is not None, "synthetic payload did not walk"
    assert (got["start"], got["end"]) == (2, 2 + len(code) + len(table_tok)), (got["start"], got["end"])
    assert got["start_by"] == "field" and got["table_w"] + 1 == entry_at, got
    assert len(payload) - got["end"] == 1 + RECORD, len(payload) - got["end"]
    flat = tokens(got["nodes"])
    names = collections.Counter(n["name"] for n in flat)
    assert names["JumpIfNot"] == 1 and names["Jump"] == 0, names
    assert names["LabelTable"] == 1, names
    rec = record(payload)
    assert rec == {"probe_mask": 0, "ignore_mask": 0xFFFFFFFFFFFFFFFF, "label_offset": entry_at,
                   "state_flags": 2}, rec
    # Drop the record: no end anchor, so the same bytes are not a state's code.
    assert region(payload[:-RECORD], table) is None
    # A table whose last entry has a zero offset but a real name does not end the table; the None name does.
    two = bytes([0x0b, 0x0b, 0x0c, 0x06, 0x00, 0x00, 0x00, 0x00, 0x07, 0x09, 0x00, 0x00, 0x00,
                 0x00, 0xff, 0xff, 0x00, 0x00])
    payload2 = head + code + two + bytes([MARKER]) + rec_bytes
    got2 = region(payload2, table)
    assert got2 is not None and got2["end"] == 2 + len(code) + len(two), got2

    # parse_prefix keeps the tokens before a refusal that lands inside a nested operand, which `parse` cannot:
    # the JumpIfNot's expression is the walker's bad-token case, so the cut is not a token boundary.
    nodes, covered = dis.parse_prefix(bytes([0x16, 0x07, 0x02, 0x00, 0x03]), table)
    assert covered == 1 and [n["name"] for n in nodes] == ["EndFunctionParms"], (covered, nodes)

    # The field refuses the header's tail: a `LocalVariable` in front of the same code tiles, so the most-tokens
    # rule keeps it, and the record's u16 -- measured from the script's start -- cannot be satisfied from there.
    junk = b"\x00\x05"
    payload3 = junk + code + table_tok + bytes([MARKER]) + rec_bytes
    no_field = region(payload3, table)
    assert no_field["start"] == 0 and no_field["start_by"] == "walk", no_field
    assert any(n["name"] == "LocalVariable" for n in tokens(no_field["nodes"])), "no junk to drop"
    by_field = region(payload3, table, entry_at)
    assert by_field["start"] == 2 and by_field["start_by"] == "field", by_field
    assert by_field["local"] == 0 and no_field["local"] == 1, (by_field["local"], no_field["local"])
    # ... and the sentinel says there is no table at all, so nothing anchors a start on it.
    assert region(payload3, table, NO_TABLE)["start_by"] == "walk"

    # The widened map: an index operand is three bytes longer in memory, and a position inside one has no offset.
    spans = dis.operand_spans(bytes([0x01, 0xC1, 0x05, 0x16]), table)  # InstanceVariable(index 2 bytes), EndParms
    assert widened_at(spans, 3) == 5, widened_at(spans, 3)     # 3 bytes in, plus the two the index gains
    assert widened_at(spans, 2) is None, "a position inside an index operand is not a token boundary"

    tb = text_tool()
    text = ("class Foo extends Bar;\r\n"            # the declaration a real text opens with
            "state Suspicious\r\n{\r\n"              # the state body
            "\tfunction BeginState()\r\n\t{\r\n\t\tif (x) Go();\r\n\t}\r\n"   # an override: not label code
            "Begin:\r\n\tif (Enemy != None)\r\n\t{\r\n\t\tif (y) Goto('Begin');\r\n\t}\r\n"
            "\telse Sleep(0.1);\r\n}\r\n")
    got = text_label_counts(text, lambda live: tb.function_else(live)[2])
    assert "suspicious" in got, got
    kw, els, at_floor, names = got["suspicious"]
    assert (kw, els, at_floor) == (3, 1, 1), (kw, els, at_floor)  # the override's `if` is excluded
    assert names == ["begin"], names                              # the body's own label, and only it
    # The table's entries: name index then a 4-byte offset, ending on the None name -- a zero *offset* is a real
    # label at the code's first byte, which is why the terminator is the name.
    ents, stop = label_entries(bytes([0x06, 0x00, 0x00, 0x00, 0x00, 0x07, 0x09, 0x00, 0x00, 0x00, 0x00]))
    assert ents == [(6, 0), (7, 9)] and stop == "none-name", (ents, stop)
    assert label_entries(bytes([0x06, 0x00, 0x00, 0x00, 0x00]))[1] == "end", "an unterminated table is named"
    # The arm split partitions the walk's `JumpIfNot` tokens: five categories, no token silently dropped.
    parts = arm_split(by_field["nodes"], by_field["spans"])
    assert sum(parts) == sum(1 for n in tokens(by_field["nodes"]) if n["name"] == "JumpIfNot"), parts
    print("ok   state-code: end anchor, compact label names, record, field-chosen start, label-code count and the"
          " arm split")


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
    rows, shift = walk_dump(args.system)
    rows = inherited(rows)
    rows = sorted(rows, key=lambda r: (r["package"], r["class"], r["state"])) + census(rows, shift)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    if args.write_doc:
        write_doc(args.write_doc, rows)
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
