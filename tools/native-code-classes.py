#!/usr/bin/env python3
"""Group the named library code by C++ class (PLAN 4.9).

The masked-body and call-edge passes named ~2,850 bodies in the seven library sections. Almost all of
them are MSVC-mangled C++ members, so the *name* already carries the class:

    ?XNetCreateKey@CXn@@QAEHPAUXNKID@@PAUXNKEY@@@Z      class CXn        method XNetCreateKey
    ?IpTerm@CXnIp@@IAEXXZ                               class CXnIp      method IpTerm
    ?Render@CLiveEngine@UIXNamespace@@QAGJPAUD3DSurface@@@Z   namespace UIXNamespace, class CLiveEngine

That makes a class census possible without decompiling anything: per class, how many methods, how many
bytes, which section and which library, and where its first method sits. It is the map the notes in
`docs/NATIVE_NOTES.md` then describe.

    python3 tools/native-code-classes.py --symbols symbols/xbox_functions.csv \\
        -o inventory/native_code_classes.csv --write-doc

`--check` validates the artifact without the dump.
"""

import argparse
import collections
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["namespace", "class", "methods", "bytes", "libs", "modules", "evidence", "first_address"]
C_FUNC = re.compile(r"^_[A-Za-z_][\w.]*(?:@\d+)?$")
MEMBER = re.compile(r"^\?([^@]+)@([^@]+)@(?:([^@]+)@)*")


def demangle(name):
    """(namespace, class, method) for an MSVC-mangled member, else (None, None, None)."""
    if not name.startswith("?") or "@@" not in name:
        return None, None, None
    head = name[:name.index("@@")]
    parts = [p for p in head.split("@") if p]
    if len(parts) < 2:
        return None, None, None
    method, cls = parts[0], parts[1]
    namespace = "::".join(reversed(parts[2:])) if len(parts) > 2 else ""
    return namespace, cls, method


def classify(name):
    """('member', ns, class) | ('c', '', '') | (None, '', '') for anything else."""
    ns, cls, _method = demangle(name)
    if cls:
        return "member", ns, cls
    if C_FUNC.match(name):
        return "c", "", ""
    return None, "", ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbols", default=os.path.join("symbols", "xbox_functions.csv"))
    ap.add_argument("--libs-artifact", default=os.path.join("inventory", "xdk_lib_names.csv"))
    ap.add_argument("-o", "--out", default=os.path.join("inventory", "native_code_classes.csv"))
    ap.add_argument("--doc", default=os.path.join("docs", "NATIVE_NOTES.md"))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write-doc", action="store_true")
    args = ap.parse_args()

    if args.check:
        return check(args)

    rows = list(csv.DictReader(open(args.symbols, encoding="utf-8")))
    libs = {}
    if os.path.isfile(args.libs_artifact):
        for r in csv.DictReader(open(args.libs_artifact, encoding="utf-8")):
            libs[r["address"].lower()] = r
    groups = collections.defaultdict(lambda: {"methods": 0, "bytes": 0,
                                              "modules": set(), "libs": set(),
                                              "first": None, "c_names": set()})
    skipped = collections.Counter()
    for r in rows:
        name = r["name"]
        if name.startswith("FUN_"):
            skipped["unnamed"] += 1
            continue
        module = r.get("module") or ""
        kind, ns, cls = classify(name)
        if kind is None:
            skipped["not C/C++ (exec, ctor_, thunk_, module files)"] += 1
            continue
        librow = libs.get(r["address"].lower())
        if kind == "c":
            key = ("", "(C functions)")
            groups[key]["c_names"].add(name.split("@")[0])
        else:
            key = (ns, cls)
        g = groups[key]
        g["methods"] += 1
        g["bytes"] += int(r["size"])
        if module:
            g["modules"].add(module)
        if librow:
            g["libs"].add(librow["lib"])
        addr = int(r["address"], 16)
        if g["first"] is None or addr < g["first"]:
            g["first"] = addr

    out = []
    for (ns, cls), g in groups.items():
        libs_, mods_ = "/".join(sorted(g["libs"])), "/".join(sorted(g["modules"]))
        out.append({"namespace": ns, "class": cls, "methods": g["methods"],
                    "bytes": g["bytes"], "libs": libs_, "modules": mods_,
                    "evidence": "library" if libs_ else ("module" if mods_ else "name only"),
                    "first_address": "0x%08x" % g["first"] if g["first"] else ""})
    out.sort(key=lambda r: (-r["methods"], -r["bytes"]))
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)

    print("%d groups from %d named functions" % (len(out), sum(r["methods"] for r in out)))
    for reason, n in skipped.most_common():
        print("  skipped, %-46s %5d" % (reason, n))
    for r in out[:10]:
        print("  %-22s %-26s %4d methods %7d B  %s"
              % (r["namespace"] or "-", r["class"], r["methods"], r["bytes"], r["libs"] or r["modules"]))
    if args.write_doc:
        write_doc(args.doc, out)
    return 0


