#!/usr/bin/env python3
"""Name the script-to-C++ class registration stubs in the game modules.

Most of the functions that assert out of `stargategame`/`stargatevehicles` are not gameplay code:
they are the generated per-class registration stubs of Perception's `ucpp` script-to-C++ compiler.
Each one calls the engine's class constructor with a wide-string class name, an outer package name
(`System`), an object size and a flag word, then asserts `ReturnClass` on the result:

    iVar2 = FUN_0003af60(0, 0xecc, 4, 0, …, L"AlkeshBomber", …, L"System", 0x4084004, …);
    if (iVar2 == 0)
      appAssertFailed("ReturnClass", "...\\stargatevehicles\\src\\AlkeshBomber.cpp", 3);

That is evidence enough to name the function (`ucpp_Register_<Class>`) without guessing: the class
name is the build's own string, the source file and line come from the assert, and the object size is
the class's serialised layout size.

Two ways the class name is read, in this order:

1. **from the decompiled call**, when the decompiler rendered both literals;
2. **from the call site's own bytes** (`--xbe`), when it did not. The decompiler prints an argument as
   `&DAT_00xxxxxx` whenever its string guess is unsure -- `0x003107b0` is exactly that, and the
   dword it was given is `L"GoauldDrone"` at `0x53b42a`. The listing block the export carries
   alongside the decompile still has the `PUSH 0x53b42a`, so the literal is one VA read away.

A body that calls no constructor is not a stub: it is gameplay C++ that happens to assert through the
same helper. Those are reported with their assert seam -- expression and `\\<Module>\\Src\\<File>.cpp`
line, both read by VA -- so "no class-name literal" is never the final word on a body again.

Input is the `ExportC.java` output (game-derived, lives under work/, never committed). Output is a CSV
in `symbols/xbe_overrides.csv`'s schema; `--merge` folds it into an existing file and is required
whenever `--out` already holds rows this run does not produce -- the override file is a shared table
(38 of its rows are not registrations) and a plain write would drop them.

Usage:
    ucpp-registrations.py WORK/module-export/stargategame --xbe DUMP/default.xbe \\
        --merge -o symbols/xbe_overrides.csv
    ucpp-registrations.py --check          # synthetic, no dump
"""
import argparse
import csv
import glob
import io
import os
import re
import sys

ADDRESS = re.compile(r"@ (0x[0-9a-fA-F]+)\s+size=(\d+)")
CLASS_CALL = re.compile(r"FUN_0003af60\((.*?)\);", re.S)
WIDE = re.compile(r'L"([^"]+)"')
# `0x00058a30` was named `appAssertFailed` (docs/UNKNOWNS.md U6, 2026-10-24) and the export prints the
# name now. Pinning only the FUN_ spelling is what made this tool report "0 registration stubs" against
# 40 bodies that each contain one: the assert lookup failed and every body fell through as unnamed.
ASSERT = re.compile(
    r'(?:FUN_00058a30|appAssertFailed)\(\s*"([^"]*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*,\s*(\w+)', re.S)
PATH = re.compile(r"[\\/]+([A-Za-z0-9_]+)[\\/]+[Ss]rc[\\/]+([A-Za-z0-9_]+)\.cpp")
LISTING = re.compile(r"^// ([0-9a-fA-F]{8})\s+([A-Z][A-Z0-9]*)\s*(.*)$")
IMM = re.compile(r"^(0x[0-9a-fA-F]+|\d+)$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FIELDS = ["address", "name", "evidence", "class", "outer", "object_size", "assert_line", "module",
          "source_file", "function_size"]

CTOR_VA = 0x0003af60
ASSERT_VA = 0x00058a30


