#!/usr/bin/env python3
"""The receiver-class join: for every refusal that names a receiver or a callee, *where the name is declared*.

Four refusal families in `tools/uscript-emit.py` are one question wearing four costumes: the walk resolved every
artefact it has and still does not know what the name in front of a `.` (or a bare call) is.

    a call to a name the class chain does not declare                     (a bare call)
    a member call statement to a name the receiver's chain does not declare
    a call statement whose receiver's class the artifacts do not name
    a member that is not a property of the receiver's class

Measuring them separately is how a run ends up with four nodes and no join. The emitter already records, per refused
body, the refused expression, the receiver class the artefacts gave it and (when the receiver's class is what was
missing) *why* `_class_of` answered nothing; this tool joins that row to the corpus's own declarations -- the
`function`/`event`/`var` a class's ScriptText spells -- and to `inventory/classes.csv`'s ancestor walk, and answers one
thing per row: is the name declared at class scope on the chain the walk used, only inside a state of it, off the
chain, or nowhere at all.

    python3 tools/uscript-emit.py --tree $WORK/scripts-all --dump .../System \\\\
        -o $WORK/emit --residue-out $WORK/residue
    python3 tools/receiver-join.py --tree $WORK/scripts-all --residue $WORK/residue/residue.csv -o inventory
    python3 tools/receiver-join.py --report
    python3 tools/receiver-join.py --write-doc docs/COMPILER_FRONTEND.md
    python3 tools/receiver-join.py --check          # synthetic, no corpus, writes nothing

`inventory/emit_receiver_join.csv` is one row per (family, where) pair with its refusal count and up to three
`class.function` examples, plus a `census` row. Class and function names only; no package or source text is written.
"""

import argparse
import collections
import csv
import importlib.util
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "inventory", "emit_receiver_join.csv")
EMIT = os.path.join(ROOT, "inventory", "uscript_emit.csv")

FAMILIES = {
    "a call to a name the class chain does not declare": "bare call",
    "a member call statement to a name the receiver's chain does not declare": "member call, statement",
    "a call statement whose receiver's class the artifacts do not name": "member call, no receiver class",
    "a call member whose receiver's class the artifacts do not name": "member call, no receiver class",
    "a member that is not a property of the receiver's class": "member read",
}

FIELDS = ["family", "where", "why", "bodies", "examples"]
DOC_A = ("<!-- BEGIN receiver-join -->", "<!-- END receiver-join -->")

# `where` verdicts, in the order the render lists them.
WHERE = ["on-chain", "state", "off-chain", "undeclared", "unnamed"]


def _emitter():
    spec = importlib.util.spec_from_file_location("uscript_emit", os.path.join(ROOT, "tools", "uscript-emit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_DECL = re.compile(r"(?m)^[ \t]*(?:var|Automated)\s*(?:\([^)]*\))?\s*"
                   r"(?:(?:const|config|private|protected|public|transient|native|editconst|editinline|"
                   r"editinlineuse|export|noimport|duplicatetransient|localized|input|travel|"
                   r"deprecated)\s+)*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)")


def declarations(tree, emit):
    """`(functions, properties)`: name -> {class: {states}} and name -> {class}.

    The corpus's own text is the only place a declaration that the package did not record still exists -- and it is
    what the compiler read. A class-scope declaration is state `""`; a function inside a `state` block is that
    state's, which is the distinction the `state` verdict turns on.
    """
    funcs = collections.defaultdict(lambda: collections.defaultdict(set))
    props = collections.defaultdict(lambda: collections.defaultdict(set))
    for dirpath, _dirs, names in os.walk(tree):
        for name in sorted(names):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="latin-1", newline="") as fh:
                text = fh.read()
            cls = os.path.splitext(os.path.basename(path))[0].lower()
            for state, fn, _rt, _body, _nat, _fin, _params in emit.functions_of(text):
                funcs[fn.lower()][cls].add((state or "").lower())
            for m in _DECL.finditer(text):
                props[m.group(2).lower()][cls].add(m.group(1).lower())
    return funcs, props


def bucket(nm, chain, funcs, props, read=False):
    """Where a name is declared, relative to a chain of class names.

    `chain` is the ancestor list the walk used, nearest first. A declaration at class scope on that chain answers
    `on-chain`; one only inside a state of it answers `state`; one only on classes outside it `off-chain`; a name no
    class declares at all `undeclared`. A *member read* asks the properties first and falls back to the functions,
    because a member that is a function and not a property is a call the walk classified as a read.
    """
    declared = props.get(nm) or {}
    table = "property"
    if not declared or (read and not (set(declared) & set(chain))):
        alt = funcs.get(nm) or {}
        if alt and (not declared or set(alt) & set(chain) or not (set(declared) & set(chain))):
            declared, table = alt, "function"
    if not declared:
        return "undeclared"
    states = set()
    for cls, st in declared.items():
        if cls in chain:
            states |= st
    if states:
        return "on-chain" if "" in states else "state"
    return "off-chain"


