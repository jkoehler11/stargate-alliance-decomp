#!/usr/bin/env python3
"""The direct opcode band, slot by slot: `0x70..0xff` against every source that can name one.

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


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def write_doc(path, rows, census):
    new = splice(path, render(rows, census))
    if new is None:
        print("FAIL opcode-band: markers not found in %s" % path)
        return 1
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    print("wrote %s" % path)
    return 0


def artifact_diff(path, rows, census):
    """The committed artifact against this run's join, so a stale CSV cannot agree with a stale page."""
    if not os.path.exists(path):
        return "%s is missing" % path
    with open(path, newline="", encoding="utf-8") as fh:
        back = list(csv.DictReader(fh))
    want = [{c: r.get(c, "") for c in COLUMNS} for r in rows]
    want.append({c: census.get(c, "") for c in COLUMNS})
    if len(back) != len(want):
        return "%s has %d rows, the join gives %d" % (path, len(back), len(want))
    for got, exp in zip(back, want):
        for c in COLUMNS:
            a, b = (got.get(c) or ""), ("" if exp.get(c) is None else str(exp.get(c)))
            if a != b:
                return "%s row %s column %s is %r, the join gives %r" % (path, got.get("opcode"), c, a, b)
    return ""


def check_doc(path, rows, census, artifact=None):
    bad = artifact_diff(artifact, rows, census) if artifact else ""
    if bad:
        print("FAIL opcode-band: " + bad)
        return 1
    got = splice(path, render(rows, census))
    if got is None:
        print("FAIL opcode-band: markers not found in %s" % path)
        return 1
    with open(path, encoding="utf-8", newline="") as fh:
        have = fh.read()
    if got != have:
        print("FAIL opcode-band: %s is stale" % path)
        return 1
    print("ok   opcode-band: %s rows, %s neither, artifact and block reproduce"
          % (census["index"], census["neither"]))
    return 0


def report(rows, census):
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
    return 0


def check():
    """Synthetic inventory, no dump: the four verdicts, the spelling rule, the census and the splice."""
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
        with open(os.path.join(inv, "gnatives_slots.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["index", "address", "name", "evidence", "declarers"])
            for op in (0x70, 0x71, 0x74):
                w.writerow([op, "0x00046f%02x" % op, "", "slot-only", ""])
        with open(os.path.join(inv, "gnatives_records.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["slot", "live_address", "name", "store_site", "record_va", "record_name", "source"])
            w.writerow([0x70, "0x00046f70", "", "yes", "0x005c1a58", "intUObjectexecConcat_StringString", "store"])
            w.writerow([0x71, "0x00046f71", "", "yes", "0x005c1a60", "intUObjectexecSomewhereElse", "store"])
            w.writerow([0x74, "0x00046f74", "", "yes", "0x005c1a68", "intUObjectexecZzz", "store"])
        with open(os.path.join(inv, "natives.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["package", "class", "function", "native_token", "parameter_count", "function_flags"])
            w.writerow(["Core.u", "Object", "Concat_StrStr", 0x70, "2", ""])
            w.writerow(["Core.u", "Object", "Less_StrStr", 0x73, "2", ""])
            w.writerow(["Core.u", "Object", "Yyy", 0x74, "2", ""])
            w.writerow(["Core.u", "Object", "NoIndexAtAll", "", "0", ""])
        with open(os.path.join(inv, "opcode_usage.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["opcode", "name", "tokens", "payloads", "scripts_walked", "scripts_with_epilogue"])
            w.writerow(["0x70", "Concat_StringString", 7, 3, 1, 1])
            w.writerow(["0x71", "", 0, 0, 0, 0])
        bad = []
        out, census = census_rows(*load_inputs(tmp))
        got = {r["opcode"]: (r["verdict"], r["name_match"]) for r in out}
        want = {"0x70": ("both", "spelling"), "0x71": ("registered", ""), "0x72": ("neither", ""),
                "0x73": ("declared", ""), "0x74": ("both", "differ")}
        if {k: got.get(k) for k in want} != want:
            bad.append("verdicts/names %r" % {k: got.get(k) for k in want})
        if (census["both"], census["registered_only"], census["declared_only"], census["neither"]) != (2, 1, 1, 140):
            bad.append("census %r" % {k: census[k] for k in ("both", "registered_only", "declared_only",
                                                             "neither")})
        if (census["runs"], census["tokens"], census["used"], census["corpus_tokens"]) != ("0x72;0x75..0xff",
                                                                                         7, 1, 7):
            bad.append("census %r" % {k: census[k] for k in ("runs", "tokens", "used", "corpus_tokens")})
        if (census["name_exact"], census["name_spelling"], census["name_differ"]) != (0, 1, 1):
            bad.append("name census %r" % {k: census[k] for k in ("name_exact", "name_spelling", "name_differ")})
        path = write(out, census, tmp)
        back = list(csv.DictReader(open(path, newline="")))
        if len(back) != len(out) + 1 or back[-1]["opcode"] != "census" or back[-1]["neither"] != "140":
            bad.append("artifact round trip has %d rows" % len(back))
        doc = os.path.join(tmp, "page.md")
        with open(doc, "w", encoding="utf-8") as fh:
            fh.write("# page\n\n%s\nold\n%s\n" % (DOC_START, DOC_END))
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            stale = check_doc(doc, out, census, path)
            write_doc(doc, out, census)
            fresh = check_doc(doc, out, census, path)
            with open(path, "a", newline="") as fh:      # a stale row must fail even when the page matches
                fh.write(",\n")
            corrupt = check_doc(doc, out, census, path)
        if stale == 0:
            bad.append("check-doc accepted a stale block")
        if fresh != 0:
            bad.append("check-doc refused after write-doc")
        if corrupt == 0:
            bad.append("check-doc accepted a stale artifact row")
        with open(doc, "w", encoding="utf-8") as fh:
            fh.write("# page with no markers\n")
        with contextlib.redirect_stdout(io.StringIO()):
            no_markers = check_doc(doc, out, census, None)
        if no_markers == 0:
            bad.append("check-doc accepted a page with no markers")
        if bad:
            print("FAIL opcode-band: " + "; ".join(bad))
            return 1
        print("ok   opcode-band: 4 verdicts, the spelling rule, %d hole run(s), artifact and markers"
              % len(census["runs"].split(";")))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("-o", "--out", help="write the artifact under this directory (adds opcode_band.csv)")
    ap.add_argument("--report", action="store_true", help="print the census instead of writing")
    ap.add_argument("--write-doc", help="splice the generated block into this page")
    ap.add_argument("--check-doc", help="fail when this page's block is stale")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    rows, census = census_rows(*load_inputs(ROOT))
    if args.write_doc:
        return write_doc(args.write_doc, rows, census)
    if args.check_doc:
        return check_doc(args.check_doc, rows, census, os.path.join(ROOT, "inventory", OUT))
    if args.report:
        return report(rows, census)
    print("wrote %s" % write(rows, census, args.out or ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
