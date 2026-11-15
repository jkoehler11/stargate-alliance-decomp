#!/usr/bin/env python3
"""The opcode bands: `0x70..0xff` slot by slot, `0x60..0x6f` index by index.

Above `0x6f` the token *is* the `GNatives` index (docs/BYTECODE.md, "The opcodes above `0x5f` carry the native
index itself"), so the band `0x70..0xff` covers natives 112..255 and nothing else can hide in it. Three committed
artifacts each make a claim about one of those 144 slots:

* `inventory/gnatives_slots.csv` -- the **runtime** value the guest holds at that slot (16 KB dumped over the xemu
  GDB stub at the main menu);
* `inventory/gnatives_records.csv` -- the `.data` `{name, body}` **record** that value's body belongs to, i.e. the
  image's own spelling of the native (`intUObjectexec<Function>`);
* `inventory/natives.csv` -- every `native(<index>)` declaration in the shipped `ScriptText`, which is the **source**
  side: what a rebuild compiled from those packages would number;

with `inventory/opcode_table.csv` giving the row's own name and pattern and `inventory/opcode_usage.csv` saying
whether any payload writes the token at all.

    python3 tools/opcode-band.py -o inventory          # writes inventory/opcode_band.csv
    python3 tools/opcode-band.py --report              # the same read, printed
    python3 tools/opcode-band.py --write-doc docs/BYTECODE.md
    python3 tools/opcode-band.py --check-doc docs/BYTECODE.md
    python3 tools/opcode-band.py --check               # synthetic inventory, no dump, writes nothing

No dump is needed: every input is committed, so this is a join over artifacts and `--check-doc` rebuilds the page
from the CSV alone.

The extended band is the other half of the same picture and needs its own key: a token in `0x60..0x6f` carries one
operand byte, and the native it calls is `(opcode - 0x60) * 256 + operand` -- an **index**, not the opcode. So
`census_high()` keys its rows on the native and its population is every index above 255 that any of the three
sources reaches (`inventory/opcode_band_high.csv`), which includes the populated slots no token calls; the direct
band cannot report those at all, because for it the slot *is* the token.

    python3 tools/opcode-band.py -o inventory          # writes both CSVs
    python3 tools/opcode-band.py --report              # the same read, printed
    python3 tools/opcode-band.py --write-doc docs/BYTECODE.md
    python3 tools/opcode-band.py --check-doc docs/BYTECODE.md
    python3 tools/opcode-band.py --check               # synthetic inventory, no dump, writes nothing

Why the second band matters (measured 2026-11-15): the two-byte arithmetic was only ever confirmed in aggregate --
the band's 84 natives were compared with the declarations the script ships -- so "the operand byte is the high
half of the numbering" was an inference from a total rather than a per-index join. It holds on every one of them:
the `.data` record the *runtime* `GNatives` value points at spells the same `intUObjectexec<F>` the call site
resolved on all 84, and the populated slots the corpus never calls are the population that join would have missed.

Why it exists (measured 2026-11-15): the L2 control's reach left 47 opcode rows ungraded, and nine of them are the
only band rows whose shape no payload has ever tested. This census says what those nine are. The three naming
sources agree on **every** slot they reach -- 135 of 144 carry a runtime value, a record and a declaration, and **0
slots are one-sided** -- and the nine the band table still gives a `parms` pattern are absent from all three at once,
in three runs of the numbering (`0xc6..0xc7`, `0xcc..0xd1`, `0xe4`), with 0 tokens. The one-sided count is the join's
own control: a key that matched on the wrong field would put the image's name and the source's on different rows,
and there are none. The nine are indices Perception's `Object.uc` does not declare, so a package rebuilt from the
shipped text cannot emit them, and the untested `parms` on those rows cannot mis-grade a rebuild.
"""
import argparse
import contextlib
import io
import csv
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE = os.path.join("inventory", "opcode_table.csv")
SLOTS = os.path.join("inventory", "gnatives_slots.csv")
RECORDS = os.path.join("inventory", "gnatives_records.csv")
NATIVES = os.path.join("inventory", "natives.csv")
USAGE = os.path.join("inventory", "opcode_usage.csv")
OUT = "opcode_band.csv"

BAND = (0x70, 0x100)
DOC_START = "<!-- generated:opcode-band -->"
DOC_END = "<!-- /generated:opcode-band -->"

ROW = ["opcode", "index", "name", "pattern", "registered", "record_name", "declared", "declarers",
       "tokens", "payloads", "name_match", "verdict"]
# The run-level counts ride on the `census` row, so the page and the artifact cannot disagree about them.
CENSUS = ["both", "registered_only", "declared_only", "neither", "used", "corpus_tokens", "name_exact",
          "name_spelling", "name_differ", "runs"]
COLUMNS = ROW + CENSUS

# The extended band: a token there carries one operand byte, and `(op - 0x60) * 256 + operand` is the native it
# calls, so its rows are keyed on the index and its population is every index a source reaches above 255.
HIGH_BASE = 0x60
HIGH_OUT = "opcode_band_high.csv"
BANDS_START = "<!-- generated:opcode-band-bands -->"
BANDS_END = "<!-- /generated:opcode-band-bands -->"
NATIVE_OPCODES = os.path.join("inventory", "native_opcodes.csv")
SYMBOLS = os.path.join("symbols", "xbox_functions.csv")
HIGH_ROW = ["index", "token", "opcode", "operand", "pattern", "slot_name", "body", "symbol",
            "symbol_size", "record_name", "declared", "declarers", "sites", "resolved", "off_chain", "callee",
            "callee_match", "name_match", "verdict", "receiver", "receiver_placed", "receiver_untyped"]
HIGH_CENSUS = ["both", "both_named", "registered_only", "declared_only", "neither", "used",
               "opcodes_used", "unused", "unused_declared", "populated", "declared_total",
               "unnamed", "name_exact", "name_spelling", "name_differ", "callee_exact",
               "callee_spelling", "callee_differ", "sites_total", "resolved_total", "off_chain_total",
               "off_chain_single", "off_chain_placed", "off_chain_typed", "off_chain_untyped",
               "off_chain_rows", "off_chain_indices",
               "symbols_mapped", "symbols_missing", "symbol_bytes", "symbol_min", "symbol_max",
               "unnamed_symbols", "runs"]