def decode_literal(raw):
    """A literal at a VA: UTF-16LE when every high byte is zero, else ASCII. None if not printable.

    The two encodings both occur in this image -- class/package names are wide, the compiler's own
    `__FILE__` paths and assert expressions are narrow -- and deciding by the bytes avoids a guess.
    """
    # Wide first: a UTF-16 literal is printable low bytes with a zero high byte all the way to its
    # terminator. Testing the *prefix up to the terminator* rather than a fixed window matters --
    # `L"GoauldDrone"` at 0x53b42a is followed by non-zero bytes 8 past its terminator, so a window
    # test rejected it and the class name was reported absent.
    out = []
    for i in range(0, len(raw) - 1, 2):
        if raw[i] == 0 and raw[i + 1] == 0:
            break
        if raw[i + 1] != 0 or not 32 <= raw[i] < 127:
            out = None
            break
        out.append(chr(raw[i]))
    if out and len(out) >= 3:
        return "".join(out)
    out = []
    for b in raw:
        if b == 0:
            break
        if not 32 <= b < 127:
            return None
        out.append(chr(b))
    return "".join(out) if len(out) >= 3 else None


def literals_at(image, va):
    """The literal at `va`, through either the real reader (`read_va`) or the `--check` stub (`read`)."""
    reader = getattr(image, "read", None) or image.read_va
    try:
        raw = reader(va, 200)
    except Exception:
        return None
    return decode_literal(raw)


def listing(text):
    out = []
    for line in text.splitlines():
        m = LISTING.match(line.strip())
        if not m:
            continue
        out.append((int(m.group(1), 16), m.group(2), m.group(3).strip()))
    return out


def window(insns, index):
    """The instructions after the previous CALL, up to (not including) `insns[index]`."""
    j = index - 1
    while j >= 0 and insns[j][1] != "CALL":
        j -= 1
    return insns[j + 1:index]


def immediates(insns):
    out = []
    for addr, mnem, operands in insns:
        if mnem != "PUSH":
            continue
        m = IMM.match(operands)
        if m:
            out.append((addr, int(m.group(1), 0)))
    return out


def ctor_arguments(image, insns, index, decompiled=()):
    """(class, outer, why) read from the call site's own pushed immediates."""
    names = []
    for addr, value in immediates(window(insns, index)):
        text = literals_at(image, value)
        if text:
            names.append((addr, value, text))
    outer = next((t for _, _, t in names if t == "System"), "")
    if not outer and decompiled and decompiled[0] == "System":
        # the outer is pushed through a register in some bodies; the decompiler still rendered it
        outer = decompiled[0]
    cls = next((t for _, _, t in names if IDENT.match(t) and t != "System"
                and not t.endswith(".cpp")), "")
    if not cls:
        return "", outer, ""
    va = next(v for _, v, t in names if t == cls)
    return cls, outer, f"0x{va:08x}"


def assert_seam(image, insns):
    """(expression, source file:line) from the last assert in the body, by VA."""
    first, count = ("", ""), 0
    for index, (_, mnem, operands) in enumerate(insns):
        if mnem != "CALL" or IMM.match(operands) is None:
            continue
        if int(operands, 0) != ASSERT_VA:
            continue
        exprs = []
        line = ""
        for _, value in immediates(window(insns, index)):
            if value < 0x10000:
                line = line or str(value)
                continue
            text = literals_at(image, value)
            if text:
                exprs.append(text)
        expr = next((e for e in reversed(exprs) if not e.lower().endswith((".cpp", ".h"))), "")
        path = next((e for e in exprs if e.lower().endswith((".cpp", ".h"))), "")
        path = PATH.search(path)
        tail = f"{path.group(1)}/{path.group(2)}.cpp" if path else ""
        if line and tail:
            tail = f"{tail}:{line}"
        count += 1
        if count == 1:
            first = (expr, tail)
    if count > 1:
        first = (first[0], first[1] + f" (+{count - 1} more)")
    return first


def encode_row(row):
    """One CSV line for `row`, without its terminator."""
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow([row.get(k, "") for k in FIELDS])
    return buf.getvalue()


