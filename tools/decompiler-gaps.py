#!/usr/bin/env python3
"""Where the reconstructed tree is thinner than the shipped source (PLAN 3.5's decompiler gaps).

Every package ships its own `ScriptText`, so the reconstruction has an oracle for *structure* even where it has none
for text: a class whose source declares `replication { ... }`, four states and a latent function, and whose
reconstruction declares none, is a measured gap rather than an impression.

    python3 tools/decompiler-gaps.py SOURCE_TREE DECOMPILED_TREE -o inventory

Both trees are game-derived and live under `$WORK`; only the counts are committed. The three features here are the ones
3.5 still lists: `replication` blocks, state code and latent calls. Comments are stripped before counting, because a
commented-out `replication` is not a block.
"""
import argparse
import csv
import os
import re
import sys

COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
FEATURES = {
    "replication": re.compile(r"\breplication\b\s*\{"),
    "states": re.compile(r"(?m)^[ \t]*(?:auto\s+)?state\s+\w+"),
    "latent": re.compile(r"\blatent\b"),
}


def count(text):
    text = COMMENT.sub("", text)
    return {name: len(rx.findall(text)) for name, rx in FEATURES.items()}


def tree(root):
    out = {}
    for pkg in sorted(os.listdir(root)):
        path = os.path.join(root, pkg)
        if not os.path.isdir(path):
            continue
        for name in sorted(os.listdir(path)):
            if name.endswith(".uc"):
                out[(pkg, name[:-3])] = os.path.join(path, name)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", help="the shipped source tree (tools/pkg-script-text.py --tree)")
    ap.add_argument("decompiled", help="the reconstructed tree (Sg1Tool decompile)")
    ap.add_argument("-o", "--out", help="write decompiler_gaps.csv here")
    args = ap.parse_args()

    src, dec = tree(args.source), tree(args.decompiled)
    shared = sorted(set(src) & set(dec))
    rows = []
    for key in shared:
        a = count(open(src[key], encoding="latin1").read())
        b = count(open(dec[key], encoding="latin1").read())
        rows.append((key[0], key[1]) + tuple(a[f] for f in FEATURES) + tuple(b[f] for f in FEATURES))

    print("%d classes in the source tree, %d in the reconstruction, %d in both" % (len(src), len(dec), len(shared)))
    print("%-12s %8s %8s %8s" % ("feature", "source", "ours", "classes with none of ours"))
    for i, feature in enumerate(FEATURES):
        have = sum(r[2 + i] for r in rows)
        ours = sum(r[5 + i] for r in rows)
        missing = sum(1 for r in rows if r[2 + i] and not r[5 + i])
        print("%-12s %8d %8d %8d" % (feature, have, ours, missing))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "decompiler_gaps.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["package", "class"] + ["source_" + f for f in FEATURES]
                       + ["decompiled_" + f for f in FEATURES])
            for row in rows:
                w.writerow(row)
        print("wrote %s/decompiler_gaps.csv (%d classes)" % (args.out, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