def classify(rows, chain_of, funcs, props):
    """The (family, where, why) counter and the examples, from residue rows and a chain lookup.

    A **member** family whose receiver class the walk never named is not asked against a chain: there was no chain
    to ask, and bucketing such a row against the empty one put 232 member reads in `off-chain` that share no
    inheritance story at all -- 223 of them the member sits on a class nowhere near the body. Such a row is
    `unnamed`, and the emitter's own `why` is what becomes the cell. The bare-call family has no receiver by
    construction and always walks the body's own chain.
    """
    out = collections.Counter()
    ex = collections.defaultdict(list)
    for r in rows:
        fam = FAMILIES.get(r["reason"])
        if fam is None:
            continue
        nm = (r.get("member") or "").lower()
        recv = (r.get("receiver") or "").lower()
        why = r.get("why") or ""
        if not nm:
            out[(fam, "undeclared", why)] += 1
            continue
        if fam != "bare call" and not recv:
            out[(fam, "unnamed", why)] += 1
            ex[(fam, "unnamed", why)].append("%s.%s" % (r["class"], r["function"]))
            continue
        chain = chain_of(recv) if recv else chain_of("")
        where = bucket(nm, chain, funcs, props, read=(fam == "member read"))
        out[(fam, where, why)] += 1
        ex[(fam, where, why)].append("%s.%s" % (r["class"], r["function"]))
    return out, ex


def rows_of(counter, ex):
    rows = []
    for (fam, where, why), n in sorted(counter.items()):
        rows.append({"family": fam, "where": where, "why": why, "bodies": str(n),
                     "examples": " ".join(ex[(fam, where, why)][:3])})
    return rows


def render(rows, census):
    fams = []
    for r in rows:
        if r["family"] not in fams:
            fams.append(r["family"])
    total = sum(int(r["bodies"]) for r in rows)
    by = collections.Counter()
    for r in rows:
        by[(r["family"], r["where"])] += int(r["bodies"])
    lines = []
    lines.append("**{:,} refused bodies over the four receiver families, joined to the corpus's own "
                 "declarations** (`inventory/emit_receiver_join.csv`; {:,} modelled of {:,} walked, so the join is "
                 "over the residue and not over the corpus).".format(
                     total, census.get("modelled", 0), census.get("walked", 0)))
    lines.append("")
    lines.append("| family | refusals | on the chain | only in a state | off the chain | undeclared | "
                 "no receiver class |")
    lines.append("|---|---|---|---|---|---|---|")
    for fam in fams:
        lines.append("| %s | %d | %d | %d | %d | %d | %d |" % (
            fam, sum(by.get((fam, w), 0) for w in WHERE),
            by.get((fam, "on-chain"), 0), by.get((fam, "state"), 0), by.get((fam, "off-chain"), 0),
            by.get((fam, "undeclared"), 0), by.get((fam, "unnamed"), 0)))
    lines.append("")
    lines.append("A name **on the chain** and still refused is a record the body's package does not carry: the "
                 "compiler writes those by *name index* (`1b <index>`), which is the rule this read added -- "
                 "`TransientAmbientPawn.Died` ships `1b 41 02` for `PlayDying(HitLocation, HitDamageType)` and "
                 "`ScriptedController.LeaveScripting` `19 01 8d 07 00 00 1b 39 28 16` for `Pawn.SetWalking(false)`, "
                 "neither callee imported, exported or in `natives.csv` there. **Undeclared** is the residue that is "
                 "neither: a name the reconstructed text spells and no declaration carries (`GUIImageList.internalKeyEvent`, "
                 "`GUIEditBox.InternalOnKeyEvent`, `UWindowDialogClientWindow.CreateControl`). An **array method** is not "
                 "one of them -- this build compiles `A.Remove(i, n)` and `A.Insert(i, n)` as the `0x41`/`0x40` "
                 "intrinsics, and the join counted them `undeclared` only while the emitter refused the node (see the "
                 "dynamic-array section).")
    lines.append("")
    lines.append("A **member** row is never bucketed against a chain the walk did not have: when `_class_of` names no "
                 "receiver class the row is `unnamed`, and the table below is what the walk was missing. Bucketing "
                 "those rows against the empty chain instead put 232 member reads *off* the chain -- a number the "
                 "previous read of this page carried -- where 223 of them name a member on a class nowhere near the "
                 "body, i.e. the verdict was the join's, not the build's. The `off-chain` and `undeclared` cells for "
                 "the member families are what is left when the receiver really was named.")
    lines.append("")
    lines.append("| family | where | bodies | examples |")
    lines.append("|---|---|---|---|")
    for r in rows:
        if r["where"] == "unnamed":
            # the why table below is this population's detail; a row here would repeat its family six times
            # with no column to tell the rows apart
            continue
        lines.append("| %s | %s | %s | %s |" % (r["family"], r["where"], r["bodies"],
                                                ", ".join("`%s`" % e for e in r["examples"].split()[:3]) or "-"))
    unnamed = collections.defaultdict(lambda: [0, ""])
    for r in rows:
        if r["where"] != "unnamed":
            continue
        cell = unnamed[r["why"] or "(not recorded)"]
        cell[0] += int(r["bodies"])
        if not cell[1]:
            cell[1] = r["examples"]
    if unnamed:
        lines.append("")
        lines.append("| what the walk was missing | refusals | examples |")
        lines.append("|---|---|---|")
        for why, (n, exa) in sorted(unnamed.items(), key=lambda kv: (-kv[1][0], kv[0])):
            lines.append("| %s | %d | %s |" % (why, n,
                                              ", ".join("`%s`" % e for e in exa.split()[:2]) or "-"))
    return "\n".join(lines)


