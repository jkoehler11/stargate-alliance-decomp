#!/usr/bin/env python3
"""What touches one member displacement, and the stride each site indexes it at.

The class-member notes ended on a `TArray` whose element the destructor measures as **eight** bytes:
`Class`'s destructor at `0x3dbd0` destroys `+0x4b8` through `0x3d7a0`, and that body's only
per-instantiation byte is the `PUSH imm32` it hands the range destroy -- `8`. The named next
measurement was "what indexes the `+0x4b8` array at a stride of eight", with the note that the writer
"will not be reached from `UClass::Serialize`".

A displacement is only half an address: several classes can own a member at the same offset, and this
image proves it -- `0x4b8` is a member of `Class`, of `GameReplicationInfo` and of `SceneManager`
(`inventory/class_member_map.csv`), and a site in a fourth class would look identical. So this tool
census-es every `disp32` in `.text` equal to one displacement, then reads each site two ways:

    form     load / store / lea / global -- what the instruction does with the member
    stride   the SIB scale of the first indexed use of the value the site loaded

and attributes the containing function: a slot target of a class table is named by its class, a plain
member function is not (a class table holds virtuals only), and `module` comes from the symbol map.

Why a sweep from the function's own start matters: `b8 04 00 00` is also the little-endian
`0x000004b8` and a slice of any longer immediate, so a byte-pattern search over-counts. Here every
candidate is an instruction the decoder produced at an exact boundary of a linear sweep anchored at a
known function start, and each row carries a `chain` count (how many consecutive instruction ends
confirm that boundary) so a site inside a body the decoder desynced can be seen rather than trusted.

    python3 tools/member-access-census.py --xbe DUMP/default.xbe --disp 0x4b8 -o inventory
    python3 tools/member-access-census.py --report --window-show
    python3 tools/member-access-census.py --check          # synthetic bytes, no dump, writes nothing

Reads `default.xbe` plus the committed inventories; writes one CSV and prints addresses and counts
only -- no value decoded, no name published.
"""
import argparse
import csv
import importlib.util
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


recv = load(os.path.join(ROOT, "tools", "vcall-receiver.py"), "sg1_vcall_receiver")

HEADER = ["disp", "site_va", "function", "module", "function_class", "function_route",
          "form", "base", "index", "insn", "chain", "loaded_reg", "stride", "stride_va",
          "stride_insn", "window", "note", "owner_classes", "member_elem_size"]


def sx8(v):
    return v - 0x100 if v >= 0x80 else v


def sx32(v):
    return v - 0x100000000 if v >= 0x80000000 else v


def scale_at(code, start, ins):
    """The SIB scale of this instruction's memory operand, validated against the decoded operands.

    The decoder reports base/index/disp but not the scale, and the scale is the whole question here,
    so the SIB is re-parsed and accepted only when base, index *and* displacement agree with what the
    instruction decoder already said -- a candidate that disagrees is a byte that merely looks like a
    ModRM and is rejected instead of guessed.
    """
    end = start + ins.length
    for p in range(start, end - 1):
        b = code[p]
        mod, rm = b >> 6, b & 7
        if mod == 3 or rm != 4:
            continue
        sib = code[p + 1]
        idx = (sib >> 3) & 7
        index = None if idx == 4 else recv.REGS[idx]
        if mod == 0 and (sib & 7) == 5:
            continue
        base = recv.REGS[sib & 7]
        q = p + 2
        if mod == 0:
            disp = None
        elif mod == 1:
            if q >= end:
                continue
            disp = sx8(code[q])
        else:
            if q + 4 > end:
                continue
            disp = sx32(struct.unpack_from("<I", code, q)[0])
        if (base, index, disp) == (ins.base, ins.index, ins.disp):
            return 1 << (sib >> 6)
    return None


STORE_OPS = (0x88, 0x89, 0xC6, 0xC7)


def store_operand(code, start, ins):
    """The memory operand of a store form the shared decoder reports without one.

    `vcall-receiver`'s decoder fills base/index/disp for the *source* operand of a load and a `lea`,
    and leaves a store's destination opaque (`kind="store"`, no base), which is exactly the half this
    census needs: a constructor's zero-fill is `mov dword [esi+0x4b8],edi` and would otherwise be
    invisible. `88`/`89`/`C6`/`C7` are the byte and dword stores MSVC emits for a member, so those are
    decoded here; nothing else is guessed at.
    """
    i = start
    while i < start + ins.length and code[i] in recv.PREFIXES:
        i += 1
    if i >= start + ins.length or code[i] not in STORE_OPS:
        return None
    r = recv.operand(code, i + 1)
    if not r:
        return None
    _n, mod, _reg, _rm, base, index, disp = r
    if mod == 3:
        return None
    return base, index, disp


