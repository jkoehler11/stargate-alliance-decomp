#!/usr/bin/env python3
"""Name the script-to-C++ class registration stubs in the game modules.

Most of the functions that assert out of `stargategame`/`stargatevehicles` are not gameplay code:
they are the generated per-class registration stubs of Perception's `ucpp` script-to-C++ compiler.
Each one calls the engine's class constructor with a wide-string class name, an outer package name
(`System`), an object size and a flag word, then asserts `ReturnClass` on the result:

    iVar2 = FUN_0003af60(0, 0xecc, 4, 0, …, L"AlkeshBomber", …, L"System", 0x4084004, …);
    if (iVar2 == 0)
      FUN_00058a30("ReturnClass", "...\\stargatevehicles\\src\\AlkeshBomber.cpp", 3);

That is evidence enough to name the function (`ucpp_Register_<Class>`) without guessing: the class
name is the build's own string, the source file and line come from the assert, and the object size is
the class's serialised layout size.

Input is the `ExportC.java` output (game-derived, lives under work/, never committed); output is
`symbols/xbe_overrides.csv`, which the symbol pipeline applies on the next Ghidra run.

Usage: ucpp-registrations.py WORK/native [-o symbols/xbe_overrides.csv]
"""
import argparse
import csv
import glob
import os
import re
import sys

ADDRESS = re.compile(r"@ (0x[0-9a-fA-F]+)\s+size=(\d+)")
CLASS_CALL = re.compile(
    r"FUN_0003af60\((.*?)\);", re.S)
WIDE = re.compile(r'L"([^"]+)"')
ASSERT = re.compile(r'FUN_00058a30\("([^"]*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*,\s*(\w+)', re.S)
PATH = re.compile(r"[\\/]+([A-Za-z0-9_]+)[\\/]+[Ss]rc[\\/]+([A-Za-z0-9_]+)\.cpp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("c_dir", help="directory of .c files from tools/ghidra/export-c.sh")
    ap.add_argument("-o", "--out", default="symbols/xbe_overrides.csv")
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.c_dir, "*.c")))
    if not files:
        sys.exit(f"no .c files under {args.c_dir}")

    rows, unidentified = [], []
    for path in files:
        text = open(path, encoding="utf-8", errors="replace").read()
        m = ADDRESS.search(text)
        if not m:
            continue
        address, size = m.group(1), int(m.group(2))
        call = CLASS_CALL.search(text)
        assert_m = ASSERT.search(text)
        if not call or not assert_m:
            unidentified.append((address, "no registration call or no assert"))
            continue
        args_list = call.group(1)
        wide = WIDE.findall(args_list)
        # The constructor takes the class name first and the outer package second; both are
        # literals, and the outer is "System" for every generated class in this build.
        if len(wide) < 2:
            unidentified.append((address, "no class-name literal"))
            continue
        cls, outer = wide[0], wide[1]
        # Object size is the first argument of the constructor (bytes, the class's layout size).
        size_arg = re.search(r"^\s*\d+\s*,\s*(0x[0-9a-fA-F]+|\d+)", args_list)
        obj_size = int(size_arg.group(1), 0) if size_arg else None
        source = PATH.search(assert_m.group(2))
        rows.append({
            "address": address,
            "name": f"ucpp_Register_{cls}",
            "evidence": "ucpp class registration stub: class literal + System outer + ReturnClass assert",
            "class": cls,
            "outer": outer,
            "object_size": obj_size if obj_size is not None else "",
            "assert_line": assert_m.group(3),
            "module": source.group(1) if source else "",
            "source_file": source.group(2) + ".cpp" if source else "",
            "function_size": size,
        })

    rows.sort(key=lambda r: r["address"])
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address", "name", "evidence", "class", "outer",
                                           "object_size", "assert_line", "module", "source_file",
                                           "function_size"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} registration stubs named -> {args.out}")
    for r in rows:
        print(f"  {r['address']}  {r['name']:<34} size={r['function_size']:>3} "
              f"object={r['object_size']} {r['source_file']}")
    if unidentified:
        print(f"  {len(unidentified)} functions left unnamed: "
              + ", ".join(f"{a} ({why})" for a, why in unidentified))
    return 0


if __name__ == "__main__":
    sys.exit(main())
