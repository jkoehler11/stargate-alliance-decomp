#!/usr/bin/env python3
"""Read each class table's slot bodies for the source file they assert in (U40's last name channel).

The 18 class tables no registration names are the residue of `inventory/xbe_class_table_names.csv`,
and three routes have been measured shut: the registration literal (the site pushes a shared default
constructor), the installer body (0 of 26 carry a string at all) and the registration's size argument
(every size in the tree originates at a registration, so the join is circular). The slot bodies were
never read this way, and they carry something the installers do not: **the map names a body after the
assert path it inlines** (`Engine_UnChan`, `Core_UnObj`), so a table's slots vote for a `.cpp` file.

This tool tallies that vote per table. It is *evidence*, not a naming rule, and the control says why:
only 13 of the 474 named tables carry a token that starts with their own class name, so a token
corroborates a class family (`UnConn` for the connection hierarchy) without naming one. Its value is
in the tokens no other table carries: a token on two tables is a base class and its child sharing an
inherited slot body, and that pair is the read.

    python3 tools/class-slot-tokens.py --xbe DUMP/default.xbe -o inventory
    python3 tools/class-slot-tokens.py --report
    python3 tools/class-slot-tokens.py --write-doc docs/systems/CLASS_REGISTRATION.md

The extents are `inventory/xbe_class_tables.csv`'s -- the body-start rule -- so this read cannot
disagree with the artifact about which dwords are slots. Addresses, counts and file names only.
"""
import argparse
import collections
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

OUT = os.path.join("inventory", "class_slot_tokens.csv")
TABLES = os.path.join(ROOT, "inventory", "xbe_class_tables.csv")
NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
SRC = os.path.join(ROOT, "inventory", "class_slot_tokens.csv")
DOC_START = "<!-- generated:class-slot-tokens -->"
DOC_END = "<!-- /generated:class-slot-tokens -->"
FIELDS = ("table_va", "class", "slots", "classed_slots", "tokens", "distinctive", "top_token",
          "top_slots", "top_df", "named_by_class")
RARE = 8          # a token on at most this many of the 492 tables is distinctive, not the base rate
MODULES = ("Core", "Engine", "IpDrv", "Gui", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive", "Fire",
           "Gameplay", "stargategame", "stargatevehicles")
HEX = re.compile(r"^(0x)?[0-9a-fA-F]+$")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def key(text):
    return "%#010x" % int(text, 16) if HEX.match(text or "") else (text or "")


def token(symbol):
    """The `.cpp` file a body's assert-derived name carries, or ''.

    The symbol map names an unnamed body `<Module>_<File>` from the assert path it inlines
    (`Engine_UnChan` is `...\\Engine\\Src\\UnChan.cpp`). `FUN_*`, `thunk_*` and the MSVC library
    names carry no module prefix and answer ''.
    """
    if not symbol or "_" not in symbol or symbol.startswith(("FUN_", "thunk_")):
        return ""
    mod, _, rest = symbol.partition("_")
    return rest if mod in MODULES and rest else ""


def load_symbols(path):
    names = {}
    for row in read_csv(path):
        try:
            names[int(row["address"], 16)] = row["name"]
        except (KeyError, ValueError):
            continue
    return names


def slot_files(slots, names):
    """The token counts over one table's slot bodies."""
    counts = collections.Counter()
    classed = 0
    for va in slots:
        tok = token(names.get(va, ""))
        if tok:
            counts[tok] += 1
            classed += 1
    return counts, classed


