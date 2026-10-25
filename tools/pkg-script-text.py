#!/usr/bin/env python3
"""Extract and characterise the `ScriptText` a class ships (PLAN 4.7, docs/PACKAGE_SCRIPT_TEXT.md).

Every class carries its original UnrealScript source as a `TextBuffer` export. This reads it, and can either
write it out (to `$WORK`, never into this repository) or reduce it to countable facts: lines, comments, and
the `native(...)` declarations that say which functions are implemented in C++ and under which index.

    python3 tools/pkg-script-text.py PKG.u --class SGOrder --write $WORK/scripttext
    python3 tools/pkg-script-text.py PKG.u --class SGOrder --stats
    python3 tools/pkg-script-text.py PKG.u --all --stats -o inventory   # every class in the package

The text is game-derived: `--write` targets are the caller's problem, and nothing here prints source to stdout
unless `--print` is given explicitly.
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

# Native functions are declared with the `native` keyword, not with an index: the index lives in the
# function's own record inside the package. A class that is itself native says `class X extends Y ... native;`
# on its declaration line, which is not a function and is counted separately.
# A native function declaration can put `native` anywhere in the modifier list and may carry an explicit
# index (`native(1234)`). Matching on "the line mentions native and declares a function" is what survives the
# real spellings; the index, when present, is captured with the name.
NATIVE_FN = re.compile(r"\bnative\b(?:\s*\(\s*(\d+)\s*\))?[^\n;]*?\bfunction\s+"
                       r"(?:[\w<>\[\]]+\s+)?(\w+)\s*\(", re.IGNORECASE)
CLASS_NATIVE = re.compile(r"^\s*class\s+\w+.*\bnative\s*;", re.IGNORECASE | re.MULTILINE)
FUNC_START = re.compile(r"^\s*(?:simulated\s+|static\s+|final\s+|latent\s+)*function\s+(?:[\w<>\[\]]+\s+)?(\w+)\s*\(",
                        re.IGNORECASE)
CLASS_DECL = re.compile(r"^\s*class\s+(\w+)(?:\s+extends\s+(\w+))?", re.IGNORECASE)


def script_text(pkg, export, head=4096):
    """Return the class's ScriptText as a string, or None.

    The export's byte range starts with the object's property stream, not the text, so the text is located by
    its own first token rather than by an assumed offset: `class <Name>` or a leading comment. That is a
    heuristic and it is stated here rather than hidden; `--stats` reports where the text was found so a wrong
    start is visible as an implausible line count.
    """
    idx = export["index"] + 1
    for kid in pkg.exports:
        if kid["outer"] != idx:
            continue
        if pkg.class_name_of(kid) == "TextBuffer" and pkg.name(kid["name"]) == "ScriptText":
            raw = pkg.data[kid["offset"]:kid["offset"] + kid["size"]]
            starts = [i for i in (raw.find(b"class ", 0, head), raw.find(b"//", 0, head)) if i >= 0]
            if not starts:
                return None
            start = min(starts)
            text = raw[start:].decode("latin1", "replace")
            # the TextBuffer stores a trailing NUL run; cut at the last printable character
            return text.rstrip("\x00")
    return None


def stats(text):
    lines = text.splitlines()
    comments = sum(1 for l in lines if l.strip().startswith("//") or l.strip().startswith("/*"))
    native_hits = NATIVE_FN.findall(text)
    native_fns = [name for _idx, name in native_hits]
    natives = native_fns
    funcs = [m.group(1) for m in (FUNC_START.match(l) for l in lines) if m]
    decl = CLASS_DECL.match(lines[0]) if lines else None
    return {"lines": len(lines), "bytes": len(text), "comment_lines": comments,
            "native_count": len(native_fns), "native_functions": ",".join(native_fns),
            "native_indices": ",".join(i for i, _n in native_hits if i),
            "class_is_native": bool(CLASS_NATIVE.search(text)),
            "funcs_declared": len(funcs),
            "declares_class": bool(decl), "class_decl": decl.group(1) if decl else "",
            "extends": (decl.group(2) or "") if decl else ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--class", dest="cls", help="one class")
    ap.add_argument("--all", action="store_true", help="every class in the package")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--write", help="directory to write .uc files into (use $WORK, never the repo)")
    ap.add_argument("--print", dest="show", action="store_true", help="print the text (debugging only)")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    pkg = Package(args.package)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()

    classes = [e for e in pkg.exports
               if pkg.class_name_of(e) == "Class" and (args.all or pkg.name(e["name"]) == args.cls)]
    if not classes:
        sys.exit("no matching class in %s" % args.package)

    rows = []
    for exp in classes:
        name = pkg.name(exp["name"])
        text = script_text(pkg, exp)
        if text is None:
            print("%-30s no ScriptText" % name)
            continue
        if args.write:
            os.makedirs(args.write, exist_ok=True)
            with open(os.path.join(args.write, name + ".uc"), "w") as f:
                f.write(text)
        if args.show:
            print(text)
        if args.stats:
            s = stats(text)
            s["class"] = name
            rows.append(s)
            print("%-30s lines=%-5d natives=%-2d class_native=%-5s funcs=%-3d extends=%s"
                  % (name, s["lines"], s["native_count"], s["class_is_native"],
                     s["funcs_declared"], s["extends"]))
    if args.out and rows:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "script_text_stats.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["class", "lines", "bytes", "comment_lines",
                                              "native_count", "native_functions", "native_indices", "class_is_native",
                                              "funcs_declared", "declares_class", "class_decl", "extends"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
