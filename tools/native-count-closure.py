#!/usr/bin/env python3
"""Close the source-count against the record count, per package, by an identity rather than by arithmetic.

`docs/PACKAGE_SCRIPT_TEXT.md` compares two different populations -- `native function` declarations in the
shipped `ScriptText` and function records in the class -- and a difference between them is not an error until
someone says what it is made of. This tool says it, per package, from committed artifacts only (no dump):

    records        = matched + operator_records + record_only
    declarations   = matched + declaration_only + live_operators

* `matched` (functions and `native event` declarations that name a record) and `operator_records` come from
  `inventory/native_reconcile.csv`, where the match is by name, case-insensitively;
* `record_only` and `declaration_only` are the named rows of `inventory/native_residual.csv`;
* `live_functions`/`live_events`/`live_operators` are the live declarations of the same artifact, which strips
  both comment forms -- the whole-buffer number (`inventory/script_text_natives.csv`) is larger by
  `inventory/class_text_comments.csv`'s two disabled columns, and quoting it instead is what made `Core` look
  like a 102-declaration shortfall and `Engine` like 7 extra functions.

The identity is the finding: one `operator` statement becomes several typed records (`Add_IntInt`,
`AddEqual_VectorVector`), so a package can record more natives than its text declares without a single record
being unexplained, and a declaration can have no record at all. Both directions are counted apart here.

    tools/native-count-closure.py -o inventory
    tools/native-count-closure.py --report
    tools/native-count-closure.py --write-doc docs/PACKAGE_SCRIPT_TEXT.md
    tools/native-count-closure.py --check-doc docs/PACKAGE_SCRIPT_TEXT.md   # from the committed artifact
    tools/native-count-closure.py --check                                   # synthetic, writes nothing
"""
import argparse
import csv
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ARTIFACT = os.path.join(ROOT, "inventory/native_count_closure.csv")
RECONCILE = os.path.join(ROOT, "inventory/native_reconcile.csv")
RESIDUAL = os.path.join(ROOT, "inventory/native_residual.csv")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
SOURCES = os.path.join(ROOT, "inventory/script_text_natives.csv")
COMMENTS = os.path.join(ROOT, "inventory/class_text_comments.csv")
DOC = os.path.join(ROOT, "docs/PACKAGE_SCRIPT_TEXT.md")
DOC_START = "<!-- generated:native-count-closure -->"
DOC_END = "<!-- /generated:native-count-closure -->"

FIELDS = ["kind", "package", "classes", "whole", "disabled_block", "disabled_line", "live_functions",
          "live_events", "live_operators", "matched", "operator_records", "record_only",
          "declaration_only", "records", "verdict", "count", "note"]


