#!/usr/bin/env python3
"""The two switch censuses, joined arm by arm -- U20.

A `Switch` is counted twice in this repo, from opposite ends of the same stream:

* `inventory/switch_operands.csv` (`tools/uscript-dis.py --switches`) walks the *token tree* forward from each
  `0x05` and counts the `Case` tokens it meets until the list's `0xffff` terminator. It is a scan, so a `Switch`
  inside a case body ends it early and that row's count is a **lower bound**.
* `inventory/jump_branches.csv` (`tools/jump-branches.py`) follows each `Case` operand's `0xffff`-terminated chain,
  which is `execSwitch`'s own reading of the list, so its list size is **exact**.

Both agree per site on almost the whole build, but they could not be compared until U20 was measured: the census
keyed a site by its export's **immediate outer** name while the jump census keyed it by the class it **walks to**,
so a state-declared body (`WindowConsole.UWindow.KeyEvent`) was one key in one file and two keys in the other, and
`UWindow.u`'s four `KeyEvent` exports split 2-and-1 across the pair. Both artifacts now carry `path` -- the class
path with the declaring state kept in it, `uscript-dis.export_path()` -- and this tool joins on `package`+`path`.

Within one function the sites and the lists are paired by offset order: a list begins at its first `Case`, which
follows its own `Switch`, and a nested switch sits inside its parent's body, so both orders run the same way. A
`nested-switch` row is `filled` when the chain's arm count is at least the scan's (the scan is a prefix of the
list), `agree` when a terminated row's count equals the list's arm count, and `differ` otherwise: the two walks
must agree everywhere once the key is one key, so a single `differ` or `unpaired` row fails `--check`.

    python3 tools/switch-join.py -o inventory/switch_join.csv --write-doc docs/BYTECODE.md

`--check` needs no dump: it rebuilds the join from the two committed censuses and byte-compares it with the
committed artifact, then refuses any row the two walks disagree on.
"""
import argparse
import collections
import csv
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWITCH_CSV = os.path.join(ROOT, "inventory", "switch_operands.csv")
JUMP_CSV = os.path.join(ROOT, "inventory", "jump_branches.csv")
OUT_CSV = os.path.join(ROOT, "inventory", "switch_join.csv")
DOC_MARK = ("<!-- generated:switch-join -->", "<!-- /generated:switch-join -->")
COLUMNS = ("package", "path", "site", "offset", "outer", "census", "chain", "verdict", "note")


