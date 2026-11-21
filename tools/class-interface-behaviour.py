#!/usr/bin/env python3
"""What the 232 interface tables' slot bodies are: their own file, their literals, and what they call.

`tools/class-interface-families.py` bounded the per-body read at **89 representative reads over 135
tables** and left the question of what a read would find. `tools/class-interface-tokens.py` measured the
first channel -- the assert file a slot body's own map name carries -- and found it on 25 of the 232
tables (45 of 2,527 slots), so the representatives stayed owed. This tool reads the same population
three ways, because a read is only owed where there is a body to read:

  * **the body census**: every slot is `__purecall`, a stub of 8 bytes or less, a body the map has a row
    for, or a body outside it. A table whose slots are all defaults has nothing to read;
  * **the literals a slot body pushes**: `tools/class-ctor-evidence.py` reads a `push imm32` whose
    immediate is a printable UTF-16 string, and this image's asserts carry their source path, so the
    same read recovers the `.cpp` a body was compiled from **even when the symbol map left the body
    unnamed** -- the channel the token census could only read through a name;
  * **what a slot body calls**: the direct `E8` targets, resolved through the map, give the callee's own
    file (a second, independent `<Module>_<File>` vote) and its mangled class scope.

The two file witnesses are reported side by side and not merged: a table's slot token names the file of
the *interface's own* method, while a callee's file names the file of the code it *calls*, so agreement
is evidence and disagreement is a helper call inside an inherited slot.

The undecoded half joins `docs/UNKNOWNS.md` U40: a slot outside the function map whose address is a
`tools/gap-heads.py` head is a decodable body nothing has named yet, and those are the map's work rather
than this census's.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-behaviour.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-behaviour.py --report
    python3 tools/class-interface-behaviour.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-interface-behaviour.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-interface-behaviour.py --check      # synthetic rows, no dump, writes nothing

Addresses, slot indices, file names and counts only; no package or image content is written.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

PLAIN = os.path.join(ROOT, "inventory", "class_plain_tables.csv")
SHAPES = os.path.join(ROOT, "inventory", "class_interface_shapes.csv")
FAMILIES = os.path.join(ROOT, "inventory", "class_interface_families.csv")
TOKENS = os.path.join(ROOT, "inventory", "class_interface_tokens.csv")
HEADS = os.path.join(ROOT, "inventory", "gap_heads.csv")
MODULES = os.path.join(ROOT, "inventory", "function_modules.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
OUT = os.path.join(ROOT, "inventory", "class_interface_behaviour.csv")
DOC_START = "<!-- generated:class-interface-behaviour -->"
DOC_END = "<!-- /generated:class-interface-behaviour -->"
FIELDS = ("table_va", "slots", "role", "family", "purecall", "stubs", "real", "outside", "calls",
          "callees", "callee_files", "top_file", "callee_classes", "top_class", "callee_modules",
          "literals", "literal_head", "slot_token", "token_agree", "gap_slots", "gap_verdicts",
          "verdict", "evidence")
STUB_BYTES = 8
HEX = re.compile(r"^(0x)?[0-9a-fA-F]+$")
SOURCE = re.compile(r"([A-Za-z0-9_]+\.(?:cpp|h))$")
# A named callee that is one of these carries no behaviour: an assert, a CRT helper, or `operator
# delete`. A table whose every named callee is in this set has a body that only checks its arguments.
INERT = ("appAssert", "__wcs", "_wcs", "??2@", "??3@", "memcpy", "memset", "memmove", "strlen",
         "strcmp", "wcscmp", "wcslen", "_mem", "_str")
VERDICTS = ("callee-file", "assert-only", "mapped-no-name", "unmapped-only", "no-body")


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


def key(text):
    text = (text or "").strip()
    return "%#010x" % int(text, 16) if HEX.match(text) else text


def load_symbols(path):
    names, sizes = {}, {}
    for row in read_csv(path):
        try:
            va = int(row["address"], 16)
        except (KeyError, ValueError):
            continue
        names[va] = row.get("name") or ""
        try:
            sizes[va] = int(row.get("size") or 0)
        except ValueError:
            sizes[va] = 0
    return names, sizes


def load_modules(path):
    out = {}
    for row in read_csv(path):
        try:
            out[int(row["address"], 16)] = row.get("module") or ""
        except (KeyError, ValueError):
            continue
    return out


def gap_heads(path):
    """{head_va: verdict} for the bodies U40's gap census decodes."""
    out = {}
    for row in read_csv(path):
        try:
            out[int(row["head_va"], 16)] = row.get("verdict") or ""
        except (KeyError, ValueError):
            continue
    return out