def write_doc(path, rows):
    lines = [
        "",
        "## The library code, by C++ class (2026-11-19)",
        "",
        "Generated from `inventory/native_code_classes.csv` by `tools/native-code-classes.py`",
        "(`make native-code-classes`). The mangled names the library passes recovered carry their own class,",
        "so this is a census of the SDK code's structure rather than a reading of it: methods collected per",
        "class, with the bytes they occupy and the section and library they came from.",
        "",
        "| namespace | class | methods | bytes | library |",
        "|---|---|---:|---:|---|",
    ]
    for r in rows[:40]:
        lines.append("| %s | `%s` | %d | %d | %s |"
                     % (r["namespace"] or "-", r["class"], r["methods"], r["bytes"], r["libs"] or r["modules"]))
    total = sum(r["methods"] for r in rows)
    classes = len([r for r in rows if r["class"] != "(C functions)"])
    c_funcs = next((r for r in rows if r["class"] == "(C functions)"), {"methods": 0})
    lines += [
        "",
        "%d groups in total: **%d C++ classes** and %d C functions. The shape is the networking stack the"
        % (len(rows), classes, c_funcs["methods"]),
        "Xbox SDK ships (`CXnBase` -> `CXnIp`/`CXnNic`/`CXnEnet`), the UIX front-end engine (`CLiveEngine`,",
        "`CScreen`, `CSkinFile`), D3DX's shader assembler and DSOUND's mixer. What each method *does* is",
        "still unknown — this is the index a per-subsystem reading (PLAN 4.9) starts from, not a substitute",
        "for it.",
        "",
    ]
    text = open(path, encoding="utf-8").read()
    marker = "\n## The library code, by C++ class (2026-11-19)"
    if marker in text:
        head, _sep, _rest = text.partition(marker)
        text = head
    open(path, "w", encoding="utf-8").write(text.rstrip("\n") + "\n" + "\n".join(lines))
    print("wrote %s" % path)


def check(args):
    problems = []
    if not os.path.isfile(args.out):
        raise SystemExit("%s missing -- run the generator" % args.out)
    with open(args.out, newline="") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != COLUMNS:
            raise SystemExit("%s columns are %r" % (args.out, rd.fieldnames))
        rows = list(rd)
    if not rows:
        problems.append("no rows")
    for r in rows:
        if int(r["methods"]) < 1:
            problems.append("%s has no methods" % r["class"])
        if r["evidence"] not in ("library", "module", "name only"):
            problems.append("%s has evidence %r" % (r["class"], r["evidence"]))
        if r["evidence"] == "library" and not r["libs"]:
            problems.append("%s claims library evidence with no library" % r["class"])
        if int(r["bytes"]) < int(r["methods"]):
            problems.append("%s claims %s bytes for %s methods" % (r["class"], r["bytes"], r["methods"]))
    seen = collections.Counter((r["namespace"], r["class"]) for r in rows)
    problems.extend("%s::%s appears twice" % k for k, v in seen.items() if v > 1)
    if problems:
        print("native-code-classes: %d problem(s)" % len(problems))
        for p in problems[:10]:
            print("  " + p)
        return 1
    print("ok   native-code-classes: %d groups, %d methods"
          % (len(rows), sum(int(r["methods"]) for r in rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())