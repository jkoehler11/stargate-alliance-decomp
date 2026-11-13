#!/usr/bin/env python3
"""Every member displacement the image names itself, from the assert that checks it.

The `+0x4b0` census ended on a fact worth generalising: `Class`'s member at `+0x4b0` is not named by a
symbol map, a header or a signature database -- it is named by the **expression string its own assert
prints**. `0x397b0` loads the member, tests it for null and calls the assert printer with
`UnClass.cpp` and the string `ClassWithin`, so the displacement and the name sit eight instructions
apart in the same body.

That shape is mechanical, so it is a census: `mov r32,[base+disp32]` -> `test r32,r32` -> `jcc` ->
`push line; push file; push expression; call <assert printer>`. A body that prints an expression which
is a bare identifier names the member it just checked; an expression like `Index<=ArrayNum` names a
local and is dropped by the shape test, not by a blocklist.

This is not a name map for the class layout -- an assert names the member at a displacement in *that*
function's class, and a displacement is not a field (see `docs/NATIVE_VM.md`). What it is, is a set of
(image, displacement, name) triples the image states about itself, each with a site to read.

    python3 tools/member-assert-names.py --xbe DUMP/default.xbe -o inventory
    python3 tools/member-assert-names.py --xbe DUMP/default.xbe --report
    python3 tools/member-assert-names.py --check     # synthetic body, no dump, writes nothing

Reads `default.xbe` plus the committed inventories; writes one CSV of addresses, displacements and the
expression strings the image prints -- no value decoded, no payload byte published.
"""
import argparse
import csv
import importlib.util
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = ["disp", "expr", "site_va", "function", "module", "function_class", "function_route",
          "tested_reg"]
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,63}$")
PUSH_IMM = re.compile(r"^push 0x([0-9a-f]{8})$")
CALL_REL = re.compile(r"^call rel 0x([0-9a-f]{8})$")
MOV_MEM = re.compile(r"^mov ([a-z0-9]+),\[([a-z0-9]+)\+0x([0-9a-f]+)\]$")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


recv = load(os.path.join(ROOT, "tools", "vcall-receiver.py"), "sg1_vcall_receiver")


def functions(path):
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                rows.append((int(row["address"], 16), row.get("name") or "", int(row["size"] or 0),
                             row.get("module") or ""))
            except (KeyError, ValueError):
                continue
    return sorted(rows)