def rows_of(path):
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def number(row, key):
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def build(reconcile, residual, classes, sources, comments):
    """[(kind, package, ...)] -- one `package` row per package plus one census row per total.

    Every input is a committed artifact; the identity is checked per package, so a package whose two sides do
    not close is a row with an `OFF` verdict rather than a number in the prose.
    """
    per = {}
    for r in reconcile:
        p = per.setdefault(r["package"], dict(fn=0, ev=0, op=0, opr=0, m=0))
        p["fn"] += number(r, "text_functions")
        p["ev"] += number(r, "text_events")
        p["op"] += number(r, "operators")
        p["opr"] += number(r, "operator_records")
        p["m"] += number(r, "matched")
    rec = {}
    for r in classes:
        rec[r["package"]] = rec.get(r["package"], 0) + number(r, "native_functions")
    whole = {r["package"]: number(r, "natives_in_source") for r in sources}
    for p in whole:
        per.setdefault(p, dict(fn=0, ev=0, op=0, opr=0, m=0))
    for p in rec:
        per.setdefault(p, dict(fn=0, ev=0, op=0, opr=0, m=0))
    block, line = {}, {}
    for r in comments:
        if r["kind"] != "class":
            continue
        block[r["package"]] = block.get(r["package"], 0) + number(r, "comment_natives")
        line[r["package"]] = line.get(r["package"], 0) + number(r, "line_comment_natives")
    only_record, only_decl = {}, {}
    for r in residual:
        if r.get("direction") == "record-only":
            only_record[r["package"]] = only_record.get(r["package"], 0) + 1
        elif r.get("direction") == "declaration-only":
            only_decl[r["package"]] = only_decl.get(r["package"], 0) + 1
    n_classes = {}
    for r in classes:
        n_classes[r["package"]] = n_classes.get(r["package"], 0) + 1

    out = []
    for pkg in sorted(per):
        p = per[pkg]
        records = rec.get(pkg, 0)
        ro, do = only_record.get(pkg, 0), only_decl.get(pkg, 0)
        side_record = p["m"] + p["opr"] + ro
        side_text = p["m"] + do + p["op"]
        closes = side_record == records and side_text == p["fn"] + p["ev"] + p["op"]
        out.append(dict(kind="package", package=pkg, classes=str(n_classes.get(pkg, 0)),
                        whole=str(whole.get(pkg, 0)), disabled_block=str(block.get(pkg, 0)),
                        disabled_line=str(line.get(pkg, 0)), live_functions=str(p["fn"]),
                        live_events=str(p["ev"]), live_operators=str(p["op"]), matched=str(p["m"]),
                        operator_records=str(p["opr"]), record_only=str(ro), declaration_only=str(do),
                        records=str(records),
                        verdict="closes" if closes else "OFF records %+d text %+d"
                        % (records - side_record, p["fn"] + p["ev"] + p["op"] - side_text),
                        count="1", note=""))
    tot = {k: sum(number(r, k) for r in out) for k in
           ("classes", "whole", "disabled_block", "disabled_line", "live_functions", "live_events",
            "live_operators", "matched", "operator_records", "record_only", "declaration_only", "records")}
    census = (("packages", len(out), "packages read"),
              ("whole", tot["whole"], "`native function` declarations in the whole buffer"),
              ("disabled_block", tot["disabled_block"], "of those, inside a `/* ... */`"),
              ("disabled_line", tot["disabled_line"], "of those, inside a `//` run"),
              ("live_functions", tot["live_functions"], "live `native function` declarations"),
              ("live_events", tot["live_events"], "live `native event` declarations"),
              ("live_operators", tot["live_operators"], "live `operator` statements"),
              ("matched", tot["matched"], "declarations that name a record, matched by name"),
              ("operator_records", tot["operator_records"],
               "records one live `operator` statement expands into"),
              ("declaration_only", tot["declaration_only"], "declarations with no record"),
              ("record_only", tot["record_only"], "records no declaration explains"),
              ("records", tot["records"], "native function records in `inventory/classes.csv`"),
              ("packages_closing", sum(1 for r in out if r["verdict"] == "closes"),
               "packages whose two sides close exactly"))
    for key, value, note in census:
        row = {k: "" for k in FIELDS}
        row.update(kind="census", count=str(value), note=note)
        row["verdict"] = key
        out.append(row)
    return out


def census_of(rows):
    return {r["verdict"]: r for r in rows if r["kind"] == "census"}