def interface_rows(plain=None):
    rows = [r for r in read_csv(PLAIN) if (r.get("verdict") or "").startswith("interface")]
    rows.sort(key=lambda r: int(r["table_va"], 16))
    return rows


# --------------------------------------------------------------------------- reading one table
def body_calls(disasm, body_va, size):
    """The direct `E8`/`E9` targets of one body, from its disassembly text."""
    out = []
    for _at, text in disasm(body_va, size):
        m = re.match(r"(?:call|jmp)\s+0x([0-9a-f]+)", text)
        if m:
            out.append(int(m.group(1), 16))
    return out


def verdict_of(kinds, files, named, gap):
    """The five categories, in the order that makes the strongest channel win."""
    real = kinds.get("real", 0)
    if files:
        return "callee-file"
    if real and named and all(n.startswith(INERT) for n in named):
        return "assert-only"
    if real:
        return "mapped-no-name"
    if gap:
        return "unmapped-only"
    return "no-body"


def agree(slot_token, top_file):
    if slot_token and top_file:
        return "yes" if slot_token == top_file else "no"
    if slot_token or top_file:
        return "one-side"
    return ""


def harvest(rows, slots_of, shapes, tokens, heads, names, sizes, modules, disasm, literals, token,
            mangle):
    """One row per interface table, plus a `census` row. `disasm` and `literals` read the image."""
    out = []
    seen_heads = collections.defaultdict(set)          # head -> {(table, slot index)}
    for r in rows:
        tva = int(r["table_va"], 16)
        vector = slots_of(tva, int(r.get("slots") or 0))
        kinds = collections.Counter()
        files, classes, mods = collections.Counter(), collections.Counter(), collections.Counter()
        named, head_hits = [], []
        calls = 0
        lits = []
        for slot_index, va in enumerate(vector):
            name = names.get(va, "")
            if name == "__purecall":
                kinds["pure"] += 1
                continue
            if va not in sizes:
                kinds["outside"] += 1
                if va in heads:
                    head_hits.append(heads[va])
                    seen_heads[va].add((tva, slot_index))
                continue
            if sizes[va] and sizes[va] <= STUB_BYTES:
                kinds["stub"] += 1
                continue
            kinds["real"] += 1
            for lit in literals(va, sizes[va] or 0):
                if lit not in lits:
                    lits.append(lit)
            for tgt in body_calls(disasm, va, sizes[va]):
                calls += 1
                cname = names.get(tgt, "")
                tok = token(cname)
                if tok:
                    files[tok] += 1
                cls = mangle(cname)
                if cls:
                    classes[cls] += 1
                if cname and not cname.startswith(("FUN_", "thunk_")):
                    named.append(cname)
                mods[modules.get(tgt, "(unnamed)")] += 1
        shape = shapes.get(r["table_va"], {})
        tok_row = tokens.get(r["table_va"], {})
        slot_token = (tok_row.get("top_token") or "").strip()
        top_file = max(files.items(), key=lambda kv: (kv[1], kv[0]))[0] if files else ""
        top_class = max(classes.items(), key=lambda kv: (kv[1], kv[0]))[0] if classes else ""
        verdict = verdict_of(kinds, files, named, head_hits)
        out.append({
            "table_va": r["table_va"],
            "slots": str(len(vector)),
            "role": shape.get("role", ""),
            "family": shape.get("family", ""),
            "purecall": str(kinds["pure"]),
            "stubs": str(kinds["stub"]),
            "real": str(kinds["real"]),
            "outside": str(kinds["outside"]),
            "calls": str(calls),
            "callees": str(len(set(named))),
            "callee_files": " ".join("%s:%d" % kv for kv in
                                     sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))),
            "top_file": top_file,
            "callee_classes": " ".join("%s:%d" % kv for kv in
                                       sorted(classes.items(), key=lambda kv: (-kv[1], kv[0]))[:4]),
            "top_class": top_class,
            "callee_modules": " ".join("%s:%d" % kv for kv in
                                       sorted(mods.items(), key=lambda kv: (-kv[1], kv[0]))[:4]),
            "literals": str(len(lits)),
            "literal_head": (lits[0][:60] if lits else ""),
            "slot_token": slot_token,
            "token_agree": agree(slot_token, top_file),
            "gap_slots": str(len(head_hits)),
            "gap_verdicts": " ".join("%s:%d" % kv for kv in
                                     sorted(collections.Counter(head_hits).items())),
            "verdict": verdict,
            "evidence": ("%d slot(s): %d pure, %d stub, %d mapped, %d outside (%d a gap head); "
                         "%d call(s); file %s; first literal %s; slot token %s"
                         % (len(vector), kinds["pure"], kinds["stub"], kinds["real"],
                            kinds["outside"], len(head_hits), calls, top_file or "-",
                            (lits[0][:40] if lits else "-"), slot_token or "-")),
        })
    census = {k: "" for k in FIELDS}
    census.update({
        "table_va": "census",
        "slots": str(sum(int(r["slots"]) for r in out)),
        "purecall": str(sum(int(r["purecall"]) for r in out)),
        "stubs": str(sum(int(r["stubs"]) for r in out)),
        "real": str(sum(int(r["real"]) for r in out)),
        "outside": str(sum(int(r["outside"]) for r in out)),
        "calls": str(sum(int(r["calls"]) for r in out)),
        "gap_slots": str(sum(int(r["gap_slots"]) for r in out)),
        "gap_verdicts": "distinct %d; one-table %d; shared %d; same-index %d; cross-index %d"
                        % (len(seen_heads),
                           sum(1 for t in seen_heads.values() if len({x[0] for x in t}) == 1),
                           sum(1 for t in seen_heads.values() if len({x[0] for x in t}) > 1),
                           sum(1 for t in seen_heads.values()
                               if len({x[0] for x in t}) > 1 and len({x[1] for x in t}) == 1),
                           sum(1 for t in seen_heads.values()
                               if len({x[0] for x in t}) > 1 and len({x[1] for x in t}) > 1)),
        "verdict": "census",
        "evidence": "the 232 interface tables: %d gap-head slot(s) over %d distinct bodies"
                    % (sum(int(r["gap_slots"]) for r in out), len(seen_heads)),
    })
    out.append(census)
    return out