def read_csv(path):
    if not os.path.exists(path):
        sys.exit("switch-join: %s does not exist" % path)
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def join(census, jumps):
    """`(rows, stats)` -- one row per switch site and per unpaired side, in file order."""
    per_c, per_j = collections.defaultdict(list), collections.defaultdict(list)
    for r in census:
        per_c[(r["package"], r["path"].strip())].append(r)
    no_list = 0
    for r in jumps:
        if r.get("kind") != "switch":
            continue
        if int(r["count"]) == 0:                     # `jump-branches`' own "no case list" row: nothing to pair with
            no_list += 1
            continue
        per_j[(r["package"], r["path"].strip())].append(r)
    rows = []
    for key in sorted(set(per_c) | set(per_j)):
        c = sorted(per_c.get(key, []), key=lambda r: int(r["offset"], 16))
        j = sorted(per_j.get(key, []), key=lambda r: int(r["offset"], 16))
        for i in range(max(len(c), len(j))):
            one, two = (c[i] if i < len(c) else None), (j[i] if i < len(j) else None)
            site, package, path = i + 1, key[0], key[1]
            if one is None:
                rows.append({"package": package, "path": path, "site": site, "offset": "0x" + two["offset"],
                             "outer": "", "census": "", "chain": int(two["count"]) - 1, "verdict": "unpaired",
                             "note": "a case list with no `Switch` site"})
                continue
            arms = int(one["cases"])
            outer = one["class"]
            if two is None:
                rows.append({"package": package, "path": path, "site": site, "offset": one["offset"],
                             "outer": outer, "census": arms, "chain": "", "verdict": "unpaired",
                             "note": "a `Switch` site with no case list"})
                continue
            chain = int(two["count"]) - 1
            if one["ended"] == "nested-switch":
                # The forward scan stops at a `Switch` inside an arm, so its count is a prefix of the list; the chain
                # is what the handler reads. `filled` says the chain completes it, `held` that the bound was already
                # the whole list, `differ` that the chain is shorter than the scan.
                verdict = "filled" if chain > arms else ("held" if chain == arms else "differ")
                note = {"filled": "the scan stopped at a nested switch after %d arm(s); the chain reaches %d"
                        % (arms, chain),
                        "held": "the scan stopped at a nested switch after %d arm(s), which is the whole list" % arms,
                        "differ": "the scan counted %d arm(s) but the chain reaches only %d" % (arms, chain)}[verdict]
            else:
                verdict = "agree" if chain == arms else "differ"
                note = ("" if verdict == "agree" else
                        "the scan counted %d arm(s), the chain %d" % (arms, chain))
            rows.append({"package": package, "path": path, "site": site, "offset": one["offset"], "outer": outer,
                         "census": arms, "chain": chain, "verdict": verdict, "note": note})
    verdicts = collections.Counter(r["verdict"] for r in rows)
    filled = [r for r in rows if r["verdict"] == "filled"]
    held = [r for r in rows if r["verdict"] == "held"]
    state_owned = [r for r in rows if r["outer"] and r["outer"] != r["path"].split(".")[-2]]
    census_arms = sum(int(r["census"]) for r in rows if r["census"] != "")
    chain_arms = sum(int(r["chain"]) for r in rows if r["chain"] != "")
    added = sum(int(r["chain"]) - int(r["census"]) for r in filled)
    stats = {
        "rows": len(rows),
        "sites": len([r for r in rows if r["census"] != ""]),
        "functions": len({(r["package"], r["path"]) for r in census}),
        "agree": verdicts["agree"], "filled": verdicts["filled"], "held": verdicts["held"],
        "differ": verdicts["differ"], "unpaired": verdicts["unpaired"],
        "census_arms": census_arms, "chain_arms": chain_arms, "added": added,
        "no_list": no_list,
        "filled_rows": filled, "held_rows": held,
        "state_owned": state_owned,
    }
    return rows, stats


def render(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in COLUMNS})
    return out.getvalue()


