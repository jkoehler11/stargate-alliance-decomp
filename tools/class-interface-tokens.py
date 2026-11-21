#!/usr/bin/env python3
"""Do the 232 interface vtables' slots vote for a source file, the way a class table's do?

`tools/class-table-names.py`'s `slot-file-token` rule names four of the 18 unnamed class tables from the
assert file their slot bodies inline, because the symbol map names an unnamed body `<Module>_<File>` after
the assert path it inlines. The interfaces are the population that owes the next read -- one family
representative per anonymous family -- so the first question is whether that channel reaches them at all, and
this tool answers it by running the same tally over `inventory/class_plain_tables.csv`'s 232 interface
tables instead of the 492 class tables.

It is a census, not a naming rule, and the controls are what make its answer useful:

  * the interface's slots are mostly the compiler's defaults (111 stubs of 8 bytes or less, 24
    `__purecall`) and the shipped XDK libraries' methods, whose names are MSVC-mangled and not
    `<Module>_<File>`, so a token is the exception;
  * every token that does appear is a token a *class* table already carries -- these are slots the
    interface inherits from an engine base class, not slots that describe the interface;
  * the 18 interfaces the map already names by their own slots stay named; no row here renames one.

    python3 tools/class-interface-tokens.py --xbe DUMP/default.xbe -o inventory
    python3 tools/class-interface-tokens.py --report
    python3 tools/class-interface-tokens.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-interface-tokens.py --check          # synthetic, no dump, writes nothing
    python3 tools/class-interface-tokens.py --check-doc docs/systems/CLASS_REGISTRATION.md

Addresses, counts and file names only; no package or image content is written.
"""
import argparse
import collections
import csv
import importlib.util
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