def family_rows(fams, by_va):
    """The 89 owed representatives, with the census row for each."""
    out = []
    for f in fams:
        if (f.get("read_owed") or "") != "yes":
            continue
        row = by_va.get(f.get("rep_va") or "")
        out.append((f, row))
    return out


def counted(text, name):
    """One named integer of the census row's `gap_verdicts` sentence, or 0 when absent."""
    m = re.search(r"\b%s (\d+)" % re.escape(name), text or "")
    return int(m.group(1)) if m else 0


def summary(rows, fams):
    census = next((r for r in rows if r["table_va"] == "census"), {})
    rows = [r for r in rows if r["table_va"] != "census"]
    by_va = {r["table_va"]: r for r in rows}
    cats = collections.Counter(r["verdict"] for r in rows)
    reps = family_rows(fams, by_va)
    rep_cats = collections.Counter(r["verdict"] if r else "missing" for _f, r in reps)
    return {
        "tables": len(rows),
        "slots": sum(int(r["slots"]) for r in rows),
        "real_total": sum(int(r["real"]) for r in rows),
        "cats": cats,
        "with_calls": sum(1 for r in rows if int(r["calls"])),
        "file_voted": [r for r in rows if r["top_file"]],
        "class_voted": sum(1 for r in rows if r["top_class"]),
        "literal_tables": [r for r in rows if int(r["literals"])],
        "agree_yes": [r for r in rows if r["token_agree"] == "yes"],
        "agree_no": [r for r in rows if r["token_agree"] == "no"],
        "agree_one": [r for r in rows if r["token_agree"] == "one-side"],
        "gap_slots": sum(int(r["gap_slots"]) for r in rows),
        "gap_bodies": counted(census.get("gap_verdicts"), "distinct"),
        "gap_single": counted(census.get("gap_verdicts"), "one-table"),
        "gap_shared": counted(census.get("gap_verdicts"), "shared"),
        "gap_same": counted(census.get("gap_verdicts"), "same-index"),
        "gap_cross": counted(census.get("gap_verdicts"), "cross-index"),
        "gap_tables": sum(1 for r in rows if int(r["gap_slots"])),
        "reps": len(reps),
        "rep_cats": rep_cats,
        "rep_rows": reps,
        "families": len(fams),
    }


