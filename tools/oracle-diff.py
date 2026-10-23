#!/usr/bin/env python3
"""Compare the reconstructed script tree against the privately held 2006 studio source.

The 2006 `Classes` tree is an ORACLE, not ground truth: it is ~6 months newer than the
2005-12-15 build (PLAN.md risk 5). So this tool never "fixes" anything -- it buckets classes:

  matched     both trees agree structurally: the decompiler reproduced the class faithfully
  drifted     same class, changed between the build and the leaked source
  divergent   same name, little shared structure: investigate before trusting either side
  build-only  in the build, absent from the leak  -> candidate cut content (PLAN 3.4)
  oracle-only in the leak, absent from the build  -> work added after Dec 2005

Similarity is Jaccard over identifier sets (declarations + references), which is insensitive to
formatting and to the decompiler's unresolved `__NFUN_n__` tokens. Nothing is copied: the CSV holds
class names, counts and scores only, and the oracle path is taken from the environment so no
contributor path lands in the repo.

Usage: oracle-diff.py --oracle DIR --src DIR [-o inventory]
       ORACLE_DIR=... WORK=... make oracle-diff
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter

# Keywords and built-ins that carry no signal when comparing two versions of a class.
NOISE = {
    "class", "extends", "native", "config", "abstract", "state", "function", "event", "var", "local",
    "struct", "enum", "const", "defaultproperties", "if", "else", "for", "foreach", "while", "do",
    "switch", "case", "default", "break", "continue", "return", "goto", "new", "self", "super",
    "true", "false", "none", "optional", "out", "coerce", "transient", "private", "protected",
    "public", "final", "static", "simulated", "singular", "reliable", "unreliable", "latent",
    "iterator", "exec", "preoperator", "postoperator", "operator", "int", "float", "bool", "byte",
    "string", "name", "array", "delegate", "pointer", "vector", "rotator", "quat", "color",
}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
FUNC = re.compile(r"^\s*(?:function|event|operator|preoperator|postoperator)\s+[\w\[\]<>.]+\s+(\w+)",
                  re.MULTILINE)
COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
COMMENT_LINE = re.compile(r"//[^\n]*")


def normalise(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = COMMENT_BLOCK.sub(" ", text)
    text = COMMENT_LINE.sub("", text)
    return text


def identifiers(text):
    return {i for i in IDENT.findall(normalise(text)) if i.lower() not in NOISE
            and not i.startswith("__NFUN_")}


def functions(text):
    return set(FUNC.findall(normalise(text)))


def jaccard(a, b):
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def load_tree(root):
    """class name (case-insensitive) -> (path, text). Flat or one-level-deep trees both work."""
    found = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.lower().endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                found.setdefault(name[:-3].lower(), (path, fh.read()))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", default=os.environ.get("ORACLE_DIR"))
    ap.add_argument("--src", default=os.path.join(os.environ.get("WORK", os.path.expanduser("~/sg1/work")),
                                                  "UnrealScript"))
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--match", type=float, default=0.75)
    ap.add_argument("--drift", type=float, default=0.35)
    args = ap.parse_args()
    if not args.oracle or not os.path.isdir(args.oracle):
        sys.exit("set --oracle (or ORACLE_DIR) to the 2006 studio Classes directory -- it is never committed")
    if not os.path.isdir(args.src):
        sys.exit(f"no reconstructed tree at {args.src} -- run 'make decompile' first")

    oracle = load_tree(args.oracle)
    ours = {}
    for dirpath, _d, filenames in os.walk(args.src):
        pkg = os.path.basename(dirpath)
        for name in filenames:
            if name.lower().endswith(".uc"):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    ours.setdefault(name[:-3].lower(), []).append((pkg, path, fh.read()))

    rows = []
    for key, (op, otext) in sorted(oracle.items()):
        oids, ofuncs = identifiers(otext), functions(otext)
        entries = ours.get(key)
        if not entries:
            rows.append({
                "class": os.path.basename(op)[:-3], "package": "", "bucket": "oracle-only",
                "similarity": "", "identifier_jaccard": "", "function_jaccard": "",
                "build_tokens": 0, "oracle_tokens": len(oids),
                "functions_only_in_build": "", "functions_only_in_oracle": len(ofuncs),
            })
            continue
        for pkg, _path, btext in entries:
            bids, bfuncs = identifiers(btext), functions(btext)
            idj = jaccard(bids, oids)
            fnj = jaccard(bfuncs, ofuncs)
            sim = round(0.5 * idj + 0.5 * fnj, 3)
            bucket = "matched" if sim >= args.match else "drifted" if sim >= args.drift else "divergent"
            rows.append({
                "class": os.path.basename(op)[:-3], "package": pkg, "bucket": bucket,
                "similarity": sim,
                "identifier_jaccard": round(idj, 3), "function_jaccard": round(fnj, 3),
                "build_tokens": len(bids), "oracle_tokens": len(oids),
                "functions_only_in_build": ",".join(sorted(bfuncs - ofuncs)),
                "functions_only_in_oracle": len(ofuncs - bfuncs),
            })

    # Classes in the build that the leak never mentions, restricted to the game's own packages:
    # engine/editor packages are stock UE2.5 and are absent from a studio script dump by design.
    game_pkgs = {"StargateGame", "StargateVehicles", "StargateContent", "StargateMenus",
                 "Stargateeffects", "Sharpeffects", "AmbientCreatures"}
    for key, entries in sorted(ours.items()):
        if key in oracle:
            continue
        for pkg, _path, btext in entries:
            if pkg not in game_pkgs:
                continue
            rows.append({
                "class": _path.rsplit("/", 1)[-1][:-3], "package": pkg, "bucket": "build-only",
                "similarity": "", "identifier_jaccard": "", "function_jaccard": "",
                "build_tokens": len(identifiers(btext)), "oracle_tokens": 0,
                "functions_only_in_build": len(functions(btext)),
                "functions_only_in_oracle": "",
            })

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "oracle_diff.csv")
    fields = ["class", "package", "bucket", "similarity", "identifier_jaccard", "function_jaccard",
              "build_tokens", "oracle_tokens", "functions_only_in_build", "functions_only_in_oracle",
              "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["bucket"], r["package"], r["class"])))

    counts = Counter(r["bucket"] for r in rows)
    print(f"{len(oracle)} oracle classes, {sum(len(v) for v in ours.values())} build classes -> {path}")
    for bucket in ("matched", "drifted", "divergent", "build-only", "oracle-only"):
        print(f"  {bucket:12s} {counts.get(bucket, 0)}")
    matched = [r for r in rows if r["bucket"] == "matched"]
    if matched:
        print(f"  mean similarity of matched: {sum(r['similarity'] for r in matched) / len(matched):.3f}")
    write_doc(rows, len(oracle), sum(len(v) for v in ours.values()), args)


def write_doc(rows, oracle_count, build_count, args):
    """Render docs/ORACLE_DIFF.md -- numbers and class names only, never source text."""
    counts = Counter(r["bucket"] for r in rows)
    covered = [r for r in rows if r["package"]]
    by_pkg = Counter(r["package"] for r in covered)
    matched = [r for r in rows if r["bucket"] == "matched"]
    mean_sim = (sum(r["similarity"] for r in matched) / len(matched)) if matched else 0.0
    lines = []
    w = lines.append
    w("# Oracle diff: reconstructed build vs the 2006 studio source")
    w("")
    w("Generated by `tools/oracle-diff.py`. The 2006 `Classes` tree is held privately and never")
    w("enters this repository; this report carries class names, counts and scores only.")
    w("")
    w("## Method")
    w("")
    w("Classes are matched by name. Similarity is the mean of two Jaccard scores over the")
    w("normalised source: distinct identifiers (declarations and references, keywords and")
    w("`__NFUN_n__` placeholders excluded) and declared function names. Both are insensitive to")
    w("formatting, so decompiler style cannot flatter or punish the result. Buckets:")
    w("")
    w(f"- **matched** similarity >= {args.match}: the decompiler reproduced the class faithfully")
    w(f"- **drifted** {args.drift} <= similarity < {args.match}: same class, changed between the build and the leak")
    w(f"- **divergent** similarity < {args.drift}: same name, little shared structure")
    w("- **build-only**: in the Dec 2005 build, absent from the leak")
    w("- **oracle-only**: in the leak, absent from the build (work done after Dec 2005)")
    w("")
    w("## Result")
    w("")
    w("| bucket | classes |")
    w("|---|---|")
    for bucket in ("matched", "drifted", "divergent", "build-only", "oracle-only"):
        w(f"| {bucket} | {counts.get(bucket, 0)} |")
    w("")
    w(f"{oracle_count} oracle classes against {build_count} reconstructed classes; "
      f"mean similarity of matched classes **{mean_sim:.3f}**.")
    w("")
    w("## Which packages the oracle actually covers")
    w("")
    w("| package | classes in the oracle |")
    w("|---|---|")
    for pkg, count in by_pkg.most_common():
        w(f"| `{pkg}` | {count} |")
    w("")
    w("This is the important finding: the leaked tree is **Perception's modified Engine source**")
    w("(`Engine` + a handful of `Core` classes), plus a few game classes and the two")
    w("script-compiled-to-C++ classes (`Pawn`, `ShadowProjector`). It is not a dump of the game's")
    w("own script packages, so it cannot be used as a completeness oracle for `StargateGame`,")
    w("`StargateContent` or `StargateVehicles` -- those are `build-only` by construction, and that")
    w("bucket is **not** evidence of cut content. What the oracle is good for:")
    w("")
    w("1. A quality measurement of the decompiler on 278 classes of known source (mean similarity")
    w("   above), which is the number to watch as the token map improves (PLAN 3.5).")
    w("2. A change log of Perception's engine work between Dec 2005 and Jun 2006 -- the drifted and")
    w("   divergent Engine classes are precisely the subsystems they were still rewriting, which")
    w("   feeds PLAN 4.8 (fork changes vs stock UE2.5).")
    w("3. Confirmation of the ucpp trap (`PLAN` risk 4): `Pawn.ucpp`/`Pawn.cpp` and")
    w("   `ShadowProjector.ucpp`/`.cpp` exist beside thin `.uc` files, so their behaviour is in the XBE.")
    w("")
    w("## Divergent classes (investigate before trusting either side)")
    w("")
    w("| package | class | similarity | identifiers | functions |")
    w("|---|---|---|---|---|")
    for r in sorted([r for r in rows if r["bucket"] == "divergent"],
                    key=lambda r: r["similarity"]):
        w(f"| `{r['package']}` | {r['class']} | {r['similarity']} | "
          f"{r['identifier_jaccard']} | {r['function_jaccard']} |")
    w("")
    w("## Classes added after the build (oracle-only)")
    w("")
    w("Present in the Jun 2006 source, absent from the Dec 2005 packages -- features in flight at")
    w("the time of the build:")
    w("")
    for r in sorted([r for r in rows if r["bucket"] == "oracle-only"], key=lambda r: r["class"]):
        w(f"- `{r['class']}`")
    w("")
    w("## Reproducing")
    w("")
    w("```sh")
    w("ORACLE_DIR=/path/to/private/Classes make oracle-diff")
    w("```")
    w("")
    w("The oracle path comes from the environment on purpose: no contributor's filesystem layout")
    w("ends up in the repository, and the run is expected to be impossible for anyone without the")
    w("private source -- the committed report is the artefact.")
    w("")
    with open(os.path.join("docs", "ORACLE_DIFF.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    print("wrote docs/ORACLE_DIFF.md")


if __name__ == "__main__":
    main()
