#!/usr/bin/env python3
"""Why the reconstruction's operator multiset is short: which text region the tokens sit in (PLAN 3.5).

`tools/decompiler-operators.py` counts operator tokens in two trees and reports a deficit -- the source column
carries `==` 6,433 against the reconstruction's 5,579. That comparison is text against text, and both trees carry
tokens that are not operators in code. This tool splits every class's two texts into three regions and counts the
operators in each, so the deficit is attributed instead of explained:

    head     -- the fragment before the first `*/` **when the extracted `ScriptText` starts inside a block
                comment**. `pkg-script-text.script_text` locates the text by its first `class ` or `//` token, and
                the class banner is a `/* ... */` block whose middle lines often begin with `//`, so the extractor
                starts the text mid-comment and the banner's closing `====...` run is left outside any comment.
                `clean()` then counts that run's `=` characters as `==` tokens.
    defaults -- the `defaultproperties { ... }` block. The reconstruction re-renders the defaults from the class
                record, so it carries negative numbers the shipped text may not carry at all (the record holds a
                defaults block for classes whose `ScriptText` does not).
    code     -- everything else, with preprocessor conditionals collapsed to the branch this build compiles
                (`#ifdefXBOX` live, `#ifndefXBOX` dead, `#ifdefPC`/`#ifdefPS2`/`#ifndefPS2`/`#ifdefDEBUG` dead, an
                unknown directive keeps both branches and is counted in `unknown_directives`).

    python3 tools/decompiler-operator-regions.py DUMP/System $WORK/UnrealScript-patched -o inventory
    python3 tools/decompiler-operator-regions.py --check       # synthetic trees, no dump; in `make ci`

`inventory/decompiler_operator_regions.csv` carries one row per class (`head_class`, the operator count of each
region, and the code-only delta), one row per operator, and a census row. Reads two trees; no dump file is opened
by `--check`, and nothing about the game is printed.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

OUT = os.path.join(ROOT, "inventory", "decompiler_operator_regions.csv")

# Operators and the cleaner are the sibling census's, so the two artifacts cannot drift apart in what a token is.
OPS_MODULE = None


def sibling():
    """`tools/decompiler-operators.py` -- hyphenated, so it is imported by path."""
    global OPS_MODULE
    if OPS_MODULE is None:
        path = os.path.join(HERE, "decompiler-operators.py")
        spec = importlib.util.spec_from_file_location("decompiler_operators", path)
        OPS_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(OPS_MODULE)
    return OPS_MODULE


REGIONS = [side + "_" + region for side in ("source", "recon")
           for region in ("head", "defaults", "code", "dead")]

DIRECTIVE = re.compile(r"^[ \t]*#[ \t]*(\w+)(.*)$")
DEFAULT_LINE = re.compile(r"^[ \t]*defaultproperties[ \t]*\{?", re.M)
LIVE = {"ifdefxbox": True, "ifndefxbox": False, "ifdefpc": False, "ifdefps2": False,
        "ifndefps2": False, "ifdefdebug": False}


def regions(text):
    """(head, body) -- the fragment before the first `*/` when the text starts inside a block comment."""
    close = text.find("*/")
    open_at = text.find("/*")
    if close >= 0 and (open_at < 0 or open_at > close):
        return text[:close + 2], text[close + 2:]
    return "", text


def defaults_span(body):
    """(start, end) of the `defaultproperties { ... }` block, or (-1, -1)."""
    m = DEFAULT_LINE.search(body)
    if not m:
        return -1, -1
    brace = body.find("{", m.start())
    if brace < 0:
        return -1, -1
    depth = 0
    for i in range(brace, len(body)):
        ch = body[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return m.start(), i + 1
    return m.start(), len(body)


def xbox_code(body, unknown):
    """The body with only the lines this build compiles: preprocessor conditionals collapsed."""
    out, stack = [], []
    for line in body.splitlines():
        m = DIRECTIVE.match(line)
        if not m:
            if all(one[0] for one in stack):
                out.append(line)
            continue
        word = m.group(1).lower()
        if word in LIVE:
            stack.append([LIVE[word], False])
        elif word == "else" and stack:
            stack[-1][0] = not stack[-1][0]
            stack[-1][1] = True
        elif word == "endif" and stack:
            stack.pop()
        elif word == "exec":
            continue          # an editor-only import directive, not a conditional
        else:
            unknown[word] += 1
            stack.append([True, False])
    return "\n".join(out)


def mask(text):
    """Blank comments and string literals in place, keeping every offset and every line break.

    The sibling census's `clean()` deletes them, which is the same multiset of operator tokens but loses the
    positions the three regions are cut at; blanking keeps a region's slice the region itself.
    """
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    mod = sibling()
    # Comments first, then literals -- the sibling census's order. An apostrophe in a comment ("don't") would
    # otherwise open a phantom literal that blanks the code after it.
    return mod.LITERAL.sub(blank, mod.COMMENT.sub(blank, text))


def counts(text):
    """The per-operator counts of a text that is already masked."""
    per = {op: 0 for op in sibling().OPS}
    for op in sibling().OPERATOR.findall(text):
        per[op] = per.get(op, 0) + 1
    return per


def split(text, unknown):
    """The four regions of one class's text, each with its per-operator counts.

    `head + defaults + code + dead` is the whole text, token for token, so the artifact reconciles against the
    sibling census's operator rows: `dead` is the difference the preprocessor collapse makes. The regions are
    cut on the masked text, so a `defaultproperties` block inside a comment is not counted twice.
    """
    head, body = regions(text)
    masked = mask(text)
    at = len(head)
    start, end = defaults_span(body)
    if start >= 0:
        first, last = at + start, at + end
        pieces = {"head": masked[:at], "defaults": masked[first:last],
                  "code": masked[at:first] + masked[last:]}
    else:
        pieces = {"head": masked[:at], "defaults": "", "code": masked[at:]}
    full, live = counts(pieces["code"]), counts(xbox_code(pieces["code"], unknown))
    dead = {op: max(0, full[op] - live[op]) for op in full}
    return {"head": counts(pieces["head"]), "defaults": counts(pieces["defaults"]),
            "code": live, "dead": dead, "head_text": head, "defaults_text": pieces["defaults"]}


def totals(ops, region):
    return {op: sum(one[region][op] for one in ops.values()) for op in sibling().OPS}


def delta(a, b):
    """`==:+32;-:-2` for the operators where two per-operator maps disagree."""
    parts = []
    for op in sibling().OPS:
        if a[op] != b[op]:
            parts.append("%s:%+d" % (op, a[op] - b[op]))
    return ";".join(parts)


def head_top(per_op):
    parts = ["%s:%d" % (op, n) for op, n in sorted(per_op.items(), key=lambda kv: (-kv[1], kv[0])) if n]
    return ";".join(parts)


def run(dump, tree, out=None):
    from ue2pkg import Package                              # noqa: PLC0415 - a real package
    path = os.path.join(HERE, "pkg-script-text.py")
    spec = importlib.util.spec_from_file_location("pkg_script_text", path)
    script_text = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script_text)

    files = ([os.path.join(dump, f) for f in sorted(os.listdir(dump)) if f.lower().endswith(".u")]
             if os.path.isdir(dump) else [dump])
    unknown = collections.Counter()
    rows, per_class = [], {}
    total = {k: collections.Counter() for k in REGIONS}
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = os.path.splitext(os.path.basename(one))[0]
        for index, export in enumerate(pkg.exports):
            if pkg.class_name_of(export) != "Class":
                continue
            cls = pkg.name(export["name"])
            path = os.path.join(tree, pkg_name, cls + ".uc")
            text = script_text.script_text(pkg, dict(export, index=index))
            if not text or not os.path.exists(path):
                continue
            src = split(text, unknown)
            rec = split(open(path, encoding="latin1", errors="replace").read(), unknown)
            for kind, one_side in (("source", src), ("recon", rec)):
                for region in ("head", "defaults", "code", "dead"):
                    for op, n in one_side[region].items():
                        total["%s_%s" % (kind, region)][op] += n
            per_class[(pkg_name, cls)] = (src, rec)
            row = {"kind": "class", "package": pkg_name, "class": cls,
                   "head_class": 1 if src["head_text"] else 0,
                   "source_head_ops": sum(src["head"].values()),
                   "source_head_top": head_top(src["head"]),
                   "source_defaults_ops": sum(src["defaults"].values()),
                   "recon_defaults_ops": sum(rec["defaults"].values()),
                   "source_code_ops": sum(src["code"].values()),
                   "recon_code_ops": sum(rec["code"].values()),
                   "source_dead_ops": sum(src["dead"].values()),
                   "recon_dead_ops": sum(rec["dead"].values()),
                   "code_delta": delta(src["code"], rec["code"])}
            rows.append(row)
    for op in sibling().OPS:
        rows.append({"kind": "operator", "package": "operator", "class": op,
                     "source_head_ops": total["source_head"][op],
                     "source_defaults_ops": total["source_defaults"][op],
                     "source_code_ops": total["source_code"][op],
                     "recon_head_ops": total["recon_head"][op],
                     "recon_defaults_ops": total["recon_defaults"][op],
                     "recon_code_ops": total["recon_code"][op],
                     "source_dead_ops": total["source_dead"][op],
                     "recon_dead_ops": total["recon_dead"][op]})
    census = {"kind": "census", "package": "census", "class": "census",
              "head_class": sum(1 for r in rows if r.get("head_class") == 1),
              "classes": len(per_class),
              "unknown_directives": ";".join("%s:%d" % kv for kv in sorted(unknown.items()))}
    for key, counter in total.items():
        census[key + "_ops"] = sum(counter.values())
    census["source_code_delta"] = delta(total["source_code"], total["recon_code"])
    rows.append(census)
    if out:
        write_csv(rows, out)
    return rows, total, per_class


FIELDS = ["kind", "package", "class", "head_class", "classes", "unknown_directives",
          "source_head_ops", "source_head_top", "source_defaults_ops", "source_code_ops", "source_dead_ops",
          "recon_head_ops", "recon_defaults_ops", "recon_code_ops", "recon_dead_ops",
          "code_delta", "source_code_delta"]


def write_csv(rows, path):
    with open(path, "w", newline="") as fh:
        out = csv.DictWriter(fh, fieldnames=FIELDS, restval="")
        out.writeheader()
        for row in rows:
            out.writerow(row)


def report(rows, total):
    census = rows[-1]
    print("classes: %d; start inside a comment: %d; unknown directives: %s"
          % (census["classes"], census["head_class"], census["unknown_directives"] or "none"))
    print("%-4s %8s %8s %8s %8s %8s %8s %8s %8s %8s" %
          ("op", "src head", "src deflt", "src dead", "src code", "src all",
           "rec deflt", "rec dead", "rec code", "rec all"))
    for row in rows:
        if row["kind"] != "operator":
            continue
        print("%-4s %8d %8d %8d %8d %8d %8d %8d %8d %8d" %
              (row["class"], row["source_head_ops"], row["source_defaults_ops"], row["source_dead_ops"],
               row["source_code_ops"], row["source_head_ops"] + row["source_defaults_ops"] +
               row["source_dead_ops"] + row["source_code_ops"],
               row["recon_defaults_ops"], row["recon_dead_ops"], row["recon_code_ops"],
               row["recon_head_ops"] + row["recon_defaults_ops"] + row["recon_dead_ops"] +
               row["recon_code_ops"]))
    print("code-only delta: %s" % (census["source_code_delta"] or "none"))
    worst = sorted((r for r in rows if r["kind"] == "class" and r["code_delta"]),
                   key=lambda r: -abs(sum(int(p.split(":")[1]) for p in r["code_delta"].split(";"))))[:8]
    for row in worst:
        print("  %-18s %-40s %s" % (row["package"], row["class"], row["code_delta"]))


# --- the dump-free check ------------------------------------------------------------------------------------

HEAD_CLASS = "// the line the extractor starts on\n=========================== */\nclass A extends B;\n"
PLAIN_CLASS = "class C extends B;\nvar int N;\n"
DEFAULTS_CLASS = ("class D extends B;\n"
                  "function int F()\n{\n\tif (N == 0 && N != 1)\n\t\tN = 2 * 3;\n\treturn N;\n}\n"
                  "defaultproperties\n{\n\tN=-4\n\tStep=-1.5\n}\n")
GUARDED_CLASS = ("class E extends B;\n"
                 "#ifdefXBOX\nfunction int G()\n{\n\treturn 1;\n}\n#else\nfunction int G()\n{\n\treturn 2;\n}\n"
                 "#endif\n"
                 "#ifdefPC\nfunction int H()\n{\n\treturn 3 == 3;\n}\n#endif\n")


def self_check():
    """Six claims the artifact rests on, driven on synthetic texts -- no dump, no tree, nothing written."""
    problems = []
    unknown = collections.Counter()

    head, body = regions(HEAD_CLASS)
    if not head.endswith("*/") or "class A" not in body:
        problems.append("regions: a mid-comment start is not cut at its own `*/`")
    if regions(PLAIN_CLASS)[0]:
        problems.append("regions: a text that starts at `class` is reported as a comment fragment")
    if counts(head)["=="] != 13:
        problems.append("regions: the banner run is not the 13 `==` a run of 27 `=` makes "
                        "(got %s)" % head_top(counts(head)))
    if counts(mask(head))["*"] != 1 or counts(mask(head))["/"] != 1:
        problems.append("regions: the banner's own `*/` is not the one `*` and one `/` it is")

    start, end = defaults_span(body)
    if start != -1:
        problems.append("defaults: a body with no block answered a span")
    _, plain_body = regions(DEFAULTS_CLASS)
    start, end = defaults_span(plain_body)
    one = split(DEFAULTS_CLASS, unknown)
    if start < 0 or "N=-4" not in plain_body[start:end]:
        problems.append("defaults: the block is not cut at its own braces")
    if one["defaults"]["-"] != 2:
        problems.append("defaults: the two negative values are not two `-` tokens (got %d)"
                        % one["defaults"]["-"])
    if one["code"]["=="] != 1 or one["code"]["&&"] != 1 or one["code"]["!="] != 1:
        problems.append("code: the three comparisons of the function body are not three tokens (got %s)"
                        % head_top(one["code"]))
    if one["code"]["*"] != 1:
        problems.append("code: the multiply is not one token")

    guarded = split(GUARDED_CLASS, unknown)
    if guarded["code"]["=="] != 0:
        problems.append("directives: a `#ifdefPC` branch is counted as live code")
    if unknown:
        problems.append("directives: a known directive was reported unknown (%s)" % dict(unknown))
    two = split("#ifdefWHAT\nclass F;\n#endif\n", unknown)
    if "ifdefwhat" not in unknown:
        problems.append("directives: an unknown directive is not reported")

    # The row-level claims: operator rows sum to the census, and class rows to the operator rows.
    rows, total, per_class = run_synthetic()
    census = rows[-1]
    for region in ("source_head", "source_defaults", "source_code", "recon_code"):
        summed = sum(r[region + "_ops"] for r in rows if r["kind"] == "operator")
        if summed != census[region + "_ops"]:
            problems.append("artifact: %s rows sum to %d, the census says %d"
                            % (region, summed, census[region + "_ops"]))
    if census["head_class"] != 1:
        problems.append("artifact: the synthetic tree has one mid-comment class, the census says %d"
                        % census["head_class"])

    # The committed artifact against the shipped census: the four regions must account for every token that
    # census counted, per operator and on both trees -- the independent claim that this split covers the text.
    shipped_csv = os.path.join(ROOT, "inventory", "decompiler_operators.csv")
    if os.path.exists(OUT) and os.path.exists(shipped_csv):
        shipped = {r["class"]: r for r in csv.DictReader(open(shipped_csv)) if r["kind"] == "operator"}
        for row in (r for r in csv.DictReader(open(OUT)) if r["kind"] == "operator"):
            op = row["class"]
            if op not in shipped:
                problems.append("artifact: the census has no `%s` row to reconcile against" % op)
                continue
            for side, column in (("source", "source_operators"), ("recon", "decompiled_operators")):
                summed = sum(int(row[side + "_" + one + "_ops"] or 0)
                             for one in ("head", "defaults", "dead", "code"))
                want = int(shipped[op][column] or 0)
                if summed != want:
                    problems.append("artifact: `%s` %s regions sum to %d, the census counted %d"
                                    % (op, side, summed, want))
    return problems


def run_synthetic():
    """The row shape of `run()`, over four synthetic texts: mid-comment, plain, defaults, guarded."""
    return _fake(HEAD_CLASS, PLAIN_CLASS, DEFAULTS_CLASS, GUARDED_CLASS)


def _fake(*texts):
    """A synthetic two-row run: `run()`'s row shape over texts, with no package on disk."""
    per_class = {}
    total = {k: collections.Counter() for k in REGIONS}
    unknown = collections.Counter()
    rows = []
    for i, text in enumerate(texts):
        src = split(text, unknown)
        rec = split(text, unknown)
        for kind, side in (("source", src), ("recon", rec)):
            for region in ("head", "defaults", "code", "dead"):
                for op, n in side[region].items():
                    total["%s_%s" % (kind, region)][op] += n
        rows.append({"kind": "class", "package": "Synth", "class": "S%d" % i,
                     "head_class": 1 if src["head_text"] else 0,
                     "source_head_ops": sum(src["head"].values()),
                     "source_head_top": head_top(src["head"]),
                     "source_defaults_ops": sum(src["defaults"].values()),
                     "recon_defaults_ops": sum(rec["defaults"].values()),
                     "source_code_ops": sum(src["code"].values()),
                     "recon_code_ops": sum(rec["code"].values()),
                     "source_dead_ops": sum(src["dead"].values()),
                     "recon_dead_ops": sum(rec["dead"].values()),
                     "code_delta": delta(src["code"], rec["code"])})
    for op in sibling().OPS:
        rows.append({"kind": "operator", "package": "operator", "class": op,
                     "source_head_ops": total["source_head"][op],
                     "source_defaults_ops": total["source_defaults"][op],
                     "source_code_ops": total["source_code"][op],
                     "recon_head_ops": total["recon_head"][op],
                     "recon_defaults_ops": total["recon_defaults"][op],
                     "recon_code_ops": total["recon_code"][op],
                     "source_dead_ops": total["source_dead"][op],
                     "recon_dead_ops": total["recon_dead"][op]})
    census = {"kind": "census", "package": "census", "class": "census",
              "head_class": sum(1 for r in rows if r.get("head_class") == 1),
              "classes": len(texts),
              "unknown_directives": ";".join("%s:%d" % kv for kv in sorted(unknown.items()))}
    for key, counter in total.items():
        census[key + "_ops"] = sum(counter.values())
    census["source_code_delta"] = delta(total["source_code"], total["recon_code"])
    rows.append(census)
    return rows, total, per_class


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dump", nargs="?", help="a dump System directory, or one .u")
    ap.add_argument("tree", nargs="?", help="a decompiled tree ($WORK/UnrealScript-...)")
    ap.add_argument("-o", "--out", help="write the artifact (a directory, or a .csv path)")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    args = ap.parse_args()
    if args.check:
        problems = self_check()
        for one in problems:
            print("FAIL decompiler-operator-regions: %s" % one)
        print("decompiler-operator-regions: %s" % ("ok" if not problems else "%d problem(s)" % len(problems)))
        return 1 if problems else 0
    if not args.dump or not args.tree:
        ap.error("a dump and a tree are required unless --check is given")
    out = None
    if args.out:
        out = args.out if args.out.endswith(".csv") else os.path.join(args.out, os.path.basename(OUT))
    rows, total, per_class = run(args.dump, args.tree, out)
    report(rows, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