HIGH_COLUMNS = HIGH_ROW + HIGH_CENSUS

# The C++ name spells the argument types out (`StringString`); the script's operator declaration abbreviates them
# (`StrStr`). That is one spelling rule, not a disagreement per row -- measured on nine of the 135 slots.
SPELLINGS = [("_StringString", "_StrStr"), ("_StringFloat", "_StrFloat"), ("_StringInt", "_StrInt")]


def read(path, columns):
    if not os.path.exists(path):
        raise SystemExit("opcode-band: %s is missing" % path)
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        missing = [c for c in columns if c not in (r.fieldnames or [])]
        if missing:
            raise SystemExit("opcode-band: %s has no %s column" % (path, ", ".join(missing)))
        return list(r)


def load_inputs(base):
    """Every input as a dict keyed by what this census joins on."""
    table = {}
    for r in read(os.path.join(base, TABLE), ["opcode", "name", "pattern"]):
        op = r["opcode"].strip()
        if op.startswith("0x"):
            table[int(op, 16)] = r
    slots = set()
    for r in read(os.path.join(base, SLOTS), ["index"]):
        if r["index"].strip():
            slots.add(int(r["index"]))
    records = {}
    for r in read(os.path.join(base, RECORDS), ["slot", "record_name"]):
        if r["slot"].strip():
            records[int(r["slot"])] = r["record_name"].strip()
    natives = {}
    for r in read(os.path.join(base, NATIVES), ["package", "class", "function", "native_token"]):
        tok = r["native_token"].strip()
        if tok.isdigit():
            natives.setdefault(int(tok), []).append(r)
    usage = {}
    for r in read(os.path.join(base, USAGE), ["opcode", "tokens", "payloads"]):
        op = r["opcode"].strip()
        if op.startswith("0x"):
            usage[int(op, 16)] = r
    corpus = sum(int(r["tokens"]) for r in read(os.path.join(base, USAGE), ["tokens"])
                 if r["tokens"].strip().isdigit())
    return table, slots, records, natives, usage, corpus


def unspell(name):
    for wide, short in SPELLINGS:
        name = name.replace(wide, short)
    return name


def match(record_name, functions):
    """`exact` / `spelling` / `differ` -- the record's `intUObjectexec<F>` against the declaration's `F`."""
    if not record_name or not functions:
        return ""
    spelled = record_name.split("exec", 1)[1] if "exec" in record_name else record_name
    if spelled in functions:
        return "exact"
    if unspell(spelled) in {unspell(f) for f in functions}:
        return "spelling"
    return "differ"


def runs_of(rows):
    """The contiguous runs of `neither` slots, as `0xNN..0xNN` strings."""
    runs, cur = [], []
    for r in rows:
        if r["verdict"] != "neither":
            continue
        op = int(r["opcode"], 16)
        if cur and op != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(op)
    if cur:
        runs.append(cur)
    return ["0x%02x..0x%02x" % (r[0], r[-1]) if len(r) > 1 else "0x%02x" % r[0] for r in runs]


def census_rows(table, slots, records, natives, usage, corpus):
    """One row per band slot, then one `census` row carrying every number the page quotes."""
    missing = [o for o in range(*BAND) if o not in table]
    if missing:
        raise SystemExit("opcode-band: %s has no row for %s" % (TABLE, ", ".join("0x%02x" % o for o in missing)))
    rows = []
    for op in range(*BAND):
        t = table[op]
        decl = natives.get(op) or []
        names = sorted({r["function"] for r in decl if r["function"].strip()})
        reg = op in slots
        got = records.get(op, "")
        rows.append({
            "opcode": "0x%02x" % op, "index": op, "name": t["name"], "pattern": t["pattern"],
            "registered": "yes" if reg else "no", "record_name": got,
            "declared": len(names),
            "declarers": ";".join(sorted({"%s.%s.%s" % (r["package"], r["class"], r["function"]) for r in decl})),
            "tokens": usage.get(op, {}).get("tokens", "0") or "0",
            "payloads": usage.get(op, {}).get("payloads", "0") or "0",
            "name_match": match(got, names),
            "verdict": ("both" if reg and names else "registered" if reg else "declared" if names else "neither"),
        })
    census = {c: "" for c in COLUMNS}
    for key, val in {
        "opcode": "census", "index": len(rows),
        "tokens": sum(int(r["tokens"]) for r in rows),
        "payloads": sum(int(r["payloads"]) for r in rows),
        "both": sum(1 for r in rows if r["verdict"] == "both"),
        "registered_only": sum(1 for r in rows if r["verdict"] == "registered"),
        "declared_only": sum(1 for r in rows if r["verdict"] == "declared"),
        "neither": sum(1 for r in rows if r["verdict"] == "neither"),
        "used": sum(1 for r in rows if int(r["tokens"])),
        "corpus_tokens": corpus,
        "name_exact": sum(1 for r in rows if r["name_match"] == "exact"),
        "name_spelling": sum(1 for r in rows if r["name_match"] == "spelling"),
        "name_differ": sum(1 for r in rows if r["name_match"] == "differ"),
        "runs": ";".join(runs_of(rows)),
    }.items():
        census[key] = val
    return rows, census


def write(rows, census, out_dir):
    path = os.path.join(out_dir, OUT)
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
        w.writerow({c: census.get(c, "") for c in COLUMNS})
    return path