def scaled_text(ins, scale):
    """The instruction's own text with the SIB scale written in (the decoder reports no scale)."""
    if ins.index is None or not scale:
        return ins.text
    return ins.text.replace(recv.mtext(ins.base, ins.index, ins.disp),
                            recv.mtext(ins.base, "%s*%d" % (ins.index, scale), ins.disp), 1)


def field_sites(blob, base_va, funcs, disp, window=12):
    """Every instruction in `blob` whose memory operand displacement is `disp`.

    `funcs` is [(va, name, size, module)] and each body is swept from its own first byte, so an
    instruction is a boundary by construction; `chain` and the sweep's own failure are recorded so a
    body that desynced is visible.
    """
    sites, stack, global_, undecodable = [], [], [], 0
    for va, name, size, module in funcs:
        if not size:
            continue
        off = va - base_va
        if off < 0 or off >= len(blob):
            continue
        cur, end = off, min(off + size, len(blob))
        insns = []
        while cur < end:
            ins = recv.decode(blob, cur)
            if not ins:
                undecodable += 1
                insns = None
                break
            insns.append((base_va + cur, ins))
            cur += ins.length
        if not insns:
            continue
        for i, (sva, ins) in enumerate(insns):
            st = store_operand(blob, sva - base_va, ins) if ins.kind == "store" else None
            if ins.disp != disp and not (st and st[2] == disp):
                continue
            base = st[0] if st else ins.base
            if base in ("esp", "ebp"):
                stack.append((va, name, sva, ins))
                continue
            if base is None and (st[1] if st else ins.index) is None:
                global_.append((va, name, sva, ins))
                continue
            row = {"site_va": "0x%08x" % sva, "function": name, "module": module,
                   "insn": ins.text, "base": (st[0] if st else ins.base) or "",
                   "index": (st[1] if st else ins.index) or "",
                   "chain": str(recv.chain_links(blob, sva - base_va)),
                   "form": "store" if st else ins.kind, "loaded_reg": "", "stride": "", "stride_va": "",
                   "stride_insn": "", "window": str(len(insns) - i - 1), "note": ""}
            if ins.kind == "array":
                # the member is itself indexed in this instruction: [obj + disp + index*scale]
                sc = scale_at(blob, sva - base_va, ins)
                row["stride"] = str(sc or "")
                row["stride_va"] = row["site_va"]
                row["stride_insn"] = scaled_text(ins, sc)
                row["note"] = "indexed in place"
            elif st:
                row["note"] = "store"
            elif ins.dests:
                reg = ins.dests[0]
                row["loaded_reg"] = reg
                found = False
                for uva, uins in insns[i + 1:i + 1 + window]:
                    if uins.index is None or reg not in (uins.base, uins.index):
                        continue
                    sc = scale_at(blob, uva - base_va, uins)
                    row["stride"] = str(sc or "")
                    row["stride_va"] = "0x%08x" % uva
                    row["stride_insn"] = scaled_text(uins, sc)
                    found = True
                    break
                if not found:
                    row["note"] = "no indexed use in %d" % window
            else:
                row["note"] = "store or no destination"
            sites.append((va, row))
    return sites, stack, global_, undecodable


def owner_classes(path, disp):
    """Which classes place a member at `disp`, and the element size its destructor measures."""
    owners, elem = {}, {}
    if not os.path.isfile(path):
        return owners, elem
    key = "0x%03x" % disp if disp < 0x1000 else "0x%x" % disp
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("disp") or "").lower() != key.lower():
                continue
            cls = (row.get("class") or "").strip()
            if not cls:
                continue
            owners.setdefault(cls, set()).add(row.get("form") or "")
            if row.get("target_va"):
                elem.setdefault(cls, set()).add(row["target_va"])
    return owners, elem


def dtor_elem_sizes(path):
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            va = (row.get("target_va") or "").strip()
            if va and (row.get("elem_size") or "").strip():
                out[va.lower()] = row["elem_size"].strip()
    return out