def rows_of(tables, classes, names, image, tablemod):
    """One row per table, in address order."""
    vectors = {}
    for tva, n in tables.items():
        vectors[tva] = tablemod.read_entries(image, tva)[:n]
    per = {tva: slot_files(vector, names) for tva, vector in vectors.items()}
    df = collections.Counter()
    for counts, _ in per.values():
        for tok in counts:
            df[tok] += 1
    out = []
    for tva in sorted(vectors):
        counts, classed = per[tva]
        cls = classes.get(tva, "")
        rare = sorted(((t, n) for t, n in counts.items() if df[t] <= RARE),
                      key=lambda kv: (df[kv[0]], -kv[1], kv[0]))
        top = max(counts.items(), key=lambda kv: (kv[1], -df[kv[0]])) if counts else ("", 0)
        out.append({
            "table_va": "%#010x" % tva,
            "class": cls,
            "slots": str(len(vectors[tva])),
            "classed_slots": str(classed),
            "tokens": " ".join("%s:%d" % kv for kv in
                               sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "distinctive": " ".join("%s:%d" % kv for kv in rare),
            "top_token": top[0],
            "top_slots": str(top[1]),
            "top_df": str(df[top[0]]) if top[0] else "",
            "named_by_class": "yes" if cls and any(cls.lower() in t.lower() for t in counts) else "no",
        })
    return out


def summary(rows):
    """The numbers the page quotes, so they come from the artifact and not from prose."""
    unnamed = [r for r in rows if not r["class"]]
    named = [r for r in rows if r["class"]]
    return {
        "tables": len(rows),
        "with_token": sum(1 for r in rows if r["classed_slots"] != "0"),
        "named": len(named),
        "named_by_class": sum(1 for r in named if r["named_by_class"] == "yes"),
        "unnamed": len(unnamed),
        "unnamed_with_token": sum(1 for r in unnamed if r["classed_slots"] != "0"),
        "unnamed_rare": [r for r in unnamed if r["distinctive"]],
    }


def render(rows):
    s = summary(rows)
    lines = []
    lines.append("**%d of the %d class tables carry an assert-file token on at least one slot "
                 "(%d of the %d named, %d of the %d unnamed).**"
                 % (s["with_token"], s["tables"], s["with_token"] - s["unnamed_with_token"],
                    s["named"], s["unnamed_with_token"], s["unnamed"]))
    lines.append("")
    if not s["unnamed_rare"]:
        lines.append("No unnamed table carries a token that is rare across the 492.")
        return "\n".join(lines)
    df = collections.Counter()
    for r in rows:
        for tok in r["tokens"].split():
            df[tok.partition(":")[0]] += 1
    lines.append("| table | slots | distinctive slot tokens (count, tables carrying the token) |")
    lines.append("|---|---|---|")
    for r in s["unnamed_rare"]:
        bits = []
        for tok in r["distinctive"].split():
            name, _, n = tok.partition(":")
            bits.append("`%s` %s (%d table(s))" % (name, n, df[name]))
        lines.append("| `%s` | %s | %s |" % (r["table_va"], r["slots"], ", ".join(bits)))
    lines.append("")
    lines.append("**%d of the %d named tables carry a token that starts with their own class name**, "
                 "so a token corroborates a class family and does not name a class."
                 % (s["named_by_class"], s["named"]))
    return "\n".join(lines)


def block_from_csv(path):
    if not os.path.exists(path):
        return None
    return render(read_csv(path))


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def self_check():
    bad = []
    if token("Engine_UnChan") != "UnChan":
        bad.append("a module-prefixed name carries its file")
    if token("FUN_00012345") != "" or token("thunk_FUN_00012345") != "":
        bad.append("an unnamed body and a thunk carry no file")
    if token("??_H@YGXPAXIHP6EPAX0@Z@Z") != "" or token("Core") != "":
        bad.append("a library name and a bare module carry no file")
    if token("stargategame_StargatePlayer") != "StargatePlayer":
        bad.append("a game module prefix is not the file")
    if token("XboxAudio_XboxAudioSubsystem") != "XboxAudioSubsystem":
        bad.append("the XboxAudio module prefix is stripped whole")

    names = {0x9001: "Engine_UnChan", 0x9002: "Engine_UnChan", 0x9003: "Core_UnObj",
             0x9004: "FUN_00009004"}
    counts, classed = slot_files([0x9001, 0x9002, 0x9003, 0x9004, 0x9999], names)
    if counts["UnChan"] != 2 or counts["UnObj"] != 1 or classed != 3:
        bad.append("the token tally counts the file each slot body asserts in")

    import tempfile
    rows = [{"table_va": "0x00001000", "class": "", "slots": "5", "classed_slots": "2",
             "tokens": "UnChan:2", "distinctive": "UnChan:2", "top_token": "UnChan",
             "top_slots": "2", "top_df": "1", "named_by_class": "no"},
            {"table_va": "0x00002000", "class": "UnChan", "slots": "4", "classed_slots": "1",
             "tokens": "UnChan:1", "distinctive": "", "top_token": "UnChan", "top_slots": "1",
             "top_df": "2", "named_by_class": "yes"}]
    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_csv(path) != rows:
        bad.append("artifact round trip differs")
    text = render(rows)
    for want in ("0x00001000", "class family"):
        if want not in text:
            bad.append("render lacks %r" % (want,))
    if bad:
        print("FAIL class-slot-tokens: " + "; ".join(bad))
        return 1
    print("ok   class-slot-tokens: the module prefix, the tally and the block render agree with "
          "the synthetic tables")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--tables", default=TABLES)
    ap.add_argument("--names", default=NAMES)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()

    if args.check:
        return self_check()

    if args.check_doc:
        block = block_from_csv(SRC)
        if block is None:
            print("FAIL class-slot-tokens: %s is missing" % SRC)
            return 1
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-slot-tokens: %s has no generated block" % args.check_doc)
            return 1
        if text[i:j + len(DOC_END)] != DOC_START + "\n" + block + "\n" + DOC_END:
            print("FAIL class-slot-tokens: %s's block does not reproduce from %s"
                  % (args.check_doc, SRC))
            return 1
        print("ok   class-slot-tokens: %s's block reproduces from %s" % (args.check_doc, SRC))
        return 0

    if args.report or args.write_doc:
        block = block_from_csv(SRC)
        if block is None:
            print("FAIL class-slot-tokens: %s is missing" % SRC)
            return 1
        if args.report:
            print(block)
            return 0
        text = open(args.write_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-slot-tokens: %s has no generated block markers" % args.write_doc)
            return 1
        new = text[:i] + DOC_START + "\n" + block + "\n" + text[j:]
        if new != text:
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s" % args.write_doc)
        return 0

    if not args.xbe:
        ap.error("--xbe is required to read the tables (or use --report/--check-doc/--check)")
    import importlib.util
    import xbe as xbemod

    spec = importlib.util.spec_from_file_location("class_tables",
                                                  os.path.join(ROOT, "tools", "class-tables.py"))
    tablemod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tablemod)

    image = xbemod.Xbe(args.xbe)
    names = load_symbols(args.symbols)
    tables = {key(r["table_va"]): int(r["slots"]) for r in read_csv(args.tables)}
    tables = {int(k, 16): n for k, n in tables.items()}
    classes = {int(key(r["table_va"]), 16): r["class"] for r in read_csv(args.names)}
    rows = rows_of(tables, classes, names, image, tablemod)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, os.path.basename(OUT))
        write_artifact(rows, path)
        print("wrote %s (%d rows)" % (path, len(rows)))
    else:
        print(render(rows))
    s = summary(rows)
    print("tokens: %d of %d tables carry one; %d of %d named carry the class's own name; "
          "%d of %d unnamed carry a distinctive token"
          % (s["with_token"], s["tables"], s["named_by_class"], s["named"],
             len(s["unnamed_rare"]), s["unnamed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