def render(rows, census):
    """The page block, rebuilt from the CSV alone so `--check-doc` sees exactly what `--write-doc` wrote."""
    holes = [r for r in rows if r["verdict"] == "neither"]
    one_sided = int(census["registered_only"]) + int(census["declared_only"])
    out = [
        "The band is `0x70..0xff`, so its %s slots cover natives 112..255 and nothing else can hide in it. Three"
        % census["index"],
        "sources can name a slot -- the runtime `GNatives` value, the `.data` record that value's body belongs to, and",
        "a `native(<index>)` declaration in the shipped `ScriptText` -- and **they never disagree**:",
        "",
        "| slots | runtime + declaration | runtime only | declaration only | neither |",
        "|---:|---:|---:|---:|---:|",
        "| %s | %s | %s | %s | %s |" % (census["index"], census["both"], census["registered_only"],
                                       census["declared_only"], census["neither"]),
        "",
        ("No slot is one-sided: a key that matched on the wrong field would put the image's name and the source's on"
         if not one_sided else
         "The **%d one-sided slot(s)** are the control on that join: a key that matched on the wrong field would put"
         % one_sided),
        "different rows, and %s. The **%s** slots the source does not declare are the ones the image does not"
        % ("there are none" if not one_sided else "these are the disagreements", census["neither"]),
        "implement either, in %d run(s) of the numbering (`%s`):"
        % (len(census["runs"].split(";")) if census["runs"] else 0, census["runs"].replace(";", "`, `")),
        "",
        "| opcode | index | table pattern | tokens | runtime slot | `.data` record | `native()` |",
        "|---|---:|---|---:|---|---|---|",
    ]
    for r in holes:
        out.append("| `%s` | %s | `%s` | %s | %s | %s | %s |"
                   % (r["opcode"], r["index"], r["pattern"], r["tokens"], r["registered"],
                      r["record_name"] or "-", r["declared"]))
    out += [
        "",
        "**%s of the %s rows are used** -- %s tokens, %.2f%% of the corpus's %s opcode tokens -- and the image's own"
        % (census["used"], census["index"], "{:,}".format(int(census["tokens"])),
           100.0 * int(census["tokens"]) / max(1, int(census["corpus_tokens"])),
           "{:,}".format(int(census["corpus_tokens"]))),
        "spelling of the native matches the declaration exactly on **%s** rows and differs on **%s** by one rule: the"
        % (census["name_exact"], census["name_spelling"]),
        "C++ name writes the argument types out (`execConcat_StringString`) where `Object.uc` abbreviates them",
        "(`Concat_StrStr`). **%s** rows disagree beyond that rule." % census["name_differ"],
    ]
    return "\n".join(out)


def splice(path, block, start=None, end=None):
    start = start or DOC_START
    end = end or DOC_END
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if start not in text or end not in text:
        return None
    head, rest = text.split(start, 1)
    _old, tail = rest.split(end, 1)
    return head + start + "\n" + block + "\n" + end + tail


def blocks(rows, census, hrows, hcensus):
    """Every generated block this tool owns, as `(start marker, end marker, text)`."""
    return [(DOC_START, DOC_END, render(rows, census)),
            (BANDS_START, BANDS_END, render_bands(rows, census, hrows, hcensus))]


def write_doc(path, rows, census, hrows, hcensus):
    # Read first, write last: `open(path, "w")` before the splice truncates the page it is about to read.
    with open(path, encoding="utf-8") as fh:
        new = fh.read()
    for start, end, block in blocks(rows, census, hrows, hcensus):
        if start not in new or end not in new:
            print("FAIL opcode-band: markers not found in %s" % path)
            return 1
        head, rest = new.split(start, 1)
        _old, tail = rest.split(end, 1)
        new = head + start + "\n" + block + "\n" + end + tail
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    print("wrote %s" % path)
    return 0


def artifact_diff(path, rows, census, columns):
    """The committed artifact against this run's join, so a stale CSV cannot agree with a stale page."""
    if not os.path.exists(path):
        return "%s is missing" % path
    with open(path, newline="", encoding="utf-8") as fh:
        back = list(csv.DictReader(fh))
    want = [{c: r.get(c, "") for c in columns} for r in rows]
    want.append({c: census.get(c, "") for c in columns})
    if len(back) != len(want):
        return "%s has %d rows, the join gives %d" % (path, len(back), len(want))
    for got, exp in zip(back, want):
        for c in columns:
            a, b = (got.get(c) or ""), ("" if exp.get(c) is None else str(exp.get(c)))
            if a != b:
                return "%s row %s column %s is %r, the join gives %r" % (path, got.get(columns[0]), c, a, b)
    return ""


def check_doc(path, rows, census, hrows, hcensus, artifacts=()):
    for p, rws, cns, cols in artifacts:
        bad = artifact_diff(p, rws, cns, cols)
        if bad:
            print("FAIL opcode-band: " + bad)
            return 1
    with open(path, encoding="utf-8", newline="") as fh:
        have = fh.read()
    for start, end, block in blocks(rows, census, hrows, hcensus):
        if start not in have or end not in have:
            print("FAIL opcode-band: markers not found in %s" % path)
            return 1
        head, rest = have.split(start, 1)
        _old, tail = rest.split(end, 1)
        have = head + start + "\n" + block + "\n" + end + tail
    with open(path, encoding="utf-8", newline="") as fh:
        if fh.read() != have:
            print("FAIL opcode-band: %s is stale" % path)
            return 1
    print("ok   opcode-band: %s direct slots and %s extended indices reproduce, blocks and artifacts agree"
          % (census["index"], hcensus["index"]))
    return 0