PLAIN = os.path.join(ROOT, "inventory", "class_plain_tables.csv")
CLASS_TOKENS = os.path.join(ROOT, "inventory", "class_slot_tokens.csv")
SHAPES = os.path.join(ROOT, "inventory", "class_interface_shapes.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
OUT = os.path.join(ROOT, "inventory", "class_interface_tokens.csv")
DOC = os.path.join(ROOT, "docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-interface-tokens -->"
DOC_END = "<!-- /generated:class-interface-tokens -->"
RARE = 8                                    # a token on more tables than this is the base rate
FIELDS = ["table_va", "interface", "slots", "classed_slots", "tokens", "distinctive", "top_token",
          "top_df", "in_class_tables", "family", "verdict"]


def sibling(name):
    """A hyphenated sibling tool, imported by path (the repo names its tools with hyphens)."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"),
                                                  os.path.join(ROOT, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_symbols(path):
    names = {}
    for row in read_csv(path):
        try:
            names[int(row["address"], 16)] = row["name"]
        except (KeyError, ValueError):
            continue
    return names


def interface_rows(plain=None):
    """The 232 interface tables, in address order, from the plain-title census."""
    rows = [r for r in read_csv(PLAIN) if (r.get("verdict") or "").startswith("interface")]
    rows.sort(key=lambda r: int(r["table_va"], 16))
    return rows


def vector(image, table_va, slots):
    out = []
    for k in range(slots):
        try:
            out.append(struct.unpack("<I", image.read_va(table_va + 4 * k, 4))[0])
        except KeyError:
            break
    return out


def tally(vectors, names, tokener, class_tokens=None):
    """One row per table: the token counts, the rare ones, and the verdict.

    `class_tokens` is the token set the 492 class tables already carry
    (`inventory/class_slot_tokens.csv`); a row whose tokens are all in it is describing a slot the
    interface inherits, not one that distinguishes it.
    """
    class_tokens = class_tokens or set()
    per = {}
    for tva, vec in vectors.items():
        counts = collections.Counter()
        classed = 0
        for va in vec:
            tok = tokener(names.get(va, ""))
            if tok:
                counts[tok] += 1
                classed += 1
        per[tva] = (counts, classed)
    df = collections.Counter()
    for counts, _ in per.values():
        for tok in counts:
            df[tok] += 1
    out = []
    for tva, (counts, classed) in sorted(per.items()):
        rare = sorted(((t, n) for t, n in counts.items() if df[t] <= RARE),
                      key=lambda kv: (df[kv[0]], -kv[1], kv[0]))
        top = max(counts.items(), key=lambda kv: (kv[1], -df[kv[0]])) if counts else ("", 0)
        out.append({
            "table_va": "%#010x" % tva,
            "interface": "",
            "slots": str(len(vectors[tva])),
            "classed_slots": str(classed),
            "tokens": " ".join("%s:%d" % kv for kv in
                               sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "distinctive": " ".join("%s:%d" % kv for kv in rare),
            "top_token": top[0],
            "top_df": str(df[top[0]]) if top[0] else "",
            "in_class_tables": "yes" if counts and all(t in class_tokens for t in counts) else "",
            "family": "",
            "verdict": "no-token" if not counts else "token-only",
        })
    return out, df


def harvest(plain=None, class_tokens=None, names=None, image=None, tokener=None):
    rows = interface_rows(plain)
    vectors = {}
    for r in rows:
        n = int(r.get("slots") or 0)
        tva = int(r["table_va"], 16)
        vectors[tva] = vector(image, tva, n) if image is not None else []
    if class_tokens is None:
        class_tokens = set()
        for r in read_csv(CLASS_TOKENS):
            for item in (r.get("tokens") or "").split():
                tok, _, _n = item.rpartition(":")
                if tok:
                    class_tokens.add(tok)
    out, df = tally(vectors, names or {}, tokener or (lambda s: ""), class_tokens)
    by_va = {int(r["table_va"], 16): r for r in rows}
    families = {}
    for r in read_csv(SHAPES):
        try:
            families[int(r["table_va"], 16)] = r.get("family") or ""
        except (KeyError, ValueError):
            continue
    for row in out:
        src = by_va[int(row["table_va"], 16)]
        row["interface"] = src.get("vocab_class") or ""
        row["family"] = families.get(int(row["table_va"], 16), "")
        if row["interface"] and row["verdict"] == "token-only":
            row["verdict"] = "named-slots"
    return out, df


def summary(rows):
    named = [r for r in rows if r["interface"]]
    any_token = [r for r in rows if r["tokens"]]
    rare = [r for r in rows if r["distinctive"]]
    return {
        "tables": len(rows),
        "named": len(named),
        "unnamed": len(rows) - len(named),
        "with_token": len(any_token),
        "with_token_named": len([r for r in any_token if r["interface"]]),
        "with_token_unnamed": len([r for r in any_token if not r["interface"]]),
        "with_rare": len(rare),
        "with_rare_unnamed": len([r for r in rare if not r["interface"]]),
        "slots": sum(int(r["slots"]) for r in rows),
        "classed_slots": sum(int(r["classed_slots"]) for r in rows),
    }


def render(rows):
    s = summary(rows)
    df = collections.Counter()
    slots = sum(int(r["slots"]) for r in rows)
    vocab = collections.Counter()
    fams = collections.defaultdict(set)
    for r in rows:
        for item in (r["tokens"] or "").split():
            tok, _, n = item.rpartition(":")
            df[tok] += 1
            vocab[tok] += int(n)
            fams[tok].add(r.get("family") or "-")
    shared = [t for t in df if df[t] > 1]
    agree = [t for t in shared if len(fams[t]) == 1]
    cross = [t for t in shared if len(fams[t]) > 1]
    order = sorted(df, key=lambda t: (-df[t], -vocab[t], t))
    lines = ["| token (the `.cpp` its slots assert in) | tables | slots | shape families |",
             "|---|---:|---:|---:|"]
    for tok in order[:12]:
        lines.append("| `%s` | %d | %d | %d |" % (tok, df[tok], vocab[tok], len(fams[tok])))
    lines.append("")
    lines.append("**%d of the %d interface tables carry an assert-file token on at least one slot "
                 "(%d of the %d named, %d of the %d unnamed), over %d slots in all** -- and **%d carry "
                 "one no more than %d of the 232 carry**, of which %d are unnamed."
                 % (s["with_token"], s["tables"], s["with_token_named"], s["named"],
                    s["with_token_unnamed"], s["unnamed"], slots, s["with_rare"], RARE,
                    s["with_rare_unnamed"]))
    lines.append("")
    lines.append("**%d tokens are carried by more than one table, and %d of them sit in exactly one shape "
                 "family** (%s) -- a second, independent vote for the grouping `class-interface-shapes.py` "
                 "made from the slot vectors. **%d cross families** (%s), which is what an inherited slot "
                 "looks like rather than a family signature."
                 % (len(shared), len(agree), ", ".join("`%s`" % t for t in sorted(agree)) or "none",
                    len(cross), ", ".join("`%s`" % t for t in sorted(cross)) or "none"))
    lines.append("")
    lines.append("**The channel that named four class tables names nothing here, and the reason is the "
                 "population.** An interface's slots are the compiler's defaults (111 stubs of 8 bytes "
                 "or less, 24 `__purecall`; class-table census) and the shipped XDK libraries' methods, "
                 "whose names are MSVC-mangled rather than `<Module>_<File>`, so the vote is %d of %d "
                 "slots; %d of the %d tables that do carry a token carry only tokens a *class* table "
                 "already has, i.e. a slot inherited from an engine base class rather than a slot that "
                 "describes the interface. So the representative reads cannot be paid for with this "
                 "channel, and an interface's name has to come from its slots' behaviour or from a member "
                 "field that holds it."
                 % (s["classed_slots"], slots,
                    len([r for r in rows if r["in_class_tables"]]), s["with_token"]))
    return "\n".join(lines)


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def block_from_csv(path):
    rows = read_artifact(path)
    if not rows:
        return None
    return render(rows)


def splice_doc(text, block):
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test

def self_check():
    """Synthetic only: a token reader, the rare-token rule, the render, the round trip. No dump."""
    bad = []
    tok = sibling("class-slot-tokens").token
    if tok("Engine_UnChan") != "UnChan" or tok("FUN_00001000") != "":
        bad.append("the sibling tool's token reader is the one this census uses")
    plain = [{"table_va": "0x00001000", "slots": "3", "verdict": "interface-named",
              "vocab_class": "CFoo"},
             {"table_va": "0x00002000", "slots": "3", "verdict": "interface-unnamed",
              "vocab_class": ""},
             {"table_va": "0x00003000", "slots": "2", "verdict": "interface-unnamed",
              "vocab_class": ""}]
    names = {0x9001: "Engine_UnChan", 0x9002: "Core_UnObj", 0x9003: "??0CFoo@@QAE@XZ"}
    vecs = {0x1000: [0x9001, 0x9003], 0x2000: [0x9002, 0x9003], 0x3000: [0x9003, 0x9003]}
    out, df = tally(vecs, names, tok, {"UnChan", "UnObj"})
    by = {int(r["table_va"], 16): r for r in out}
    if by[0x1000]["classed_slots"] != "1" or by[0x1000]["tokens"] != "UnChan:1":
        bad.append("one slot's assert file is one token")
    if by[0x3000]["verdict"] != "no-token":
        bad.append("a table whose slots are all library methods carries no token")
    if df["UnChan"] != 1 or df["UnObj"] != 1:
        bad.append("the document frequency counts tables, not slots: %r" % (dict(df),))
    if by[0x1000]["in_class_tables"] != "yes" or by[0x3000]["in_class_tables"]:
        bad.append("a token the class tables carry is marked as theirs")
    by[0x1000]["interface"] = "CFoo"
    by[0x1000]["verdict"] = "named-slots"
    by[0x2000]["slots"] = by[0x3000]["slots"] = by[0x1000]["slots"] = "3"
    text = render([by[0x1000], by[0x2000], by[0x3000]])
    if "2 of the 3 interface tables" not in text:
        bad.append("the block counts the tables that carry a token")
    tmp = os.path.join(tempfile.mkdtemp(), "a.csv")
    rows = [by[0x1000], by[0x2000], by[0x3000]]
    write_artifact(rows, tmp)
    if read_artifact(tmp) != rows:
        bad.append("artifact round trip differs")
    if splice_doc("no markers here", "x") is not None:
        bad.append("splice found markers where there are none")
    if bad:
        print("FAIL class-interface-tokens: " + "; ".join(bad))
        return 1
    print("ok   class-interface-tokens: the assert-file token, the document frequency and the render "
          "all behave on synthetic slots")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report or args.write_doc or args.check_doc:
        block = block_from_csv(OUT)
        if block is None:
            print("FAIL class-interface-tokens: %s is missing" % OUT)
            return 1
        if args.report:
            print(block)
            return 0
        if args.check_doc:
            text = open(args.check_doc, encoding="utf-8").read()
            i, j = text.find(DOC_START), text.find(DOC_END)
            if i < 0 or j < 0:
                print("FAIL class-interface-tokens: %s has no generated block" % args.check_doc)
                return 1
            if text[i:j + len(DOC_END)] != DOC_START + "\n" + block + "\n" + DOC_END:
                print("FAIL class-interface-tokens: %s's block is stale against %s"
                      % (args.check_doc, OUT))
                return 1
            print("ok   class-interface-tokens: %s matches the committed artifact" % args.check_doc)
            return 0
        text = open(args.write_doc, encoding="utf-8").read()
        new = splice_doc(text, block)
        if new is None:
            print("FAIL class-interface-tokens: %s has no generated block markers" % args.write_doc)
            return 1
        if new != text:
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s" % args.write_doc)
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the tables (or use --report/--check-doc/--check)")
    import xbe as xbemod
    image = xbemod.Xbe(args.xbe)
    rows, df = harvest(names=load_symbols(args.symbols), image=image,
                       tokener=sibling("class-slot-tokens").token)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "class_interface_tokens.csv")
        write_artifact(rows, path)
        print("wrote %s (%d rows)" % (path, len(rows)))
    else:
        print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
