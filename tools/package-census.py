#!/usr/bin/env python3
"""Regenerate the package-side census artifacts, so every number in them has a committed generator.

Four artifacts that `docs/ARTIFACTS.md` flagged as having no generator: `package_exports_summary.csv`,
`package_script_text.csv`, `script_text_natives.csv`, `native_declarations.csv`. They were produced by one-off
scripts that were never committed, which means nothing rebuilt them and any doc quoting them quoted a frozen
number. This is that generator, and `--check` proves it reproduces the committed files byte for byte.

    python3 tools/package-census.py DUMP/.../System -o inventory
    python3 tools/package-census.py DUMP/.../System --check

Definitions, so the numbers mean something specific:

* `names` is the package's name-table count as the header records it; `exports` and `imports` are table lengths;
  `classes` counts exports whose class is `Class`.
* `script_text_exports` counts class exports whose `ScriptText` child yielded text, and `script_text_bytes` is
  the length of that text as decoded — the same locator `tools/pkg-script-text.py` uses, imported rather than
  reimplemented.
* `natives_in_source` counts `native` function declarations in the shipped text; `native_functions_in_inventory`
  is the same package's total from `inventory/classes.csv`, and the two disagree wherever the inventory counts
  inherited or operator natives (a disagreement the doc records rather than smoothing).
* `native_declarations` lists the fork's own C++ natives: classes in `StargateGame` whose *source* declares at
  least one native function, in export order.
"""
import argparse
import csv
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECLARATION_PACKAGE = "StargateGame"


def load_script_text():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pkg-script-text.py")
    spec = importlib.util.spec_from_file_location("pkg_script_text", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def inventory_natives(path=os.path.join(ROOT, "inventory", "classes.csv")):
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out[r["package"]] = out.get(r["package"], 0) + int(r["native_functions"] or 0)
    return out


def write_csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system_dir", help="the dump's System directory")
    ap.add_argument("-o", "--out", help="directory holding the CSVs (default: inventory/)")
    ap.add_argument("--check", action="store_true", help="compare instead of writing; non-zero if different")
    args = ap.parse_args()

    st = load_script_text()
    outdir = args.out or os.path.join(ROOT, "inventory")
    inv = inventory_natives()

    exports_rows, text_rows, native_rows, decl_rows = [], [], [], []
    for fname in sorted(f for f in os.listdir(args.system_dir) if f.lower().endswith(".u")):
        path = os.path.join(args.system_dir, fname)
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        classes = [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]

        exports_rows.append([pkg_name, pkg.version, len(pkg.names), len(pkg.exports),
                             len(pkg.imports), len(classes)])

        found, byte_count, natives_in_source = 0, 0, 0
        for exp in classes:
            text = st.script_text(pkg, exp)
            if text is None:
                continue
            found += 1
            byte_count += len(text)
            declared = st.NATIVE_FN.findall(text)
            natives_in_source += len(declared)
            if pkg_name == DECLARATION_PACKAGE and declared:
                names = [m[1] if isinstance(m, tuple) else m for m in declared]
                decl_rows.append([pkg.name(exp["name"]), len(names), ";".join(names), "shipped ScriptText"])
        text_rows.append([pkg_name, len(classes), found, byte_count])
        native_rows.append([pkg_name, len(classes), natives_in_source, inv.get(pkg_name, 0)])

    files = [
        ("package_exports_summary.csv",
         ["package", "version", "names", "exports", "imports", "classes"], exports_rows),
        ("package_script_text.csv",
         ["package", "classes", "script_text_exports", "script_text_bytes"], text_rows),
        ("script_text_natives.csv",
         ["package", "classes", "natives_in_source", "native_functions_in_inventory"], native_rows),
        ("native_declarations.csv",
         ["class", "native_functions", "native_function_names", "from"], decl_rows),
    ]

    bad = []
    for name, header, rows in files:
        path = os.path.join(outdir, name)
        if args.check:
            import io
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
            want = buf.getvalue()
            try:
                have = open(path, encoding="utf-8").read()
            except FileNotFoundError:
                have = None
            if have != want:
                bad.append(name)
            print("%-4s %s (%d rows)" % ("ok" if have == want else "DIFF", name, len(rows)))
        else:
            write_csv(path, header, rows)
            print("wrote %s (%d rows)" % (path, len(rows)))
    if args.check:
        if bad:
            print("\nstale: %s -- a doc quoting these numbers may be wrong" % ", ".join(bad))
            return 1
        print("\nall four reproduce byte for byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