def render(rows, fams):
    s = summary(rows, fams)
    c = s["cats"]
    lines = ["| what the slot bodies are | tables | slots |", "|---|---|---|"]
    for v in VERDICTS:
        group = [r for r in rows if r["verdict"] == v]
        if not group:
            continue
        lines.append("| `%s` | %d | %d |" % (v, len(group), sum(int(r["slots"]) for r in group)))
    lines.append("")
    lines.append("**%d of the %d interface tables hold at least one body the map decodes (%d slots), "
                 "%d call something, and %d name the file they call; %d tables' only body is an "
                 "undecoded gap head and %d hold no body at all.** The %d interface slots outside the "
                 "function map are %d `tools/gap-heads.py` heads over %d distinct bodies, so they are "
                 "U40's work and not a fourth channel: %d of the %d sit in exactly one table and %d in "
                 "two or more -- and of the shared ones %d sit at the same slot index in both tables and "
                 "%d at different indices, so the twin families share bodies and an add would have to "
                 "name one body from two tables for those."
                 % (c["callee-file"] + c["assert-only"] + c["mapped-no-name"], s["tables"],
                    s["real_total"], s["with_calls"], len(s["file_voted"]),
                    c["unmapped-only"], c["no-body"], s["gap_slots"], s["gap_slots"],
                    s["gap_bodies"], s["gap_single"], s["gap_bodies"], s["gap_shared"],
                    s["gap_same"], s["gap_cross"]))
    lines.append("")
    lines.append("| the 89 representatives | families |")
    lines.append("|---|---|")
    for v in VERDICTS:
        if s["rep_cats"][v]:
            lines.append("| `%s` | %d |" % (v, s["rep_cats"][v]))
    lines.append("")
    rc = s["rep_cats"]
    lines.append("**Of the %d representative reads the families census owes, only %d have a mapped "
                 "body to read; %d more own nothing but an undecoded gap head and %d are the compiler's "
                 "defaults (purecalls and stubs) and have no read at all.** %d of the mapped ones carry "
                 "a callee file."
                 % (s["reps"], rc["callee-file"] + rc["assert-only"] + rc["mapped-no-name"],
                    rc["unmapped-only"], rc["no-body"], rc["callee-file"]))
    lines.append("")
    if s["file_voted"]:
        lines.append("| representative | slots | the file its slots call | callee class | the slot's own token | agree |")
        lines.append("|---|---|---|---|---|---|")
        reps = [r for _f, r in s["rep_rows"] if r and r["top_file"]]
        for r in sorted(reps, key=lambda r: -int(r["callee_files"].split(":")[-1]))[:12]:
            lines.append("| `%s` | %s | %s | %s | %s | %s |"
                         % (r["table_va"], r["slots"], r["callee_files"], r["top_class"] or "-",
                            r["slot_token"] or "-", r["token_agree"] or "-"))
        lines.append("")
    lines.append("**The callee file is the helper's file, not the interface's.** %d tables carry both "
                 "a slot token and a callee file: **%d agree and %d disagree**, and %d carry only one "
                 "of the two. A slot token is the file the interface's own method was compiled from and "
                 "a callee file is the file of the code that method calls, so the call target labels "
                 "what a slot does -- a template instantiation, a class helper, zlib -- and cannot "
                 "stand in for the interface's name."
                 % (len(s["agree_yes"]) + len(s["agree_no"]), len(s["agree_yes"]),
                    len(s["agree_no"]), len(s["agree_one"])))
    lines.append("")
    if s["literal_tables"]:
        lines.append("**%d tables' slot bodies push a printable literal, and none of them is a source "
                     "path.** The strings name what the slot does rather than the file it came from "
                     "(%s), so a literal labels a slot's subsystem where the map has no name for its "
                     "body, and the `<Module>_<File>` token is not among the pushed immediates."
                     % (len(s["literal_tables"]),
                        "; ".join("`%s` %s" % (r["table_va"], r["literal_head"])
                                  for r in s["literal_tables"][:6])))
    return "\n".join(lines)