def run(c_dir, image, out, merge):
    files = sorted(glob.glob(os.path.join(c_dir, "*.c")))
    if not files:
        sys.exit(f"no .c files under {c_dir}")
    rows, named_by_va, game, unnamed = [], [], [], []
    for path in files:
        text = open(path, encoding="utf-8", errors="replace").read()
        m = ADDRESS.search(text)
        if not m:
            continue
        address, size = m.group(1), int(m.group(2))
        call = CLASS_CALL.search(text)
        assert_m = ASSERT.search(text)
        if not call or not assert_m:
            if image is not None:
                expr, where = assert_seam(image, listing(text))
                if expr or where:
                    game.append((address, expr, where))
                    continue
            unnamed.append((address, "no registration call and no assert seam"))
            continue
        args_list = call.group(1)
        wide = WIDE.findall(args_list)
        by_va = ""
        if len(wide) >= 2:
            cls, outer = wide[0], wide[1]
            evidence = "ucpp class registration stub: class literal + System outer + ReturnClass assert"
        elif image is not None:
            index = next((i for i, (_, mnem, ops) in enumerate(listing(text))
                          if mnem == "CALL" and IMM.match(ops) and int(ops, 0) == CTOR_VA), None)
            if index is None:
                unnamed.append((address, "no constructor call in the listing"))
                continue
            cls, outer, by_va = ctor_arguments(image, listing(text), index, wide)
            if not cls:
                unnamed.append((address, "no class-name literal in the decompile or at the call site"))
                continue
            evidence = ("ucpp class registration stub: class literal read by VA from the call site "
                        f"({by_va}; the decompiler printed a &DAT_ for it) + System outer + "
                        "ReturnClass assert")
            named_by_va.append(address)
        else:
            unnamed.append((address, "no class-name literal in the decompiled text (pass --xbe)"))
            continue
        size_arg = re.search(r"^\s*\d+\s*,\s*(0x[0-9a-fA-F]+|\d+)", args_list)
        obj_size = int(size_arg.group(1), 0) if size_arg else None
        source = PATH.search(assert_m.group(2))
        rows.append({
            "address": address,
            "name": f"ucpp_Register_{cls}",
            "evidence": evidence,
            "class": cls,
            "outer": outer,
            "object_size": obj_size if obj_size is not None else "",
            "assert_line": assert_m.group(3),
            "module": source.group(1) if source else "",
            "source_file": source.group(2) + ".cpp" if source else "",
            "function_size": size,
        })

    rows.sort(key=lambda r: r["address"])
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if merge and os.path.exists(out):
        # Keep the file's own order and its own bytes for every row this run does not produce: the
        # override table is grouped by the tool that wrote each block (and an older block's quoting is
        # not even canonical CSV), so a re-sorted or re-encoded rewrite buries a one-row change in a
        # whole-file diff.
        existing = open(out, newline="", encoding="utf-8").read().splitlines()
        header, body = existing[0], [l for l in existing[1:] if l.strip()]
        fresh = {r["address"]: r for r in rows}
        seen, lines = set(), []
        for line in body:
            address = line.split(",")[0]
            if address in fresh:
                lines.append(encode_row(fresh[address]))
                seen.add(address)
            else:
                lines.append(line)
        lines.extend(encode_row(r) for r in rows if r["address"] not in seen)
        with open(out, "w", newline="", encoding="utf-8") as fh:
            fh.write(header + "\r\n")
            for line in lines:
                fh.write(line + "\r\n")
        print(f"{len(rows)} registrations this run, {len(lines)} rows in {out}")
        rows = sorted(fresh.values(), key=lambda r: r["address"])
    else:
        if os.path.exists(out):
            with open(out, newline="", encoding="utf-8") as fh:
                foreign = [r for r in csv.DictReader(fh)
                           if (r.get("name") or "").split("Register_")[0] != "ucpp_"]
            if foreign:
                sys.exit(f"{out} already holds {len(foreign)} rows this run does not produce; "
                         "re-run with --merge (a plain write would drop them)")
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"{len(rows)} rows -> {out}")
    for r in rows:
        if not r["name"].startswith("ucpp_Register_"):
            continue
        tag = "  (class by VA)" if r["address"] in named_by_va else ""
        print(f"  {r['address']}  {r['name']:<34} size={r['function_size']:>3} "
              f"object={r['object_size']} {r['source_file']}{tag}")
    for address, expr, where in game:
        print(f"  {address}  game body, asserts {expr!r} at {where}")
    if unnamed:
        print(f"  {len(unnamed)} functions left unidentified: "
              + ", ".join(f"{a} ({why})" for a, why in unnamed))
    return 0


