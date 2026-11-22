#!/usr/bin/env python3
"""How far each class is from compiling clean (PLAN 3.6 L1), aggregated from the emitter's own verdicts.

L1 is "the class compiles clean", and `docs/STATUS.md` prints `--` for it because nobody has measured the distance. The
emitter already decides every body: `inventory/uscript_emit.csv` holds the ones it models and `--residue-out` writes the
ones it refuses, each with its reason. This tool joins the two per class — it does not parse anything itself, so it cannot
drift from the emitter.

    python3 tools/uscript-emit.py --tree $WORK/scripts-all --dump $WORK/dump/System \
        -o $WORK/emit --residue-out $WORK/residue
    python3 tools/l1-ready.py --emit $WORK/emit/uscript_emit.csv --residue $WORK/residue/residue.csv -o inventory
    python3 tools/l1-ready.py --check          # synthetic inputs, no corpus

`inventory/l1_ready.csv` is one row per class: bodies, modelled, refused, and the share. The summary separates the three
sets that matter — classes already fully covered, classes exactly one refusal away, and the rest — because "one shape
away" is where the next unit of emitter work pays for itself.
"""

import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["class", "bodies", "modelled", "refused", "coverage", "first_reason"]


def load(emit_path, residue_path):
    modelled, refused, first_reason = collections.Counter(), collections.Counter(), {}
    for row in csv.DictReader(open(emit_path, newline="")):
        cls = row.get("class") or ""
        if cls and cls != "(census)":
            modelled[cls] += 1
    for row in csv.DictReader(open(residue_path, newline="")):
        cls = row.get("class") or ""
        if not cls:
            continue
        refused[cls] += 1
        first_reason.setdefault(cls, (row.get("reason") or "").split("(")[0].split(",")[0].strip())
    return modelled, refused, first_reason


def summarise(modelled, refused, first_reason):
    classes = set(modelled) | set(refused)
    rows = []
    for cls in sorted(classes):
        bodies = modelled[cls] + refused[cls]
        rows.append({"class": cls, "bodies": bodies, "modelled": modelled[cls],
                     "refused": refused[cls],
                     "coverage": "%.1f" % (100.0 * modelled[cls] / bodies if bodies else 0.0),
                     "first_reason": first_reason.get(cls, "")})
    clean = [r for r in rows if r["refused"] == 0]
    one = [r for r in rows if r["refused"] == 1]
    summary = {
        "classes": len(rows),
        "bodies": sum(r["bodies"] for r in rows),
        "modelled": sum(r["modelled"] for r in rows),
        "refused": sum(r["refused"] for r in rows),
        "clean": len(clean),
        "clean_bodies": sum(r["bodies"] for r in clean),
        "one": len(one),
        "one_bodies": sum(r["bodies"] for r in one),
    }
    return rows, summary


def run(emit_path, residue_path, out_dir):
    modelled, refused, first_reason = load(emit_path, residue_path)
    rows, s = summarise(modelled, refused, first_reason)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "l1_ready.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print("%d class(es), %d bod(ies): %d modelled, %d refused"
          % (s["classes"], s["bodies"], s["modelled"], s["refused"]))
    print("   fully covered today: %d class(es), %d bod(ies)" % (s["clean"], s["clean_bodies"]))
    print("   one refusal away:    %d class(es), %d bod(ies)" % (s["one"], s["one_bodies"]))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


SYNTH_EMIT = """package,class,state,function,model,rettype,source,ours,theirs,verdict,walked,differ
(census),,,,census,,,3,3,modelled/identical,5,0
Engine.u,Alpha,,One,return,bool,,0412,0412,identical,,
Engine.u,Alpha,,Two,return,int,,0413,0413,identical,,
Engine.u,Beta,,Only,return,bool,,0414,0414,identical,,
"""

SYNTH_RESIDUE = """package,class,state,function,rettype,reason,source,theirs
Engine.u,Beta,,Blocked,bool,"a nested block: control flow is not modelled",,00
Engine.u,Gamma,,Solo,,3 statements,,00
Engine.u,Gamma,,Other,,2 statements,,00
"""


def check(_args):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        e = os.path.join(tmp, "e.csv")
        r = os.path.join(tmp, "r.csv")
        open(e, "w").write(SYNTH_EMIT)
        open(r, "w").write(SYNTH_RESIDUE)
        modelled, refused, first = load(e, r)
        rows, s = summarise(modelled, refused, first)
        # 3 modelled (Alpha x2, Beta x1) + 3 refused (Beta x1, Gamma x2)
        want = {"classes": 3, "bodies": 6, "modelled": 3, "refused": 3, "clean": 1, "one": 1}
        for key, value in want.items():
            if s[key] != value:
                print("FAIL l1-ready: %s is %s, expected %s" % (key, s[key], value))
                return 1
        alpha = next(x for x in rows if x["class"] == "Alpha")
        if alpha["coverage"] != "100.0":
            print("FAIL l1-ready: Alpha coverage read %s" % alpha["coverage"])
            return 1
        beta = next(x for x in rows if x["class"] == "Beta")
        if beta["first_reason"] != "a nested block: control flow is not modelled":
            print("FAIL l1-ready: Beta's first reason read %r" % beta["first_reason"])
            return 1
    print("ok l1-ready: 3 classes, 1 clean, 1 a refusal away, reasons carried")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--emit")
    ap.add_argument("--residue")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not args.emit or not args.residue:
        print("FAIL l1-ready: --emit and --residue are both required (the emitter's own output)")
        return 2
    return run(args.emit, args.residue, args.out)


if __name__ == "__main__":
    sys.exit(main())
