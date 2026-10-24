#!/usr/bin/env python3
"""Measure the property type space from decompiled UnProp.cpp / UnName.cpp exports.

Input is the output of `tools/ghidra/ghidra_scripts/ExportAt.java` over the addresses
`docs/NATIVE_PROPERTIES.md` names: the 15 per-class static-class constructors in UnProp.cpp and
the name-table initialiser in UnName.cpp. Decompiled bodies are game-derived and stay under
`$WORK` (never committed); what this script writes is the derived measurement: addresses,
class names, object sizes, flags and the reserved name indices.

    python3 tools/property-map.py --exports $WORK/property --exports $WORK/property2 -o inventory

Regenerating needs Ghidra, the dump and the address list; the CSVs it produces are what the
repository carries.
"""
import argparse
import glob
import os
import re

CONSTRUCTOR = "FUN_0003af60"          # the class-object constructor the stubs call
NAME_REGISTER = "thunk_FUN_003e67ed"  # wide-string length helper, used as the name argument


def balanced(text, open_index):
    """Return the argument string of the call whose '(' is at open_index."""
    depth = 0
    for k in range(open_index, len(text)):
        if text[k] == "(":
            depth += 1
        elif text[k] == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:k]
    return ""


def decompiled(path):
    s = open(path, errors="replace").read()
    i = s.find("---- decompiled ----")
    return (s[:i], s[i:]) if i >= 0 else (s, "")


def header(path):
    head, _ = decompiled(path)
    m = re.search(r"// (\S+) @ 0x([0-9a-f]+)  size=(\d+)", head)
    if not m:
        return None
    return m.group(1), int(m.group(2), 16), int(m.group(3))


def parse_classes(dirs):
    rows = {}
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.c"))):
            h = header(path)
            if not h:
                continue
            name, addr, _size = h
            _, body = decompiled(path)
            if CONSTRUCTOR not in body:
                continue
            i = body.find(CONSTRUCTOR + "(")
            args = [a.strip() for a in re.sub(r"\s+", " ", balanced(body, body.index("(", i))).split(",")]
            names = [a for a in args if a.startswith('L"')]
            if len(names) < 2:
                continue
            class_name = names[0].strip('L"')
            # FUN_0003af60(outer, objectSize, classFlags, ?, ?, ?, ?, L"<class>", ?, L"<outer>",
            #              <classFlags2>, &<vtable>, &<parent-ish>)
            try:
                object_size = int(args[1], 16)
                class_flags = int(args[2], 16)
                second_flags = int(args[10], 16)
            except (IndexError, ValueError):
                continue
            vtable = args[11].lstrip("&")
            m = re.search(r'FUN_00058a30\("ReturnClass",\s*"[^"]*UnProp\.cpp",\s*(0x[0-9a-f]+)\)',
                          body.replace("\n", " "))
            line = int(m.group(1), 16) if m else 0
            rows[addr] = (addr, class_name, object_size, class_flags, second_flags, vtable, line)
    return [rows[a] for a in sorted(rows)]


def parse_name_table(dirs):
    """Reserved name indices, from the initialiser that registers them at fixed values."""
    out = {}
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.c"))):
            _, body = decompiled(path)
            if NAME_REGISTER not in body or "NameEntry" not in body:
                continue
            # Each registration: len = NameRegister(L"<name>",L"NameEntry"); p = alloc(len*2+0xe);
            # p[0] = <index>; p[1] = <flags>; p[2] = 0; copy(p+3, L"<name>"); register(p)
            pat = re.compile(
                r'thunk_FUN_003e67ed\(L"((?:[^"\\]|\\.)*)",L"NameEntry"\);.{0,400}?'
                r'\*puVar2 = (0x[0-9a-f]+|\d+);.{0,200}?puVar2\[1\] = (0x[0-9a-f]+);.{0,200}?'
                r'thunk_FUN_003e68ad\(puVar2 \+ 3,L"((?:[^"\\]|\\.)*)"\);', re.S)
            for m in pat.finditer(body):
                index = int(m.group(2), 16) if m.group(2).startswith("0x") else int(m.group(2))
                out[index] = (index, m.group(4), int(m.group(3), 16))
    return [out[i] for i in sorted(out)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exports", action="append", required=True,
                    help="directory of ExportAt .c files (repeatable)")
    ap.add_argument("-o", "--out", default="inventory")
    args = ap.parse_args()

    classes = parse_classes(args.exports)
    names = parse_name_table(args.exports)
    if not classes:
        raise SystemExit("no UnProp static-class constructors found under %s" % args.exports)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "property_classes.csv"), "w") as f:
        f.write("address,class,object_size,class_flags,class_flags_2,vtable,assert_line,evidence\n")
        for addr, name, size, flags, flags2, vtable, line in classes:
            f.write("0x%08x,%s,%d,0x%x,0x%x,%s,%d,UnProp.cpp:line 0x%x class literal\n"
                    % (addr, name, size, flags, flags2, vtable, line, line))
    # The reserved name indices: the property type names are the contiguous block 1..16, and the
    # initialiser assigns them in declaration order, so the block is the type -> index map.
    with open(os.path.join(args.out, "property_types.csv"), "w") as f:
        f.write("name_index,name,name_flags,evidence\n")
        for index, name, flags in names:
            if 0 <= index <= 0x10:
                f.write("%d,%s,0x%x,0x0005c080 reserved name index\n" % (index, name, flags))
    print("property_classes.csv: %d classes" % len(classes))
    print("property_types.csv: %d reserved name indices (0..0x10), %d names registered in total"
          % (len([n for n in names if n[0] <= 0x10]), len(names)))


if __name__ == "__main__":
    main()