def report(rows, census, hrows, hcensus):
    print("band 0x%02x..0x%02x: %s slots, %s both, %s runtime only, %s declaration only, %s neither"
          % (BAND[0], BAND[1] - 1, census["index"], census["both"], census["registered_only"],
             census["declared_only"], census["neither"]))
    print("used: %s rows, %s of %s tokens" % (census["used"], census["tokens"], census["corpus_tokens"]))
    print("names: %s exact, %s spelling, %s differ" % (census["name_exact"], census["name_spelling"],
                                                       census["name_differ"]))
    print("neither runs: %s" % census["runs"])
    for r in rows:
        if r["verdict"] != "both":
            print("  %s %-3s %-10s registered=%s declared=%s tokens=%s"
                  % (r["opcode"], r["index"], r["verdict"], r["registered"], r["declared"], r["tokens"]))
    print("extended band: %s indices above 255, %s called, %s populated, %s declared"
          % (hcensus["index"], hcensus["used"], hcensus["populated"], hcensus["declared_total"]))
    print("extended verdicts: %s both, %s runtime only, %s declaration only, %s neither"
          % (hcensus["both"], hcensus["registered_only"], hcensus["declared_only"], hcensus["neither"]))
    print("extended callee: %s exact, %s spelling, %s differ over %s called indices"
          % (hcensus["callee_exact"], hcensus["callee_spelling"], hcensus["callee_differ"], hcensus["used"]))
    print("extended sites: %s over %s rows, and %s name a declaration on the caller chain"
          % (hcensus["sites_total"], hcensus["used"], hcensus["resolved_total"]))
    print("extended bodies: %s of %s in the symbol map, %s to %s bytes, %s in total, %s a Native_ label"
          % (hcensus["symbols_mapped"], hcensus["index"], hcensus["symbol_min"], hcensus["symbol_max"],
             hcensus["symbol_bytes"], hcensus["unnamed_symbols"]))
    for r in hrows:
        if r["verdict"] != "both" or not int(r["sites"]):
            print("  %-9s %-5s %-10s slot=%s record=%s declared=%s sites=%s"
                  % (r["token"], r["index"], r["verdict"], r["slot_name"] or "-",
                     r["record_name"] or "-", r["declared"], r["sites"]))
    return 0


def load_high_inputs(base):
    """The inputs only the extended band reads: the runtime slot's name and body, the tokens, and the symbol map.

    The symbol map is a fourth source and it asks a different question -- not what the native is called but whether a
    function body was decoded at the address -- which is what tells an unnamed native from a hole.
    """
    slot_names, bodies = {}, {}
    for r in read(os.path.join(base, SLOTS), ["index", "name", "address"]):
        if r["index"].strip():
            slot_names[int(r["index"])] = r["name"].strip()
            bodies[int(r["index"])] = r["address"].strip().lower()
    symbols = {}
    for r in read(os.path.join(base, SYMBOLS), ["address", "name", "size"]):
        if r["address"].strip():
            symbols[r["address"].strip().lower()] = r
    tokens = {}
    for r in read(os.path.join(base, NATIVE_OPCODES),
                  ["opcode", "operand", "index", "sites", "resolved", "off_chain", "body_name"]):
        if not r["opcode"].strip().startswith("0x"):
            continue
        idx = int(r["index"])
        if idx <= 255:
            continue
        if idx in tokens:
            raise SystemExit("opcode-band: %s names index %d twice" % (NATIVE_OPCODES, idx))
        tokens[idx] = r
    return slot_names, bodies, tokens, symbols


def callee_match(record_name, callee):
    """The `.data` record's spelling at the index against the body the call site itself resolved."""
    if not record_name or not callee:
        return ""
    if record_name == callee:
        return "exact"
    return "spelling" if unspell(record_name) == unspell(callee) else "differ"


def census_high(table, slots, records, natives, slot_names, bodies, tokens, symbols):
    """The extended band keyed on the native, over every index above 255 a source reaches.

    The population is the union of the three sources, not just the called indices: a populated slot no token writes is
    what a key built from the corpus alone cannot see, and it is the control on the join.
    """
    population = sorted(i for i in (set(slots) | set(natives) | set(tokens)) if i > 255)
    rows = []
    for idx in population:
        t = tokens.get(idx) or {}
        op = HIGH_BASE + (idx >> 8)
        decl = natives.get(idx) or []
        names = sorted({r["function"] for r in decl if r["function"].strip()})
        reg = idx in slots
        rec = records.get(idx, "")
        sym = symbols.get(bodies.get(idx, ""), {})
        rows.append({
            "index": idx,
            "token": "0x%02x 0x%02x" % (op, idx & 0xFF),
            "opcode": "0x%02x" % op,
            "operand": "0x%02x" % (idx & 0xFF),
            "pattern": (table.get(op) or {}).get("pattern", ""),
            "slot_name": slot_names.get(idx, ""),
            "body": bodies.get(idx, ""),
            "symbol": sym.get("name", ""),
            "symbol_size": sym.get("size", ""),
            "record_name": rec,
            "declared": len(names),
            "declarers": ";".join(sorted({"%s.%s.%s" % (r["package"], r["class"], r["function"]) for r in decl})),
            "sites": t.get("sites", "0") or "0",
            "resolved": t.get("resolved", "0") or "0",
            "off_chain": t.get("off_chain", "0") or "0",
            # The receiver read travels with the row: the off-chain population is what it explains, and the page
            # quotes the three counts, so they belong in the artifact rather than in the renderer.
            "receiver": t.get("off_chain_receiver", ""),
            "receiver_placed": t.get("off_chain_resolved", "0") or "0",
            "receiver_untyped": t.get("off_chain_untyped", "0") or "0",
            "callee": t.get("body_name", ""),
            "callee_match": callee_match(rec, t.get("body_name", "")),
            "name_match": match(rec, names),
            "verdict": ("both" if reg and names else "registered" if reg else "declared" if names else "neither"),
        })
    census = {c: "" for c in HIGH_COLUMNS}
    populated = [r for r in rows if r["verdict"] in ("both", "registered")]
    unused = [r for r in rows if not int(r["sites"])]
    for key, val in {
        "token": "census", "index": len(rows),
        "both": sum(1 for r in rows if r["verdict"] == "both"),
        "both_named": sum(1 for r in rows if r["verdict"] == "both" and r["record_name"]),
        "registered_only": sum(1 for r in rows if r["verdict"] == "registered"),
        "declared_only": sum(1 for r in rows if r["verdict"] == "declared"),
        "neither": sum(1 for r in rows if r["verdict"] == "neither"),
        "used": sum(1 for r in rows if int(r["sites"])),
        "opcodes_used": len({r["opcode"] for r in rows if int(r["sites"])}),
        "unused": len(unused),
        "unused_declared": sum(1 for r in unused if r["declared"]),
        "populated": len(populated),
        "declared_total": sum(1 for r in rows if r["declared"]),
        "unnamed": sum(1 for r in populated if not r["record_name"]),
        "name_exact": sum(1 for r in rows if r["name_match"] == "exact"),
        "name_spelling": sum(1 for r in rows if r["name_match"] == "spelling"),
        "name_differ": sum(1 for r in rows if r["name_match"] == "differ"),
        "callee_exact": sum(1 for r in rows if r["callee_match"] == "exact"),
        "callee_spelling": sum(1 for r in rows if r["callee_match"] == "spelling"),
        "callee_differ": sum(1 for r in rows if r["callee_match"] == "differ"),
        "sites_total": sum(int(r["sites"]) for r in rows),
        "resolved_total": sum(int(r["resolved"]) for r in rows),
        "off_chain_total": sum(int(r["off_chain"]) for r in rows),
        "off_chain_single": sum(int(r["off_chain"]) for r in rows
                                 if len(natives.get(r["index"], [])) == 1),
        "off_chain_placed": sum(int(r["receiver_placed"]) for r in rows),
        "off_chain_typed": sum(sum(int(x.rsplit(":", 1)[1]) for x in r["receiver"].split(";") if x.strip())
                               for r in rows),
        "off_chain_untyped": sum(int(r["receiver_untyped"]) for r in rows),
        "off_chain_rows": sum(1 for r in rows if int(r["off_chain"])),
        "off_chain_indices": sum(1 for r in rows if int(r["receiver_placed"])),
        "symbols_mapped": sum(1 for r in rows if r["symbol"]),
        "symbols_missing": sum(1 for r in rows if not r["symbol"]),
        "symbol_bytes": sum(int(r["symbol_size"]) for r in rows if r["symbol_size"]),
        "symbol_min": min([int(r["symbol_size"]) for r in rows if r["symbol_size"]] or [0]),
        "symbol_max": max([int(r["symbol_size"]) for r in rows if r["symbol_size"]] or [0]),
        "unnamed_symbols": sum(1 for r in rows if r["symbol"].startswith("Native_")),
        "runs": ";".join(high_runs(rows)),
    }.items():
        census[key] = val
    return rows, census