def read_artifact(path):
    rows, census = [], {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["family"] == "(census)":
                census = r
            else:
                rows.append(r)
    return rows, census


def emit_census(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            # The emitter writes the marker in `package`, not `class`: read there, or the page's headline says
            # "0 modelled of 0 walked" beside an artifact whose census row carries 9,479 of 11,687.
            if r.get("class") == "(census)" or r.get("package") == "(census)":
                return {"modelled": int(r["ours"]), "walked": int(r["walked"])}
    return {}


def splice_doc(path, block, markers):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    i, j = text.find(markers[0]), text.find(markers[1])
    if i < 0 or j < 0:
        return None
    new = text[:i] + markers[0] + "\n" + block + "\n" + markers[1] + text[j + len(markers[1]):]
    if new == text:
        return text
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    return new


def self_check():
    """Synthetic: the buckets, the render, the round trip, and a name that moves when its chain does."""
    bad = []
    funcs = {"walking": {"pawn": {""}}, "ticking": {"pawn": {"botidle"}}, "away": {"pawn": {""}},
             "remove": {}}
    props = {"health": {"pawn": {""}}, "bdemoowner": {"pawn": {""}}}
    chain_pawn = ["pawn", "controller", "actor", "object"]
    # every verdict, from one table
    cases = [("walking", chain_pawn, False, "on-chain"), ("ticking", chain_pawn, False, "state"),
             ("away", ["actor", "object"], False, "off-chain"), ("remove", chain_pawn, False, "undeclared"),
             ("bdemoowner", chain_pawn, True, "on-chain"), ("walking", chain_pawn, True, "on-chain")]
    for nm, chain, read, want in cases:
        got = bucket(nm, chain, funcs, props, read=read)
        if got != want:
            bad.append("bucket(%s, read=%s) = %s, wanted %s" % (nm, read, got, want))
    # a member read of a name that is a function and not a property is still placed by the function table
    if bucket("walking", chain_pawn, funcs, props, read=True) != "on-chain":
        bad.append("a function-only member read did not fall back to the function table")
    rows = [{"reason": "a member call statement to a name the receiver's chain does not declare",
             "member": "walking", "receiver": "pawn", "class": "Probe", "function": "A"},
            {"reason": "a call to a name the class chain does not declare",
             "member": "away", "receiver": "", "class": "Probe", "function": "B"},
            {"reason": "a member that is not a property of the receiver's class",
             "member": "remove", "receiver": "pawn", "class": "Probe", "function": "C"},
            {"reason": "a call statement whose receiver's class the artifacts do not name",
             "member": "walking", "receiver": "", "class": "Probe", "function": "D"},
            # a member read the walk could not name a receiver for: no chain was ever asked, so it is `unnamed`
            # with its own reason, never a bucket on an empty chain
            {"reason": "a member that is not a property of the receiver's class",
             "member": "health", "receiver": "", "class": "Probe", "function": "E",
             "why": "an element of an array the artifacts do not type"}]
    # the bare call walks its own class's chain (short), the member call the receiver's (long)
    counter, ex = classify(rows, lambda c: chain_pawn if c == "pawn" else ["actor", "object"], funcs, props)
    want = {("member call, statement", "on-chain", ""): 1, ("bare call", "off-chain", ""): 1,
            ("member read", "undeclared", ""): 1, ("member call, no receiver class", "unnamed", ""): 1,
            ("member read", "unnamed", "an element of an array the artifacts do not type"): 1}
    if dict(counter) != want:
        bad.append("classify() = %s, wanted %s" % (dict(counter), want))
    out = rows_of(counter, ex)
    block = render(out, {"modelled": 9, "walked": 11})
    if "5 refused bodies" not in block or "| member call, statement | 1 |" not in block:
        bad.append("the render does not carry the families it was handed")
    if "| an element of an array the artifacts do not type | 1 |" not in block:
        bad.append("the render does not split the unnamed population by its reason")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "emit_receiver_join.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerow(dict(zip(FIELDS, ["(census)", "", "", ""])))
            for r in out:
                w.writerow(r)
        back, _census = read_artifact(path)
        if render(back, {"modelled": 9, "walked": 11}) != block:
            bad.append("the render does not reproduce from the written artifact")
        # the emitter writes its census marker in `package`; a reader keyed on `class` answers 0 of 0 and the
        # page's headline then contradicts the artifact it quotes
        epath = os.path.join(tmp, "uscript_emit.csv")
        with open(epath, "w", newline="", encoding="utf-8") as fh:
            fh.write("package,class,state,function,model,rettype,source,ours,theirs,verdict,walked,differ\n")
            fh.write("(census),,,,census,,,9,9,modelled/identical,11,0\n")
        got = emit_census(epath)
        if got != {"modelled": 9, "walked": 11}:
            bad.append("emit_census read %s from a census row written in `package`" % (got,))
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    for line in bad:
        print("FAIL receiver-join: %s" % line)
    if not bad:
        print("ok   receiver-join: the five buckets, the function fallback, the classify, the render and its "
              "round trip")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--residue")
    ap.add_argument("--tree")
    ap.add_argument("--emit", default=EMIT)
    ap.add_argument("-o", "--out-dir")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        rows, _census = read_artifact(OUT)
        if not rows:
            print("FAIL receiver-join: %s is missing" % OUT)
            return 1
        block = render(rows, emit_census(args.emit))
        with open(args.check_doc, encoding="utf-8") as fh:
            text = fh.read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL receiver-join: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL receiver-join: %s's block does not reproduce from the committed CSV" % args.check_doc)
            return 1
        print("ok   receiver-join: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, _census = read_artifact(OUT)
        if not rows:
            print("FAIL receiver-join: %s is missing" % OUT)
            return 1
        block = render(rows, emit_census(args.emit))
        if args.report:
            print(block)
            return 0
        if splice_doc(args.write_doc, block, DOC_A) is None:
            print("FAIL receiver-join: %s has no %s markers" % (args.write_doc, DOC_A[0]))
            return 1
        print("wrote %s (%s)" % (args.write_doc, DOC_A[0]))
        return 0
    if not (args.residue and args.tree):
        ap.error("--residue and --tree are required to join (or use --report/--check-doc/--check)")
    emit = _emitter()
    chain = emit._chain()
    resid = list(csv.DictReader(open(args.residue, newline="", encoding="utf-8")))
    # the package a class lives in, so a receiver declared elsewhere still starts its walk at home
    pkgs = collections.defaultdict(set)
    with open(os.path.join(ROOT, "inventory", "classes.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            pkgs[r["class"].lower()].add(r["package"].lower())

    def chain_of(cls):
        if not cls:
            return []
        pack = getattr(emit, "_CLASS_PKG", {}).get(cls, "")
        return [c for _p, c in emit._ancestors(chain, pack, cls)]

    funcs, props = declarations(args.tree, emit)
    counter, ex = classify(resid, chain_of, funcs, props)
    rows = rows_of(counter, ex)
    census = emit_census(args.emit)
    out_dir = args.out_dir or os.path.join(ROOT, "inventory")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "emit_receiver_join.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerow({"family": "(census)", "where": "", "bodies": str(sum(int(r["bodies"]) for r in rows)),
                    "examples": "%d modelled of %d walked" % (census.get("modelled", 0), census.get("walked", 0))})
        for r in rows:
            w.writerow(r)
    print("joined %d refused bodies over %d families" % (sum(int(r["bodies"]) for r in rows), len(rows)))
    for r in rows:
        print("  %-34s %-10s %5s  %s" % (r["family"], r["where"], r["bodies"], r["examples"]))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