def doc_block(rows, stats):
    """The page's block, rendered from `inventory/switch_join.csv` alone: every figure is a count of its rows."""
    filled, held = stats["filled_rows"], stats["held_rows"]
    identity = stats["census_arms"] + stats["added"] == stats["chain_arms"]
    lines = ["**The two switch censuses join arm by arm.** **%d** `Switch` sites over **%d** functions pair with "
             "**%d** case lists: **%d** agree on the arm count, **%d** are the forward scan's lower bounds and the "
             "`Case` chain fills them, **%d** lower bound(s) the chain confirms were already whole, **%d** disagree "
             "and **%d** side is unpaired."
             % (stats["sites"], stats["functions"], stats["sites"], stats["agree"], stats["filled"], stats["held"],
                stats["differ"], stats["unpaired"])]
    lines += ["",
              "| | arms |", "|---|---:|",
              "| the forward scan counts | %d |" % stats["census_arms"],
              "| the chain adds on the filled rows | +%d |" % stats["added"],
              "| the chain counts | **%d** |" % stats["chain_arms"]]
    if identity:
        lines += ["", "**%d + %d = %d exactly**, so the %d rows the scan could only bound are the whole of the "
                  "difference between the two walks -- no site's count moved for any other reason."
                  % (stats["census_arms"], stats["added"], stats["chain_arms"], len(filled) + len(held))]
    if filled:
        lines += ["", "| filled site | `Switch` at | forward scan | chain |", "|---|---|---:|---:|"]
        for r in filled:
            lines.append("| `%s` | %s | %d | %d |" % (r["path"], r["offset"], int(r["census"]), int(r["chain"])))
    if held:
        lines += ["", "The %d bound(s) the chain did not extend are %s: the switch carries a single case, so the "
                  "nested switch the scan met sits inside that arm's body and there was no further case to reach."
                  % (len(held), ", ".join("`%s`" % r["path"] for r in held))]
    if stats["state_owned"]:
        lines += ["", "**%d** site(s) name an outer that is not their class -- the state-declared bodies, which are "
                  "the population the old `class`+`function` key could not separate: %s."
                  % (len(stats["state_owned"]),
                     ", ".join("`%s`" % r["path"] for r in stats["state_owned"][:6]))]
    lines += ["", "The rows are `inventory/switch_join.csv` (`tools/switch-join.py`), a dump-free join of "
              "`inventory/switch_operands.csv` with `inventory/jump_branches.csv` on `package`+`path`; "
              "`--check-doc` re-renders this block from the committed artifact, so `make ci` holds the page to it "
              "without the dump."]
    return "\n".join(lines)


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        current = fh.read()
    begin, end = DOC_MARK
    i, j = current.find(begin), current.find(end)
    if i < 0 or j <= i:
        sys.exit("switch-join: %s has no %s ... %s block" % (path, begin, end))
    return current[: i + len(begin)] + "\n" + block + "\n" + current[j:]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=OUT_CSV)
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--check", action="store_true", help="rebuild the join from the two censuses and compare")
    ap.add_argument("--report", action="store_true", help="print the join, write nothing")
    args = ap.parse_args()

    rows, stats = join(read_csv(SWITCH_CSV), read_csv(JUMP_CSV))
    fresh = render(rows)
    if args.check or args.check_doc:
        bad = [r for r in rows if r["verdict"] in ("differ", "unpaired")]
        if bad:
            print("FAIL switch-join: %d site(s) the two walks do not agree on, first `%s` at %s (%s)"
                  % (len(bad), bad[0]["path"], bad[0]["offset"], bad[0]["note"]))
            return 1
        if stats["census_arms"] + stats["added"] != stats["chain_arms"]:
            print("FAIL switch-join: the scan's %d arms plus the %d the chain adds do not make its %d, so a site's "
                  "count moved for a reason the filled rows do not name"
                  % (stats["census_arms"], stats["added"], stats["chain_arms"]))
            return 1
    if args.check:
        if not os.path.exists(args.out):
            print("FAIL switch-join: %s does not exist" % args.out)
            return 1
        with open(args.out, newline="") as fh:
            head = next(csv.reader(fh))
        if tuple(head) != COLUMNS:
            print("FAIL switch-join: %s header %s" % (args.out, head))
            return 1
        with open(args.out, encoding="utf-8") as fh:
            committed = fh.read()
        if committed != fresh:
            have = {(r["package"], r["path"], r["site"]): r for r in csv.DictReader(io.StringIO(committed))}
            want = {(r["package"], r["path"], r["site"]): r for r in csv.DictReader(io.StringIO(fresh))}
            differ = [k for k in set(have) | set(want) if have.get(k) != want.get(k)]
            print("FAIL switch-join: %s does not reproduce from the two censuses (%d row(s) differ of %d)"
                  % (args.out, len(differ), len(want)))
            return 1
        print("ok   switch-join: %d sites, %d agree, %d filled, %d unpaired"
              % (stats["sites"], stats["agree"], stats["filled"], stats["unpaired"]))
        return 0
    if args.check_doc:
        block = doc_block(rows, stats)
        with open(args.check_doc, encoding="utf-8") as fh:
            current = fh.read()
        if splice(args.check_doc, block) != current:
            print("FAIL %s: the generated switch-join block is stale" % args.check_doc)
            return 1
        print("ok   switch-join: %s matches its rows" % args.check_doc)
        return 0
    if args.write_doc:
        fresh_page = splice(args.write_doc, doc_block(rows, stats))
        with open(args.write_doc, "w", encoding="utf-8") as fh:
            fh.write(fresh_page)
        print("ok   switch-join: wrote %s" % args.write_doc)
    if args.report or not args.write_doc:
        for r in rows:
            if r["verdict"] != "agree":
                print("%-9s %-40s site %-2s at %-7s scan %-3s chain %-3s %s"
                      % (r["verdict"], r["path"], r["site"], r["offset"], r["census"], r["chain"], r["note"]))
        print("%d sites: %d agree, %d filled, %d differ, %d unpaired; arms scan %d, chain %d, no-list %d"
              % (stats["sites"], stats["agree"], stats["filled"], stats["differ"], stats["unpaired"],
                 stats["census_arms"], stats["chain_arms"], stats["no_list"]))
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(fresh)
    print("wrote %s (%d rows)" % (args.out, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