def high_runs(rows):
    """Contiguous runs of the one-sided indices, as `a..b` strings, for the page's prose."""
    runs, cur = [], []
    for r in rows:
        if r["verdict"] == "both":
            continue
        idx = r["index"]
        if cur and idx != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(idx)
    if cur:
        runs.append(cur)
    return ["%d..%d" % (r[0], r[-1]) if len(r) > 1 else "%d" % r[0] for r in runs]


def write_high(rows, census, out_dir):
    path = os.path.join(out_dir, HIGH_OUT)
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HIGH_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in HIGH_COLUMNS})
        w.writerow({c: census.get(c, "") for c in HIGH_COLUMNS})
    return path


def render_bands(rows, census, hrows, hcensus):
    """The band summary and the extended band's own census, rebuilt from the two row sets alone."""
    spelling = [r["record_name"].split("exec", 1)[1] for r in rows if r["name_match"] == "spelling"]
    shorter = [s.split("_", 1)[0] for s in spelling]
    high_spelling = [r["record_name"] for r in hrows if r["name_match"] == "spelling"]
    onesided = [r for r in hrows if r["verdict"] != "both"]
    named_only = [r for r in onesided if r["record_name"]]
    bare = [r for r in onesided if not r["record_name"]]
    unused = [r for r in hrows if not int(r["sites"])]
    out = [
        "| band | opcodes used | what names it | corpus sites |",
        "|---|---:|---|---:|",
        ("| `0x70..0xff` | %s of 144 | the token *is* the slot: `0x77` is slot 119 "
         "`intUObjectexecNotEqual_ObjectObject` | %s |"
         % (census["used"], "{:,}".format(int(census["tokens"])))),
        ("| `0x60..0x6f` | %s of 16 | `HighNative<n>`; with operand byte `b` the native is `(op-0x60)*256 + b`, so "
         "`0x61 0x16` is 278 `Actor.Spawn` | %s |"
         % (hcensus["opcodes_used"], "{:,}".format(int(hcensus["sites_total"])))),
        "| `0x48..0x5f` | 0 of 24 | **nothing**: the image has no `GNatives` record at any of those 24 slots | 0 |",
        "",
        "That third row is a third independent confirmation that the walker's bad-token band is not an opcode band: it "
        "rejects `0x48..0x5f` and the image has no slot there either.",
        "",
        "Two checks say the join is the naming rather than a plausible one, and both are by content:",
        "",
        ("* **Direct band** -- %s of the %s slots that carry both a record and a declaration spell the function "
         "identically, and the %s that do not differ only in spelling: `Concat_StringString` in C++ where `Object.uc` "
         "declares `Concat_StrStr` (the %s are %s). Those rows carry the declaration in their `note`."
         % (census["name_exact"], census["both"], census["name_spelling"],
            {9: "nine"}.get(int(census["name_spelling"]), census["name_spelling"]),
            ", ".join("`%s`" % s for s in shorter))),
        ("* **High band** -- %s distinct natives, **%s of the %s declared** spell it identically and %s differ only by "
         "that same rule (%s); `python3 tools/uscript-dis.py --highnative DUMP/System` prints the same comparison, and "
         "`inventory/opcode_band_high.csv` is the artifact that holds it, so the two corroborate each other rather "
         "than duplicating a measurement."
         % (hcensus["used"], hcensus["name_exact"], hcensus["both"], hcensus["name_spelling"],
            ", ".join("`%s`" % s for s in high_spelling))),
        "",
        "### The extended band is measured index by index, not token by token",
        "",
        ("`(opcode - 0x60) * 256 + operand` is an **index**, so the second band's census is keyed on the native and its "
         "population is every index above 255 that any of the three sources reaches -- the %s the corpus calls and the "
         "%s it does not. That wider population is what makes the next table a control: a join keyed only on the "
         "called tokens could not tell whether the image agrees or has nothing to say."
         % (hcensus["used"], len(unused))),
        "",
        "| extended indices | runtime + declaration | runtime only | declaration only | neither |",
        "|---:|---:|---:|---:|---:|",
        "| %s | %s | %s | %s | %s |" % (hcensus["index"], hcensus["both"], hcensus["registered_only"],
                                        hcensus["declared_only"], hcensus["neither"]),
        "",
        ("**All %s indices a token calls carry the runtime value and a declaration, %s of them with a `.data` record "
         "name as well, %s are neither, and the %s one-sided rows are all on the runtime side** -- so the operand "
         "byte is not a guess about the high half of the numbering. At every called index the record the *runtime* value points at spells "
         "the same `intUObjectexec<F>` the call site itself resolved (%s of %s), and the declarations agree to within "
         "the one spelling rule above."
         % (hcensus["used"], hcensus["both_named"], hcensus["neither"], len(onesided),
            hcensus["callee_exact"], hcensus["used"])),
        "",
        ("**%s populated slots take no token at all** -- %s of them declared, the rest the runtime's own -- and no "
         "index is declared without being populated, so nothing the shipped text declares is missing from the image: "
         "the mirror of the direct band's nine, from the other side."
         % (len(unused), hcensus["unused_declared"])),
        "",
        ("**The image names %s of the %s one-sided slots and the shipped text declares none of them** (%s), so a "
         "rebuild compiled from these packages cannot emit those tokens; the other %s carry a runtime value whose "
         "record name the `.data` table leaves empty. Put together, they are the control on the join in both "
         "directions -- a key that matched on the wrong field would have put some of them on the *declaration* side, "
         "and %s rows are."
         % (len(named_only), len(onesided),
            ", ".join("`%s` at %s" % (r["record_name"], r["index"]) for r in named_only) or "none", len(bare),
            hcensus["declared_only"])),
        "",
        "| index | token | body | `.data` record | `native()` |",
        "|---:|---|---|---|---|",
    ]
    for r in onesided:
        body = "`%s` %s B" % (r["symbol"], r["symbol_size"]) if r["symbol"] else "-"
        out.append("| %s | `%s` | %s | %s | %s |" % (r["index"], r["token"], body,
                                                    r["record_name"] or "-", r["declared"]))
    biggest = max([int(r["symbol_size"]) for r in onesided if r["symbol_size"]] or [0])
    out += [
        "",
        ("**None of the %s is a hole in the image.** Every slot's body is a decoded function in "
         "`symbols/xbox_functions.csv` -- %s of %s mapped, %s to %s bytes, %s in total -- so they are implemented "
         "and only the shipped text lacks them (the largest is %s bytes). The map's `Native_<n>` label is "
         "`tools/native-names.py`'s fallback for a slot with no record name and %s rows carry it, so it is not "
         "evidence of identity; the byte count is, and it is the one column here that does not come from the "
         "`GNatives` join."
         % (len(onesided), hcensus["symbols_mapped"], hcensus["index"], "{:,}".format(int(hcensus["symbol_min"])),
            "{:,}".format(int(hcensus["symbol_max"])), "{:,}".format(int(hcensus["symbol_bytes"])),
            "{:,}".format(biggest), hcensus["unnamed_symbols"])),
    ]
    untyped_n = int(hcensus["off_chain_untyped"])
    # The clause is rendered, not typed in: a run whose residue is closed must not still be describing what it
    # could not type, and `--check-doc` rebuilds this sentence from the committed CSVs either way.
    untyped_clause = (("%s name no expression that types to a class at all (a call result, a struct member, or a "
                       "type their class never declares)" % "{:,}".format(untyped_n)) if untyped_n
                      else "no site is left with an expression that types to nothing")
    out += [
        "",
        ("%s of the %s high-band sites name a declaration on **their own class's chain** -- the artifact's `resolved` "
         "column -- and the other %s write an index whose declaration sits in a class the caller does not inherit: %s "
         "of those sites write an index only one class declares, so the index still names the native even where the "
         "receiver's class is not the caller's. The column answers a different question from `body_name`: a call site "
         "names the body it "
         "dispatches on whether or not the caller's class declares that native, and reading the two columns as one "
         "would turn a narrower lookup into a disagreement."
         % ("{:,}".format(int(hcensus["resolved_total"])), "{:,}".format(int(hcensus["sites_total"])),
            "{:,}".format(int(hcensus["off_chain_total"])), "{:,}".format(int(hcensus["off_chain_single"])))),
        (""),
        ("**The receiver is what resolves them (measured 2026-11-15): %s of those %s sites name their index on the "
         "chain of the receiver expression's own class, across %s of the %s indices that write an off-chain site -- "
         "%s more have a receiver typed to a class whose own chain does not declare the index, and %s.** The "
         "receiver is the expression in front of the call token, which `uscript-dis.py` types from the class's own "
         "`ScriptText`; `native_opcodes.csv`'s `off_chain_receiver`, `off_chain_resolved` and `off_chain_untyped` "
         "carry the three counts per index, and this table's `receiver`/`receiver_placed`/`receiver_untyped` columns "
         "are that read joined onto the band. Their residue, not a gap in the corpus: the `off_chain` column was a "
         "lookup that asked the caller's ancestry a question about the receiver."
         % ("{:,}".format(int(hcensus["off_chain_placed"])), "{:,}".format(int(hcensus["off_chain_total"])),
            "{:,}".format(int(hcensus["off_chain_indices"])), "{:,}".format(int(hcensus["off_chain_rows"])),
            "{:,}".format(int(hcensus["off_chain_typed"]) - int(hcensus["off_chain_placed"])),
            untyped_clause)),
    ]
    return "\n".join(out)


