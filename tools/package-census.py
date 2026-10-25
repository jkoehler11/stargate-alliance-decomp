#!/usr/bin/env python3
"""Regenerate the package-side census artifacts, so every number in them has a committed generator.

Four artifacts that `docs/ARTIFACTS.md` flagged as having no generator: `package_exports_summary.csv`,
`package_script_text.csv`, `script_text_natives.csv`, `native_declarations.csv`. They were produced by one-off
scripts that were never committed, so nothing rebuilt them and any doc quoting them quoted a frozen number.
`--check` proves this tool reproduces the committed files byte for byte.

    python3 tools/package-census.py DUMP/.../System -o inventory
    python3 tools/package-census.py DUMP/.../System --check

Definitions, because a census is only useful if each column means one thing:

* `names`, `exports`, `imports` are the package header's own counts; `classes` counts exports whose class is
  `Class`.
* `script_text_exports` counts class exports whose `ScriptText` child yielded text, and `script_text_bytes` is
  the size of that `TextBuffer` export — the stored bytes, not the decoded string. The locator is
  `tools/pkg-script-text.py`'s, imported rather than reimplemented.
* `natives_in_source` counts `native` function declarations in the shipped text, while
  `native_functions_in_inventory` is that package's total from `inventory/classes.csv`. They disagree wherever the
  inventory counts inherited or operator natives, and the disagreement is the point of the column.
* `native_declarations` covers the fork's own package (`StargateGame`) and lists the *own* C++ natives: a native
  function declared by a class whose ancestors
  do not declare it. An override of a stock native (`BotBase.CoverActors` re-declaring what `Bot.CoverActors`
  already declares) is not a new native, and that ancestry test is what separates the six classes that add
  natives of their own from the ones that only rename a stock one.
"""
import argparse
import csv
import importlib.util
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_script_text():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pkg-script-text.py")
    spec = importlib.util.spec_from_file_location("pkg_script_text", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_column(name, column, path=os.path.join(ROOT, "inventory", "classes.csv")):
    out = {}
    if os.path.isfile(path):
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                out[r[name]] = r[column]
    return out


def ancestry(cls, supers):
    out, seen = [], set()
    cur = supers.get(cls)
    while cur and cur not in seen:
        seen.add(cur)
        out.append(cur)
        cur = supers.get(cur)
    return out


def size_of_text_buffer(pkg, export):
    """The `ScriptText` child export's stored size."""
    idx = export["index"] + 1
    for kid in pkg.exports:
        if kid["outer"] == idx and pkg.class_name_of(kid) == "TextBuffer" \
                and pkg.name(kid["name"]) == "ScriptText":
            return kid["size"]
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system_dir", help="the dump's System directory")
    ap.add_argument("-o", "--out", help="directory holding the CSVs (default: inventory/)")
    ap.add_argument("--check", action="store_true", help="compare instead of writing; non-zero if different")
    args = ap.parse_args()

    st = load_script_text()
    supers = load_column("class", "super")
    outdir = args.out or os.path.join(ROOT, "inventory")
    inventory_natives = {}
    classes_csv = os.path.join(ROOT, "inventory", "classes.csv")
    if os.path.isfile(classes_csv):
        with open(classes_csv, newline="") as fh:
            for r in csv.DictReader(fh):
                inventory_natives[r["package"]] = inventory_natives.get(r["package"], 0) \
                    + int(r["native_functions"] or 0)

    # Pass 1: read every package once, and remember which native names each class's source declares, because the
    # ancestry test needs ancestors from other packages.
    per_package = []
    declared_by_class = {}
    for fname in sorted(f for f in os.listdir(args.system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(args.system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        classes = [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]
        rows = []
        for exp in classes:
            text = st.script_text(pkg, exp)
            if text is None:
                continue
            cls = pkg.name(exp["name"])
            names = [m[1] if isinstance(m, tuple) else m for m in st.NATIVE_FN.findall(text)]
            declared_by_class[cls] = names
            rows.append((exp["index"], cls, len(names), size_of_text_buffer(pkg, exp)))
        per_package.append((fname[:-2], pkg.version, len(pkg.names), len(pkg.exports),
                            len(pkg.imports), len(classes), rows))

    exports_rows, text_rows, native_rows, decl_rows = [], [], [], []
    for pkg_name, version, names, exports, imports, classes, rows in per_package:
        exports_rows.append([pkg_name, version, names, exports, imports, classes])
        text_rows.append([pkg_name, classes, len(rows), sum(r[3] for r in rows)])
        native_rows.append([pkg_name, classes, sum(r[2] for r in rows), inventory_natives.get(pkg_name, 0)])
        for _index, cls, _n, _bytes in rows:
            own = [n for n in declared_by_class.get(cls, [])
                   if not any(n in declared_by_class.get(a, []) for a in ancestry(cls, supers))]
            if own and pkg_name == DECLARATION_PACKAGE:
                decl_rows.append([cls, len(own), ";".join(own), "shipped ScriptText"])

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

    stale = []
    for name, header, rows in files:
        path = os.path.join(outdir, name)
        if args.check:
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
            try:
                have = open(path, encoding="utf-8").read()
            except FileNotFoundError:
                have = None
            same = have == buf.getvalue()
            if not same:
                stale.append(name)
            print("%-4s %s (%d rows)" % ("ok" if same else "DIFF", name, len(rows)))
        else:
            with open(path, "w", newline="") as fh:
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(header)
                w.writerows(rows)
            print("wrote %s (%d rows)" % (path, len(rows)))
    if args.check and stale:
        print("\nstale: %s" % ", ".join(stale))
        return 1
    if args.check:
        print("\nall four reproduce byte for byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