def class_of_targets(xbe, tables_path, names_path):
    """function_va -> (class, slot) for every slot of every known class table."""
    names = {}
    if os.path.isfile(names_path):
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


def functions(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                rows.append((int(row["address"], 16), row.get("name") or "", int(row["size"] or 0),
                             row.get("module") or ""))
            except (KeyError, ValueError):
                continue
    return sorted(rows)


def text_section(xbe):
    for sec in xbe.sections:
        if sec.executable:
            return sec
    raise SystemExit("no executable section")


def run(args):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe  # noqa: E402

    dump = args.dump or os.environ.get("SG1_DUMP", "")
    xbe_path = args.xbe or (os.path.join(dump, "default.xbe") if dump else "")
    if not xbe_path or not os.path.isfile(xbe_path):
        raise SystemExit("no default.xbe: pass --xbe or set SG1_DUMP")
    xbe = Xbe(xbe_path)
    sec = text_section(xbe)
    with open(xbe_path, "rb") as fh:
        raw = fh.read()
    blob = raw[sec.foff:sec.foff + sec.fsize]

    funcs = functions(args.functions)
    sites, stack, glob, undecodable = field_sites(blob, sec.va, funcs, args.disp, args.window)
    owners, _ = owner_classes(args.members, args.disp)
    elem = dtor_elem_sizes(args.dtors)
    classes = class_of_targets(xbe, args.tables, args.table_names)
    # the member map's lea-call target IS the destructor, so its element size is the member's width
    owner_dtor = {}
    if os.path.isfile(args.members):
        key = "0x%03x" % args.disp
        with open(args.members, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if (row.get("disp") or "").lower() == key and row.get("target_va"):
                    owner_dtor[(row.get("class") or "").strip()] = row["target_va"].lower()

    rows = []
    for fn_va, row in sites:
        cls, slot = classes.get(fn_va, ("", -1))
        row["function_class"] = ("%s@%d" % (cls, slot)) if cls else ""
        row["function_route"] = "table" if cls else "plain"
        names = sorted(c for c, targets in owner_dtor.items() if targets)
        row["owner_classes"] = " ".join(names)
        sizes = [elem.get(owner_dtor[c].lower(), "") for c in names]
        row["member_elem_size"] = " ".join("%s=%s" % (c, s) for c, s in zip(names, sizes) if s)
        row["disp"] = "0x%x" % args.disp
        rows.append(row)

    rows.sort(key=lambda r: r["site_va"])
    if args.out:
        path = os.path.join(args.out, "member_access_census.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=HEADER)
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in HEADER})
        print("wrote %s (%d rows)" % (path, len(rows)))

    if args.report or not args.out:
        report(args, rows, stack, glob, undecodable, len(funcs), blob, sec.va, sites)
    return 0


def report(args, rows, stack, glob, undecodable, nfuncs, blob=None, base_va=0, sites=None):
    print("swept %d functions; %d bodies the decoder could not finish" % (nfuncs, undecodable))
    print("disp 0x%x: %d field sites, %d stack, %d absolute" % (args.disp, len(rows), len(stack),
                                                                len(glob)))
    forms = {}
    for row in rows:
        forms[row["form"]] = forms.get(row["form"], 0) + 1
    print("forms: " + ", ".join("%s x%d" % (k, v) for k, v in sorted(forms.items())))
    strides = {}
    for row in rows:
        strides[row["stride"] or "none"] = strides.get(row["stride"] or "none", 0) + 1
    print("stride of the loaded value: " + ", ".join(
        "%s x%d" % (k, v) for k, v in sorted(strides.items(), key=lambda kv: -kv[1])))
    print("")
    for row in rows:
        print("%s  %-22s %-6s %-28s stride=%-4s %s" % (
            row["site_va"], row["function"][:22], row["form"], row["insn"][:28],
            row["stride"] or "-", row["function_class"] or row["function_route"]))
    if rows:
        print("")
        print("owners of 0x%x (from the destructor map): %s" % (
            args.disp, rows[0].get("owner_classes") or "(none)"))
        if rows[0].get("member_elem_size"):
            print("element width that destructor measures: %s" % rows[0]["member_elem_size"])
    if args.window_show and blob is not None and sites:
        by_fn = {}
        for fn_va, row in sites:
            by_fn.setdefault(fn_va, []).append(row)
        for fn_va in sorted(by_fn):
            foff = fn_va - base_va
            print("\n=== 0x%08x %s" % (fn_va, by_fn[fn_va][0]["function"]))
            cur, n = foff, 0
            while n < args.window + 12:
                ins = recv.decode(blob, cur)
                if not ins:
                    print("  0x%08x  <undecodable>" % (base_va + cur))
                    break
                mark = any(r["site_va"] == "0x%08x" % (base_va + cur) for r in by_fn[fn_va])
                print(" %s 0x%08x  %s" % (">>" if mark else "  ", base_va + cur, ins.text))
                cur += ins.length
                n += 1


def selftest():
    """Synthetic bytes: a real field site with a stride, a stack form, an absolute form, a decoy."""
    blob = bytes([
        0x8b, 0x8f, 0xb8, 0x04, 0x00, 0x00,        # mov ecx,[edi+0x4b8]        a field site
        0x8b, 0x04, 0xb1,                          # mov eax,[ecx+esi*4]        stride 4
        0x8b, 0x8c, 0x24, 0xb8, 0x04, 0x00, 0x00,  # mov ecx,[esp+0x4b8]        stack
        0x8b, 0x12,                                # mov edx,[edx]              unrelated
        0x8b, 0x1d, 0xb8, 0x04, 0x00, 0x00,        # mov ebx,[0x000004b8]       absolute
        0xb8, 0xb8, 0x04, 0x00, 0x00,              # mov eax,0x000004b8         decoy: an immediate
        0x8b, 0x8f, 0xb8, 0x04, 0x00, 0x00,        # mov ecx,[edi+0x4b8]
        0x8b, 0x04, 0x79,                          # mov eax,[ecx+edi*2]        stride 2
        0x89, 0xbe, 0xb8, 0x04, 0x00, 0x00,        # mov [esi+0x4b8],edi        a store site
        0xc3,
    ])
    funcs = [(0x1000, "synthetic", len(blob), "Test")]
    sites, stack, glob, undecodable = field_sites(blob, 0x1000, funcs, 0x4b8, window=4)
    assert undecodable == 0, undecodable
    assert len(sites) == 3, len(sites)
    assert len(stack) == 1, len(stack)
    assert len(glob) == 1, len(glob)
    first = sites[0][1]
    assert first["insn"] == "mov ecx,[edi+0x4b8]", first["insn"]
    assert first["stride"] == "4", first["stride"]
    assert first["stride_insn"] == "mov eax,[ecx+esi*4]", first["stride_insn"]
    second = sites[1][1]
    assert second["stride"] == "2", second["stride"]
    third = sites[2][1]
    assert third["form"] == "store" and third["note"] == "store", third
    # the decoy is an immediate, so it never reaches a row; the stack form is counted apart
    assert all("0x000004b8" not in r["insn"] for _, r in sites), "decoy counted"
    # a body the decoder cannot finish stops the sweep instead of inventing a site
    sites2, _, _, bad = field_sites(bytes([0x8b, 0x0f, 0x66]), 0x2000,
                                    [(0x2000, "bad", 3, "Test")], 0x4b8)
    assert sites2 == [] and bad == 1, (sites2, bad)
    print("self-check ok: a field site with stride 4 and one with stride 2, the stack and absolute "
          "forms counted apart, an immediate decoy rejected, a bad body stopping the sweep")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", help="DUMP/default.xbe")
    ap.add_argument("--dump", help="the game directory holding default.xbe")
    ap.add_argument("--disp", default="0x4b8", help="the member displacement to census (default 0x4b8)")
    ap.add_argument("--functions", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("--members", default=os.path.join(ROOT, "inventory", "class_member_map.csv"))
    ap.add_argument("--dtors", default=os.path.join(ROOT, "inventory", "class_member_dtors.csv"))
    ap.add_argument("--tables", default=os.path.join(ROOT, "inventory", "xbe_class_tables.csv"))
    ap.add_argument("--table-names", default=os.path.join(ROOT, "inventory",
                                                          "xbe_class_table_names.csv"))
    ap.add_argument("-o", "--out", help="directory for member_access_census.csv")
    ap.add_argument("--window", type=int, default=12, help="instructions to look ahead for a stride")
    ap.add_argument("--report", action="store_true", help="print the census")
    ap.add_argument("--window-show", action="store_true", help="print each site's body window")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return selftest()
    args.disp = int(args.disp, 0)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