def class_of_targets(xbe, tables_path, names_path):
    """function_va -> (class, slot) for every slot of every known class table."""
    names = {}
    if names_path and os.path.isfile(names_path):
        with open(names_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    names[int(row["table_va"], 16)] = row.get("class") or ""
                except (KeyError, ValueError):
                    continue
    out = {}
    if not tables_path or not os.path.isfile(tables_path):
        return out
    with open(tables_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                tva = int(row["table_va"], 16)
                slots = int(row["slots"])
            except (KeyError, ValueError):
                continue
            for slot in range(slots):
                try:
                    target = struct.unpack_from("<I", xbe.read_va(tva + 4 * slot, 4))[0]
                except (KeyError, IndexError, struct.error):
                    break
                out.setdefault(target, (names.get(tva, ""), slot))
    return out


def call_target(sva, ins):
    m = CALL_REL.match(ins.text)
    if not m:
        return None
    return (sva + ins.length + int(m.group(1), 16)) & 0xFFFFFFFF


def push_imm(sva, ins):
    m = PUSH_IMM.match(ins.text)
    if m:
        return int(m.group(1), 16)
    if ins.text.startswith("push 0x") and len(ins.text) <= 12:
        try:
            return int(ins.text.split("0x", 1)[1], 16)
        except ValueError:
            return None
    return None


def scan_body(blob, base_va, funcs, resolve, assert_va=0x58A30, back=12):
    """Yield one row per assert that checks a this-relative displacement.

    A site is an instruction boundary of a sweep anchored at the function's own start, so it cannot
    be a byte inside an immediate; a body the decoder desynced contributes nothing.
    """
    rows = []
    for va, name, size, module in funcs:
        off = va - base_va
        if off < 0 or off >= len(blob) or not size:
            continue
        cur, end = off, min(off + size, len(blob))
        insns = []
        while cur < end:
            ins = recv.decode(blob, cur)
            if not ins:
                insns = None
                break
            insns.append((base_va + cur, ins))
            cur += ins.length
        if not insns:
            continue
        for i, (sva, ins) in enumerate(insns):
            if ins.kind != "call" or call_target(sva, ins) != assert_va:
                continue
            j = i - 1
            pushes = []
            while j >= 0 and i - j <= 5 and insns[j][1].text.startswith("push "):
                pushes.append(insns[j])
                j -= 1
            if len(pushes) < 2:
                continue
            expr = None
            for pva, pins in pushes:                 # nearest push is the first argument
                imm = push_imm(pva, pins)
                if imm is None:
                    continue
                text = resolve(imm)
                if text and IDENT.match(text):
                    expr = text
                    break
            if not expr:
                continue
            reg = None
            disp = None
            for k in range(j, max(-1, j - back), -1):
                pva, pins = insns[k]
                txt = pins.text
                if reg is None and txt.startswith("test ") and "," in txt:
                    a, b = [t.strip() for t in txt[5:].split(",", 1)]
                    if a == b and a in recv.REGS:
                        reg = a
                    continue
                if reg is None:
                    continue
                m = MOV_MEM.match(txt)
                if m and m.group(1) == reg and m.group(2) not in ("esp", "ebp"):
                    disp = int(m.group(3), 16)
                    break
            if disp is None or disp < 0x40:
                continue
            rows.append({"disp": "0x%x" % disp, "expr": expr, "site_va": "0x%08x" % sva,
                         "function": name, "module": module, "function_class": "",
                         "function_route": "plain", "tested_reg": reg})
    return rows


def make_resolver(xbe):
    def resolve(va):
        try:
            return xbe._cstr_at_va(va, 64)
        except Exception:                            # unreadable VA is not a string, not a failure
            return None
    return resolve


def selftest():
    """A synthetic body: the assert shape must be recovered, and a local-check shape must be dropped."""
    base = 0x100000
    expr_va, file_va = 0x402000, 0x401000
    body = bytearray()
    body += b"\x8b\x86\xb0\x04\x00\x00"               # mov eax,[esi+0x4b0]
    body += b"\x85\xc0"                              # test eax,eax
    body += b"\x75\x0f"                              # jne +0xf
    body += b"\x68" + struct.pack("<I", 0x3E8)       # push line
    body += b"\x68" + struct.pack("<I", file_va)     # push file
    body += b"\x68" + struct.pack("<I", expr_va)     # push expression
    call_va = base + len(body)
    body += b"\xe8" + struct.pack("<I", (0x58A30 - (call_va + 5)) & 0xFFFFFFFF)
    # second site: the expression is an expression, not an identifier, so it must not be reported
    body += b"\x8b\x8e\xc4\x04\x00\x00"               # mov ecx,[esi+0x4c4]
    body += b"\x85\xc9"                              # test ecx,ecx
    body += b"\x75\x0f"                              # jne
    body += b"\x68" + struct.pack("<I", 0x3E9)
    body += b"\x68" + struct.pack("<I", file_va)
    body += b"\x68" + struct.pack("<I", 0x403000)    # push a non-identifier string
    call_va = base + len(body)
    body += b"\xe8" + struct.pack("<I", (0x58A30 - (call_va + 5)) & 0xFFFFFFFF)

    strings = {expr_va: "ClassWithin", file_va: "UnClass.cpp", 0x403000: "Index<=ArrayNum"}
    funcs = [(base, "SYNTH", len(body), "")]
    rows = scan_body(bytes(body), base, funcs, lambda va: strings.get(va))
    ok = (len(rows) == 1 and rows[0]["disp"] == "0x4b0" and rows[0]["expr"] == "ClassWithin"
          and rows[0]["tested_reg"] == "eax")
    if not ok:
        print("FAIL member-assert-names: synthetic body recovered %r" % (rows,))
        return 1
    print("ok   member-assert-names: synthetic body -> disp 0x%x named %s, a local check dropped"
          % (int(rows[0]["disp"], 16), rows[0]["expr"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--xbe", help="DUMP/default.xbe")
    ap.add_argument("--dump", help="the game directory holding default.xbe")
    ap.add_argument("--functions", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("--tables", default=os.path.join(ROOT, "inventory", "xbe_class_tables.csv"))
    ap.add_argument("--table-names", default=os.path.join(ROOT, "inventory", "xbe_class_table_names.csv"))
    ap.add_argument("--members", default=os.path.join(ROOT, "inventory", "class_member_map.csv"))
    ap.add_argument("--assert-va", default="0x58a30", help="the assert printer's address")
    ap.add_argument("-o", "--out", help="directory for the census CSV")
    ap.add_argument("--artifact", default="member_assert_names.csv")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()

    if args.check:
        return selftest()
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe  # noqa: E402

    dump = args.dump or os.environ.get("SG1_DUMP", "")
    path = args.xbe or (os.path.join(dump, "default.xbe") if dump else "")
    if not path or not os.path.isfile(path):
        raise SystemExit("no default.xbe: pass --xbe or set SG1_DUMP")
    xbe = Xbe(path)
    sec = None
    for s in xbe.sections:
        if s.executable:
            sec = s
            break
    if sec is None:
        raise SystemExit("no executable section")
    with open(path, "rb") as fh:
        blob = fh.read()[sec.foff:sec.foff + sec.fsize]
    funcs = functions(args.functions)
    if not funcs:
        raise SystemExit("no function map: %s" % args.functions)
    rows = scan_body(blob, sec.va, funcs, make_resolver(xbe), int(args.assert_va, 16))
    classes = class_of_targets(xbe, args.tables, args.table_names)
    for row in rows:
        va = int(row["site_va"], 16)
        # the containing function is the row's own, so look its class up by the function address
        for fva, _n, _s, _m in funcs:
            if fva <= va < fva + (_s or 1) and fva in classes:
                row["function_class"] = "%s@%d" % classes[fva]
                row["function_route"] = "table"
                break
    # the report is grouped, so a displacement named twice in one function appears once per name
    seen = set()
    uniq = []
    for row in rows:
        key = (row["disp"], row["expr"], row["site_va"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(row)
    uniq.sort(key=lambda r: (int(r["disp"], 16), r["expr"]))
    if args.out:
        dest = os.path.join(args.out, args.artifact)
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=HEADER)
            wr.writeheader()
            for row in uniq:
                wr.writerow(row)
        print("wrote %s (%d rows)" % (dest, len(uniq)))
    if args.report:
        known = set()
        if os.path.isfile(args.members):
            with open(args.members, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    known.add((row.get("disp") or "").lower())
        pairs = {}
        for row in uniq:
            pairs.setdefault((row["disp"], row["expr"]), []).append(row["site_va"])
        print("%d sites, %d (displacement, name) pairs, %d distinct displacements"
              % (len(uniq), len(pairs), len({r["disp"] for r in uniq})))
        inmap = sum(1 for d, _e in pairs if d in known)
        print("displacements the member map also places: %d of %d" % (inmap, len({d for d, _e in pairs})))
        for (disp, expr), sites in sorted(pairs.items(), key=lambda kv: (int(kv[0][0], 16), kv[0][1])):
            print("  %-8s %-28s %s" % (disp, expr, " ".join(sites[:3])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