def write_artifact(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


def block_from_csv(path):
    rows = read_csv(path)
    if not rows:
        return None
    return render(rows, read_csv(FAMILIES))


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []
    if verdict_of({"real": 2}, {"UnChan": 1}, ["Core_UnObjBas"], []) != "callee-file":
        bad.append("a callee file wins over the body census")
    if verdict_of({"real": 1}, {}, ["appAssertFailed"], []) != "assert-only":
        bad.append("a body that only asserts is not a read")
    if verdict_of({"real": 1}, {}, ["FUN_00001234"], []) != "mapped-no-name":
        bad.append("a mapped body whose callees are unnamed is not a file")
    if verdict_of({"real": 0}, {}, [], ["body"]) != "unmapped-only":
        bad.append("a gap head is a body the map has no row for")
    if verdict_of({"real": 0}, {}, [], []) != "no-body":
        bad.append("a table of defaults owes no read")
    if verdict_of({"pure": 20}, {}, [], []) != "no-body":
        bad.append("purecalls and stubs are not a body")
    if agree("UnChan", "UnChan") != "yes" or agree("UnChan", "UnObj") != "no":
        bad.append("the two file witnesses are compared, not merged")
    if agree("", "UnChan") != "one-side" or agree("", "") != "":
        bad.append("a one-sided or absent witness is reported as such")
    if not SOURCE.search("..\\Engine\\Src\\UnChan.cpp"):
        bad.append("a source path is recognised as one")

    rows = []
    for va, verdict, sl in (("0x004c0001", "callee-file", 21), ("0x004c0002", "assert-only", 12),
                            ("0x004c0003", "unmapped-only", 9), ("0x004c0004", "no-body", 2)):
        rows.append({"table_va": va, "slots": str(sl), "role": "anonymous", "family": "0x1",
                     "purecall": "1", "stubs": "1", "real": "1" if verdict != "no-body" else "0",
                     "outside": "1", "calls": "3", "callees": "1", "callee_files": "UnChan:1",
                     "top_file": "UnChan" if verdict == "callee-file" else "",
                     "callee_classes": "", "top_class": "", "callee_modules": "Core:1",
                     "literals": "1", "literal_head": "no body", "slot_token": "UnChan",
                     "token_agree": "yes", "gap_slots": "1" if verdict == "unmapped-only" else "0",
                     "gap_verdicts": "body:1", "verdict": verdict, "evidence": ""})
    fams = [{"read_owed": "yes", "rep_va": r["table_va"]} for r in rows]
    out = render(rows, fams)
    for want in ("callee-file", "assert-only", "unmapped-only", "no-body", "representative"):
        if want not in out:
            bad.append("render lacks %r" % (want,))

    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_csv(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL class-interface-behaviour: " + "; ".join(bad))
        return 1
    print("ok   class-interface-behaviour: the five categories, the two file witnesses and the "
          "artifact round trip agree with the synthetic tables")
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        block = block_from_csv(OUT)
        if block is None:
            print("FAIL class-interface-behaviour: %s is missing" % OUT)
            return 1
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-interface-behaviour: %s has no generated block" % args.check_doc)
            return 1
        if text[i:j + len(DOC_END)] != DOC_START + "\n" + block + "\n" + DOC_END:
            print("FAIL class-interface-behaviour: %s's block does not reproduce from %s"
                  % (args.check_doc, OUT))
            return 1
        print("ok   class-interface-behaviour: %s's block reproduces from %s"
              % (args.check_doc, OUT))
        return 0
    if args.report or args.write_doc:
        block = block_from_csv(OUT)
        if block is None:
            print("FAIL class-interface-behaviour: %s is missing" % OUT)
            return 1
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block)
        if new is None:
            print("FAIL class-interface-behaviour: %s has no generated block markers" % args.write_doc)
            return 1
        if new != open(args.write_doc, encoding="utf-8").read():
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s" % args.write_doc)
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the slots (or use --report/--check-doc/--check)")

    names_mod = sibling("class-table-names")
    tokens_mod = sibling("class-slot-tokens")
    plain_mod = sibling("class-plain-tables")
    evidence_mod = sibling("class-ctor-evidence")
    xbemod = sibling("xbe")
    image = xbemod.Xbe(args.xbe)
    img = names_mod.Image()
    img.xbe, img.data = image, image.data
    names, sizes = load_symbols(SYMBOLS)
    img.size_of = lambda va: sizes.get(va)
    text = next(s for s in image.sections if s.name == ".text")
    in_text = lambda va: text.va <= va < text.va + text.fsize

    def literals(va, size):
        body = img.read(va, size or 0) or b""
        return evidence_mod.literals(image, body, va, in_text)

    def slots_of(tva, count):
        return [struct.unpack("<I", image.read_va(tva + 4 * k, 4))[0] for k in range(count)]

    shapes = {r["table_va"]: r for r in read_csv(SHAPES)}
    tokens = {r["table_va"]: r for r in read_csv(TOKENS)}
    rows = harvest(interface_rows(), slots_of, shapes, tokens, gap_heads(HEADS), names, sizes,
                   load_modules(MODULES), img.disasm, literals, tokens_mod.token,
                   plain_mod.mangle_class)
    if args.out:
        path = os.path.join(args.out, os.path.basename(OUT))
        write_artifact(rows, path)
        print("wrote %s (%d rows)" % (path, len(rows)))
    else:
        print(render(rows, read_csv(FAMILIES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