def render(rows):
    cen = census_of(rows)
    def n(key):
        return int(cen[key]["count"])
    pkgs = [r for r in rows if r["kind"] == "package"]
    out = []
    out.append("**The two counts close per record, not by arithmetic: %d packages of %d.** A package records more "
               "natives than its text declares when a live `operator` statement expands into several typed "
               "records, and fewer when a declaration has no record -- so `records = matched + operator-expanded "
               "+ record-only` and `declarations = matched + no-record + operator statements` are the two "
               "identities, and both hold on every package here."
               % (n("packages_closing"), n("packages")))
    out.append("")
    out.append("| measure | value |")
    out.append("|---|---:|")
    out.append("| `native function` declarations in the whole buffer | **%d** |" % n("whole"))
    out.append("| ... inside a `/* ... */` / inside a `//` run | **%d** / **%d** |"
               % (n("disabled_block"), n("disabled_line")))
    out.append("| live declarations (functions + events + operators) | **%d** + **%d** + **%d** |"
               % (n("live_functions"), n("live_events"), n("live_operators")))
    out.append("| declarations that name a record | **%d** |" % n("matched"))
    out.append("| records those `operator` statements expand into | **%d** |" % n("operator_records"))
    out.append("| declarations with no record | **%d** |" % n("declaration_only"))
    out.append("| records no declaration explains | **%d** |" % n("record_only"))
    out.append("| native function records | **%d** |" % n("records"))
    out.append("")
    out.append("| package | whole | `/* */` | `//` | live fn | events | operators | matched | "
               "expanded records | no record | no declaration | records |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in pkgs:
        if not (int(r["whole"]) or int(r["records"]) or int(r["live_operators"])):
            continue
        out.append("| `%s` | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                   % (r["package"], r["whole"], r["disabled_block"], r["disabled_line"],
                      r["live_functions"], r["live_events"], r["live_operators"], r["matched"],
                      r["operator_records"], r["declaration_only"] or "-", r["record_only"] or "-",
                      r["records"]))
    off = [r for r in pkgs if r["verdict"] != "closes"]
    out.append("")
    if off:
        out.append("**%d package(s) do not close:** %s." % (len(off),
                   ", ".join("`%s` (%s)" % (r["package"], r["verdict"]) for r in off)))
    else:
        out.append("**Every package closes both identities** -- `inventory/native_count_closure.csv` carries the "
                   "per-package row and its verdict, so a package that stops closing fails this block rather "
                   "than being averaged into a total.")
    return "\n".join(out)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice(path, block):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def self_check():
    bad = []
    reconcile = [
        {"package": "P", "text_functions": "3", "text_events": "1", "operators": "2",
         "operator_records": "4", "matched": "3"},
        {"package": "Q", "text_functions": "1", "text_events": "0", "operators": "0",
         "operator_records": "0", "matched": "1"},
    ]
    classes = [{"package": "P", "native_functions": "7"}, {"package": "Q", "native_functions": "1"}]
    sources = [{"package": "P", "natives_in_source": "5"}, {"package": "Q", "natives_in_source": "1"}]
    comments = [{"kind": "class", "package": "P", "comment_natives": "1", "line_comment_natives": "1"},
                {"kind": "class", "package": "Q", "comment_natives": "0", "line_comment_natives": "0"}]
    residual = [
        {"package": "P", "class": "C", "function": "Gone", "direction": "declaration-only"},
        {"package": "Q", "class": "D", "function": "Extra", "direction": "record-only"},
    ]
    rows = build(reconcile, residual, classes, sources, comments)
    p = {r["package"]: r for r in rows if r["kind"] == "package"}
    # P: 3 matched + 4 expanded + 0 record-only = 7 records; 3 matched + 1 no-record + 2 operators = 6
    # declarations = 3 functions + 1 event + 2 operators
    if p["P"]["records"] != "7" or p["P"]["verdict"] != "closes":
        bad.append("P did not close: %r" % (p["P"],))
    # Q: 1 matched + 0 expanded + 1 record-only = 2, which is not the 1 record it has -> must be OFF
    if p["Q"]["verdict"] == "closes":
        bad.append("a package whose record side is short read as closing")
    # the disabled half is carried, not folded into the live count
    if p["P"]["disabled_block"] != "1" or p["P"]["disabled_line"] != "1" or p["P"]["live_functions"] != "3":
        bad.append("the comment split did not reach the row: %r" % (p["P"],))
    cen = census_of(rows)
    if int(cen["packages_closing"]["count"]) != 1:
        bad.append("the closing tally counted a package that does not close")
    if int(cen["declaration_only"]["count"]) != 1 or int(cen["record_only"]["count"]) != 1:
        bad.append("the two leftover directions were summed together")
    block = render(rows)
    for want in ("1 packages of 2", "| `P` | 5 | 1 | 1 | 3 |", "do not close", "`Q`"):
        if want not in block:
            bad.append("render is missing %r" % want)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        write_artifact(rows, path)
        again = read_artifact(path)
        if [r["package"] for r in again] != [r["package"] for r in rows]:
            bad.append("artifact round trip differs")
        page = os.path.join(tmp, "page.md")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write("head\n" + DOC_START + "\nold\n" + DOC_END + "\ntail\n")
        if splice(page, "new") is None:
            bad.append("splice could not find its own markers")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL native-count-closure: " + "; ".join(bad))
        return 1
    print("ok   native-count-closure: both identities per package, a short record side read as OFF, the two "
          "leftover directions counted apart, the comment split carried, and a synthetic artifact round trip")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc or args.report:
        rows = read_artifact(ARTIFACT)
    else:
        rows = build(rows_of(RECONCILE), rows_of(RESIDUAL), rows_of(CLASSES), rows_of(SOURCES),
                     rows_of(COMMENTS))
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            path = os.path.join(args.out, "native_count_closure.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL native-count-closure: markers not found in %s" % path)
            return 1
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        if args.check_doc:
            if new != old:
                print("FAIL native-count-closure: %s is stale against the committed artifact" % path)
                return 1
            print("ok   native-count-closure: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