def check():
    """Synthetic inventory, no dump: both bands' verdicts, the spelling rule, the censuses and the two splices."""
    tmp = tempfile.mkdtemp(prefix="opcode-band-")
    try:
        inv = os.path.join(tmp, "inventory")
        os.makedirs(inv)
        named = {0x70: "Concat_StringString", 0x73: "Less_StringString"}
        with open(os.path.join(inv, "opcode_table.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["opcode", "group", "handler", "name", "pattern", "fixed_bytes", "source", "note"])
            for op in range(*BAND):
                w.writerow(["0x%02x" % op, "21", "", named.get(op, ""), "parms", "", "range", ""])
            for op in range(0x60, 0x70):
                w.writerow(["0x%02x" % op, "22", "", "", "bytes:1,parms", "1", "range", ""])
        with open(os.path.join(inv, "gnatives_slots.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["index", "address", "name", "evidence", "declarers"])
            for op in (0x70, 0x71, 0x74):
                w.writerow([op, "0x00046f%02x" % op, "", "slot-only", ""])
            w.writerow([256, "0x0020c830", "execFoo", "declared", "Engine:Actor:Foo"])
            w.writerow([257, "0x00043670", "execBar_StringString", "declared", "Core:Object:Bar"])
            w.writerow([300, "0x0020cc10", "", "slot-only", ""])
            w.writerow([302, "0x0020cd00", "", "slot-only", ""])
        with open(os.path.join(inv, "gnatives_records.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["slot", "live_address", "name", "store_site", "record_va", "record_name", "source"])
            w.writerow([0x70, "0x00046f70", "", "yes", "0x005c1a58", "intUObjectexecConcat_StringString", "store"])
            w.writerow([0x71, "0x00046f71", "", "yes", "0x005c1a60", "intUObjectexecSomewhereElse", "store"])
            w.writerow([0x74, "0x00046f74", "", "yes", "0x005c1a68", "intUObjectexecZzz", "store"])
            w.writerow([256, "0x0020c830", "", "yes", "0x005c2a00", "intUObjectexecFoo", "store"])
            w.writerow([257, "0x00043670", "", "yes", "0x005c2a08", "intUObjectexecBar_StringString", "store"])
            w.writerow([300, "0x0020cc10", "", "yes", "0x005c2a10", "", "store"])
        with open(os.path.join(inv, "natives.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["package", "class", "function", "native_token", "parameter_count", "function_flags"])
            w.writerow(["Core.u", "Object", "Concat_StrStr", 0x70, "2", ""])
            w.writerow(["Core.u", "Object", "Less_StrStr", 0x73, "2", ""])
            w.writerow(["Core.u", "Object", "Yyy", 0x74, "2", ""])
            w.writerow(["Core.u", "Object", "NoIndexAtAll", "", "0", ""])
            w.writerow(["Engine.u", "Actor", "Foo", 256, "1", ""])
            w.writerow(["Core.u", "Object", "Bar_StrStr", 257, "2", ""])
            w.writerow(["Core.u", "Object", "Qqq", 301, "0", ""])
        os.makedirs(os.path.join(tmp, "symbols"))
        with open(os.path.join(tmp, "symbols", "xbox_functions.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["address", "name", "size", "module"])
            w.writerow(["0x0020c830", "execFoo", 104, ""])
            w.writerow(["0x00043670", "execBar_StrStr", 117, ""])
            w.writerow(["0x0020cc10", "Native_300", 25, ""])
            w.writerow(["0x0020cd00", "Native_302", 1778, ""])
        with open(os.path.join(inv, "native_opcodes.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["opcode", "operand", "index", "sites", "resolved", "off_chain", "param_fit", "body_name",
                        "body_match", "native", "declared_in", "declared_by", "declared_params", "args",
                        "off_chain_receiver", "off_chain_resolved", "off_chain_untyped"])
            w.writerow(["0x61", "0x00", 256, 3, 2, 1, 1, "intUObjectexecFoo", 1, "Foo", "Actor", "Actor", "1", "1:3/3",
                        "Pawn:1", 1, 0])
            w.writerow(["0x61", "0x01", 257, 5, 4, 1, 1, "intUObjectexecBar_StringString", 1, "Bar_StrStr", "Object",
                        "Object", "2", "2:5/5", "", 0, 1])
        with open(os.path.join(inv, "opcode_usage.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["opcode", "name", "tokens", "payloads", "scripts_walked", "scripts_with_epilogue"])
            w.writerow(["0x70", "Concat_StringString", 7, 3, 1, 1])
            w.writerow(["0x71", "", 0, 0, 0, 0])
            w.writerow(["0x61", "HighNative1", 8, 3, 1, 1])
        bad = []
        inputs = load_inputs(tmp)
        out, census = census_rows(*inputs)
        hout, hcensus = census_high(*inputs[:4], *load_high_inputs(tmp))
        got = {r["opcode"]: (r["verdict"], r["name_match"]) for r in out}
        want = {"0x70": ("both", "spelling"), "0x71": ("registered", ""), "0x72": ("neither", ""),
                "0x73": ("declared", ""), "0x74": ("both", "differ")}
        if {k: got.get(k) for k in want} != want:
            bad.append("verdicts/names %r" % {k: got.get(k) for k in want})
        if (census["both"], census["registered_only"], census["declared_only"], census["neither"]) != (2, 1, 1, 140):
            bad.append("census %r" % {k: census[k] for k in ("both", "registered_only", "declared_only",
                                                             "neither")})
        if (census["runs"], census["tokens"], census["used"], census["corpus_tokens"]) != ("0x72;0x75..0xff",
                                                                                         7, 1, 15):
            bad.append("census %r" % {k: census[k] for k in ("runs", "tokens", "used", "corpus_tokens")})
        if (census["name_exact"], census["name_spelling"], census["name_differ"]) != (0, 1, 1):
            bad.append("name census %r" % {k: census[k] for k in ("name_exact", "name_spelling", "name_differ")})
        hgot = {r["index"]: (r["verdict"], r["name_match"], r["callee_match"], r["sites"]) for r in hout}
        hwant = {256: ("both", "exact", "exact", "3"), 257: ("both", "spelling", "exact", "5"),
                 300: ("registered", "", "", "0"), 301: ("declared", "", "", "0"),
                 302: ("registered", "", "", "0")}
        if hgot != hwant:
            bad.append("extended verdicts %r" % hgot)
        if (hcensus["both"], hcensus["registered_only"], hcensus["declared_only"],
                hcensus["neither"]) != (2, 2, 1, 0):
            bad.append("extended census %r" % {k: hcensus[k] for k in ("both", "registered_only",
                                                                      "declared_only", "neither")})
        if (hcensus["used"], hcensus["unused"], hcensus["populated"], hcensus["declared_total"]) != (2, 3, 4, 3):
            bad.append("extended census %r" % {k: hcensus[k] for k in ("used", "unused", "populated",
                                                                      "declared_total")})
        if hcensus["unused_declared"] != 1:
            bad.append("extended unused_declared %r" % hcensus["unused_declared"])
        if (hcensus["off_chain_total"], hcensus["off_chain_single"], hcensus["sites_total"] - hcensus["resolved_total"]) \
                != (2, 2, 2):
            bad.append("extended off-chain %r" % {k: hcensus[k] for k in ("off_chain_total", "off_chain_single")})
        # The receiver read: one row placed by its receiver and one that names no class, so the three counts and the
        # two populations have to disagree with each other where the synthetic rows do.
        if (hcensus["off_chain_placed"], hcensus["off_chain_typed"], hcensus["off_chain_untyped"],
                hcensus["off_chain_rows"], hcensus["off_chain_indices"]) != (1, 1, 1, 2, 1):
            bad.append("extended receiver census %r" % {k: hcensus[k] for k in (
                "off_chain_placed", "off_chain_typed", "off_chain_untyped", "off_chain_rows", "off_chain_indices")})
        if (hcensus["callee_exact"], hcensus["callee_spelling"], hcensus["callee_differ"],
                hcensus["sites_total"], hcensus["resolved_total"]) != (2, 0, 0, 8, 6):
            bad.append("extended callee %r" % {k: hcensus[k] for k in ("callee_exact", "callee_spelling",
                                                                      "callee_differ", "sites", "resolved")})
        if (hcensus["symbols_mapped"], hcensus["symbols_missing"], hcensus["unnamed_symbols"],
                hcensus["symbol_bytes"], hcensus["symbol_max"]) != (4, 1, 2, 2024, 1778):
            bad.append("extended symbols %r" % {k: hcensus[k] for k in ("symbols_mapped", "symbols_missing",
                                                                       "unnamed_symbols", "symbol_bytes",
                                                                       "symbol_max")})
        if hcensus["opcodes_used"] != 1:
            bad.append("extended opcodes_used %r" % hcensus["opcodes_used"])
        if (hcensus["both_named"], hcensus["index"]) != (2, 5):
            bad.append("extended both_named %r" % hcensus["both_named"])
        if (hcensus["index"], hcensus["unnamed"], hcensus["runs"]) != (5, 2, "300..302"):
            bad.append("extended rows %r" % {k: hcensus[k] for k in ("index", "unnamed", "runs")})
        path = write(out, census, tmp)
        hpath = write_high(hout, hcensus, tmp)
        back = list(csv.DictReader(open(path, newline="")))
        if len(back) != len(out) + 1 or back[-1]["opcode"] != "census" or back[-1]["neither"] != "140":
            bad.append("artifact round trip has %d rows" % len(back))
        hback = list(csv.DictReader(open(hpath, newline="")))
        if len(hback) != len(hout) + 1 or hback[-1]["token"] != "census" or hback[-1]["used"] != "2":
            bad.append("extended artifact round trip has %d rows" % len(hback))
        doc = os.path.join(tmp, "page.md")
        with open(doc, "w", encoding="utf-8") as fh:
            fh.write("# page\n\n%s\nold\n%s\n\n%s\nold\n%s\n" % (DOC_START, DOC_END, BANDS_START, BANDS_END))
        arts = ((path, out, census, COLUMNS), (hpath, hout, hcensus, HIGH_COLUMNS))
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            stale = check_doc(doc, out, census, hout, hcensus, arts)
            write_doc(doc, out, census, hout, hcensus)
            fresh = check_doc(doc, out, census, hout, hcensus, arts)
            with open(hpath, "a", newline="") as fh:      # a stale row in the *second* artifact must fail too
                fh.write(",\n")
            corrupt = check_doc(doc, out, census, hout, hcensus, arts)
        if stale == 0:
            bad.append("check-doc accepted a stale block")
        if fresh != 0:
            bad.append("fresh check-doc refused after write-doc")
        if corrupt == 0:
            bad.append("check-doc accepted a stale extended artifact row")
        with open(doc, "w", encoding="utf-8") as fh:
            fh.write("# page\n\n%s\nold\n%s\n" % (DOC_START, DOC_END))
        with contextlib.redirect_stdout(io.StringIO()):
            no_markers = check_doc(doc, out, census, hout, hcensus, ())
        if no_markers == 0:
            bad.append("check-doc accepted a page missing the band markers")
        if bad:
            print("FAIL opcode-band: " + "; ".join(bad))
            return 1
        print("ok   opcode-band: 4 verdicts per band, the spelling rule, %d hole run(s), both artifacts and markers"
              % len(census["runs"].split(";")))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("-o", "--out", help="write the artifacts under this directory (both CSVs)")
    ap.add_argument("--report", action="store_true", help="print the census instead of writing")
    ap.add_argument("--write-doc", help="splice the generated blocks into this page")
    ap.add_argument("--check-doc", help="fail when this page's blocks or either artifact is stale")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    inputs = load_inputs(ROOT)
    rows, census = census_rows(*inputs)
    hrows, hcensus = census_high(*inputs[:4], *load_high_inputs(ROOT))
    out = args.out or os.path.join(ROOT, "inventory")
    if args.write_doc:
        return write_doc(args.write_doc, rows, census, hrows, hcensus)
    if args.check_doc:
        return check_doc(args.check_doc, rows, census, hrows, hcensus,
                         [(os.path.join(out, OUT), rows, census, COLUMNS),
                          (os.path.join(out, HIGH_OUT), hrows, hcensus, HIGH_COLUMNS)])
    if args.report:
        return report(rows, census, hrows, hcensus)
    print("wrote %s" % write(rows, census, out))
    print("wrote %s" % write_high(hrows, hcensus, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