class Chunk:
    """A stand-in image for `--check`: literals at fixed VAs, nothing else mapped."""

    def __init__(self, blobs):
        self.blobs = blobs

    def read(self, va, n=200):
        for base, body in self.blobs.items():
            if base <= va < base + len(body):
                return body[va - base:va - base + n]
        raise KeyError(hex(va))


def check():
    wide = lambda s: s.encode("utf-16-le") + b"\x00\x00"
    image = Chunk({
        0x53b42a: wide("GoauldDrone") + b"\x00" * 4,
        0x4d0e90: wide("System") + b"\x00" * 8,
        0x53b44c: b"Mesh\x00\x00",
        0x53b6b0: b"\\\\alienbrainWork\\\\Game\\\\GameXBOX\\\\stargategame\\\\src\\\\SGCameraEffects.cpp\x00",
        0x53b698: b"DOFResourceRefCount > 0\x00",
    })
    assert decode_literal(wide("GoauldDrone")) == "GoauldDrone"
    assert decode_literal(b"Mesh\x00tail") == "Mesh"
    assert decode_literal(b"\x01\x02\x03\x04") is None
    assert decode_literal(b"ab\x00") is None

    body = "\n".join([
        "// 003107b0  PUSH -0x1",
        "// 003107d1  PUSH 0x4c7008",
        "// 003107db  CALL dword ptr [EAX]",
        "// 003107f1  PUSH 0x624d0",
        "// 00310805  PUSH ECX",
        "// 00310806  PUSH 0x53b42a",
        "// 00310800  PUSH 0x4d0e90",
        "// 0031081e  PUSH 0x9e4",
        "// 00310826  PUSH EBX",
        "// 00310829  CALL 0x0003af60",
        "// 0031083c  PUSH 0x5",
        "// 0031083e  PUSH 0x4d23c0",
        "// 00310848  CALL 0x00058a30",
        "",
        "// ---- decompiled ----",
        "",
        "FUN_0003af60(0,0x9e4,4,0,0,0,0,&DAT_0053b42a,uStack_4,L\"System\",0x4084004,&LAB_00025d10);",
        "appAssertFailed(\"ReturnClass\", \"\\\\alienbrainWork\\\\Game\\\\GameXBOX\\\\src\\\\GoauldDrone.cpp\", 5);",
    ])
    insns = listing(body)
    index = next(i for i, (_, m, o) in enumerate(insns) if m == "CALL" and o == "0x0003af60")
    cls, outer, va = ctor_arguments(image, insns, index, ['System'])
    assert (cls, outer) == ("GoauldDrone", "System"), (cls, outer)
    assert va == "0x0053b42a", va
    # the ctor argument must not be taken from the allocator call before it
    assert ctor_arguments(image, insns, index)[0] != "Mesh"

    # an assert seam resolves expression and path for a body that calls no constructor
    seam = "\n".join([
        "// 00316b21  PUSH 0x8c",
        "// 00316b26  PUSH 0x53b6b0",
        "// 00316b2b  PUSH 0x53b698",
        "// 00316b30  CALL 0x00058a30",
        "// 00316b35  ADD ESP,0xc",
    ])
    expr, where = assert_seam(image, listing(seam))
    assert expr == "DOFResourceRefCount > 0", expr
    assert where == "stargategame/SGCameraEffects.cpp:140", where

    # both spellings of the assert helper are accepted
    old = ASSERT.search('FUN_00058a30("ReturnClass", "x.cpp", 3)')
    new = ASSERT.search('appAssertFailed("ReturnClass", "x.cpp", 3)')
    assert old and new and old.group(1) == new.group(1) == "ReturnClass"
    print("ok")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("c_dir", nargs="?", help="directory of .c files from tools/ghidra/export-c.sh")
    ap.add_argument("-o", "--out", default="symbols/xbe_overrides.csv")
    ap.add_argument("--xbe", help="default.xbe, to read a literal by VA when the decompiler did not")
    ap.add_argument("--merge", action="store_true",
                    help="fold this run's rows into an existing --out instead of replacing it")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.c_dir:
        ap.error("c_dir is required unless --check")
    image = None
    if args.xbe:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import xbe
        image = xbe.Xbe(args.xbe)
    return run(args.c_dir, image, args.out, args.merge)


if __name__ == "__main__":
    sys.exit(main())
