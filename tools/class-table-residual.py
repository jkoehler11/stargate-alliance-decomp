#!/usr/bin/env python3
"""What U8's two class-table residuals actually hold.

`docs/NATIVE_CLASS_TREE.md` names 466 of the 492 per-class tables from the build's own class
registrations, and `inventory/registration_ctor_arguments.csv` explains 24 of the 27 unnamed sites by
showing that they push one shared default constructor -- so the stored dword describes them
identically. PLAN 4.3 wrote the next step as *the site's fourth/fifth pushes, unread*, and U8's other
residual as *which virtuals slots 19 and 20 are*. This tool measures both, and both answers are
narrower than the questions supposed:

`sites`  Every registration call's argument list, decoded from the `68 imm32` pushes before the call
         and classified by what each immediate *is*: a UTF-16 literal the image carries, a code
         address that is a known function start, a class table, or an unmapped immediate (flags and
         sizes live there). The answer to the fourth/fifth-push route is in the census: a site pushes
         the class's **name and outer strings**, its flags word, two constructors and its object size
         -- the class table is never an argument, because the constructor installs it.

`slots`  Slots 19 and 20 across all 492 tables. `docs/NATIVE_VM.md` records "a single `ret` in 327
         tables and real bodies in the other 165"; the measurement is sharper than that -- there are
         exactly **two** distinct (19, 20) pairs in the whole census, and the pair that is not the
         `ret` is one implementation of each, carried by the `Actor` subtree with `Actor` itself
         holding it. So these two virtuals are not per-class overrides at all: they are installed
         once, at `AActor`, and inherited by everything below it.

Usage:
    class-table-residual.py sites  --xbe DUMP/default.xbe -o inventory/registration_arguments.csv
    class-table-residual.py slots  --xbe DUMP/default.xbe -o inventory/class_table_slot_pairs.csv
    class-table-residual.py --check            # synthetic, reads no dump and writes nothing

The addresses, sizes and counts are locators, not content: this artifact is the same shape as
`inventory/xbe_class_table_names.csv` and carries no game data.
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xbe import Xbe  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(REPO, "inventory")

# The window ctor-argument.py uses, kept so the two tools describe the same bytes. A `68 imm32` inside
# another instruction is possible; a candidate is only reported with the classification of its
# immediate, and a mis-hit classifies as an unmapped `immediate` in almost every case (the census
# reports the counts, so a reader can see how many pushes each site has rather than trusting one).
WINDOW = 96
PUSH = b"\x68"


def wide_string(xbe, va, limit=96):
    """The UTF-16LE literal at `va`, or None. Empty if the bytes are not a printable literal."""
    try:
        blob = xbe.read_va(va, limit)
    except KeyError:
        return None
    chars = []
    for i in range(0, len(blob) - 1, 2):
        lo, hi = blob[i], blob[i + 1]
        if hi != 0 or lo == 0:
            return None if not chars else "".join(chars)
        if 0x20 <= lo < 0x7F:
            chars.append(chr(lo))
        else:
            return None if not chars else "".join(chars)
    return "".join(chars) or None


def ascii_string(xbe, va, limit=64):
    out = []
    for i in range(limit):
        try:
            b = xbe.read_va(va + i, 1)[0]
        except KeyError:
            break
        if b == 0:
            break
        if not 0x20 <= b < 0x7F:
            return None
        out.append(chr(b))
    s = "".join(out)
    return s if len(s) >= 3 else None


def classify(xbe, imm, tables, funcs):
    """(kind, text) for one pushed immediate: string / function / table / immediate."""
    if imm == 0:
        return "immediate", "0x00000000"
    sec = xbe.section_for(imm)
    if sec is None:
        return "immediate", "0x%08x" % imm
    text = wide_string(xbe, imm)
    if text:
        return "string", text
    text = ascii_string(xbe, imm)
    if text:
        return "string", text
    if imm in funcs:
        return "function", funcs[imm]
    if imm in tables:
        return "table", "0x%08x" % imm
    return "mapped", "0x%08x" % imm


def pushes_before(xbe, site, window=WINDOW):
    """(va, imm) of every `68 imm32` in the `window` bytes before a call site, in address order."""
    start = site - window
    try:
        blob = xbe.read_va(start, window + 5)
    except KeyError:
        return []
    out = []
    pos = blob.find(PUSH)
    while pos != -1 and pos + 5 <= window:
        imm = struct.unpack_from("<I", blob, pos + 1)[0]
        out.append((start + pos, imm))
        pos = blob.find(PUSH, pos + 1)
    return out


def call_target(xbe, site):
    """The `E8 rel32` target at `site`, or None when the byte there is not a call."""
    try:
        b = xbe.read_va(site, 5)
    except KeyError:
        return None
    if b[0] != 0xE8:
        return None
    rel = struct.unpack_from("<i", b, 1)[0]
    return (site + 5 + rel) & 0xFFFFFFFF


def load_names(path):
    funcs = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            funcs[int(r["address"], 16)] = r.get("name") or ("FUN_%08x" % int(r["address"], 16))
    return funcs


def load_tables(path, names_path):
    tables = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            tables[int(r["table_va"], 16)] = r
    names = {}
    with open(names_path, newline="") as f:
        for r in csv.DictReader(f):
            names[int(r["table_va"], 16)] = r.get("class") or ""
    return tables, names


def cmd_sites(args):
    xbe = Xbe(args.xbe)
    funcs = load_names(os.path.join(REPO, "symbols", "xbox_functions.csv"))
    tables, _ = load_tables(os.path.join(INVENTORY, "xbe_class_tables.csv"),
                            os.path.join(INVENTORY, "xbe_class_table_names.csv"))
    rows = []
    with open(args.sites, newline="") as f:
        for r in csv.DictReader(f):
            site = int(r["site_va"], 16)
            kinds = [classify(xbe, imm, tables, funcs) for _, imm in pushes_before(xbe, site)]
            strs = [t for k, t in kinds if k == "string"]
            tbl = [t for k, t in kinds if k == "table"]
            fn = [t for k, t in kinds if k == "function"]
            tgt = call_target(xbe, site)
            rows.append({
                "site_va": r["site_va"],
                "class": r.get("class", ""),
                "call_target": ("0x%08x" % tgt) if tgt else "",
                "call_name": funcs.get(tgt, "FUN_%08x" % tgt) if tgt else "",
                "pushes": len(kinds),
                "strings": len(strs),
                "functions": len(fn),
                "tables": len(tbl),
                "name_string": strs[-1] if strs else "",
                "outer_string": strs[-2] if len(strs) > 1 else "",
                "table_literals": "|".join(tbl),
                "arguments": ";".join("%s:%s" % (k, t) for k, t in kinds),
            })
    write_csv(args.out, rows, ["site_va", "class", "call_target", "call_name", "pushes", "strings",
                               "functions", "tables", "name_string", "outer_string",
                               "table_literals", "arguments"])
    n = len(rows)
    print("sites %d" % n)
    print("  with a string push            %d" % sum(1 for r in rows if r["strings"]))
    print("  with two or more strings      %d" % sum(1 for r in rows if r["strings"] >= 2))
    print("  with a class-table literal    %d" % sum(1 for r in rows if r["tables"]))
    print("  with a function-start push    %d" % sum(1 for r in rows if r["functions"]))
    print("  push count min/median/max     %d/%d/%d" % (
        min(r["pushes"] for r in rows),
        sorted(r["pushes"] for r in rows)[n // 2],
        max(r["pushes"] for r in rows)))


def cmd_slots(args):
    xbe = Xbe(args.xbe)
    tables, names = load_tables(os.path.join(INVENTORY, "xbe_class_tables.csv"),
                                os.path.join(INVENTORY, "xbe_class_table_names.csv"))
    funcs = load_names(os.path.join(REPO, "symbols", "xbox_functions.csv"))
    parent, seen, byname = {}, {}, {}
    with open(os.path.join(INVENTORY, "classes.csv"), newline="") as f:
        for r in csv.DictReader(f):
            key = (r["package"], r["class"])
            parent[key] = (r["package_of_super"], r["super"]) if r["super"] else None
            seen[r["class"]] = key
            byname[r["class"]] = r["super"]

    def under_actor(cls):
        """Walk the script super chain by name: `package_of_super` names a package that does not
        always match the row's own `package` column (Gameplay vs StargateGame), so a keyed lookup
        stops one link early and reports an Actor descendant as outside the subtree."""
        for _ in range(64):
            if cls == "Actor":
                return True
            cls = byname.get(cls)
            if not cls:
                return False
        return False

    rows = []
    for tva in sorted(tables):
        s19, s20 = struct.unpack("<II", xbe.read_va(tva + 4 * 19, 8))
        cls = names.get(tva) or ""
        rows.append({"table_va": "0x%08x" % tva, "class": cls,
                     "slot19": "0x%08x" % s19, "slot20": "0x%08x" % s20,
                     "slot19_name": funcs.get(s19, "FUN_%08x" % s19),
                     "slot20_name": funcs.get(s20, "FUN_%08x" % s20),
                     "under_actor": "yes" if (cls and under_actor(cls)) else ("no" if cls else "")})
    write_csv(args.out, rows, ["table_va", "class", "slot19", "slot20", "slot19_name",
                               "slot20_name", "under_actor"])
    groups = {}
    for r in rows:
        groups.setdefault((r["slot19"], r["slot20"]), []).append(r)
    print("tables %d, distinct (19,20) pairs %d" % (len(rows), len(groups)))
    for pair, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        named = [m for m in members if m["class"]]
        actor = sum(1 for m in named if m["under_actor"] == "yes")
        print("  %s/%s  tables %d  named %d  under Actor %d  unnamed %d"
              % (pair[0], pair[1], len(members), len(named), actor, len(members) - len(named)))


def write_csv(path, rows, fields):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


class FakeXbe(object):
    """Just enough of `Xbe` for the self-test: one section and a byte blob."""

    class S(object):
        def __init__(self, va, size):
            self.va, self.vsize, self.name = va, size, ".text"

    def __init__(self, va, blob):
        self.sections = [self.S(va, len(blob))]
        self._va, self._blob = va, blob

    def section_for(self, va, executable=None):
        return self.sections[0] if self._va <= va < self._va + len(self._blob) else None

    def read_va(self, va, size):
        if not self.section_for(va) or va + size > self._va + len(self._blob):
            raise KeyError(va)
        off = va - self._va
        return self._blob[off:off + size]


def selftest():
    """No dump: a fabricated window whose pushes are known, and a fabricated string literal."""
    base = 0x1000
    # the blob spans 0x1000..0x3010 so the pushed function and table addresses are mapped, all zero
    blob = bytearray(b"\x90" * 0x80) + bytearray(0x2010 - 0x80)
    # a UTF-16 "AB" literal at 0x1000, then four pushes at 0x1060..: string, function, table, immediate
    blob[0:6] = b"A\x00B\x00\x00"
    pushes = [(0x1060, base), (0x1065, 0x2000), (0x106a, 0x3000), (0x106f, 0x04084004)]
    for va, imm in pushes:
        off = va - base
        blob[off] = 0x68
        blob[off + 1:off + 5] = struct.pack("<I", imm)
    xbe = FakeXbe(base, bytes(blob))
    got = pushes_before(xbe, base + 0x74)
    assert [imm for _, imm in got] == [p[1] for p in pushes], got
    kinds = [classify(xbe, imm, {0x3000: {}}, {0x2000: "FUN_00002000"}) for _, imm in got]
    assert kinds[0] == ("string", "AB"), kinds
    assert kinds[1] == ("function", "FUN_00002000"), kinds
    assert kinds[2] == ("table", "0x00003000"), kinds
    assert kinds[3][0] == "immediate", kinds
    # a call: E8 rel32 at the site resolving to 0x2000
    blob2 = bytearray(b"\x90" * 16)
    blob2[8:13] = b"\xe8" + struct.pack("<i", 0x2000 - (base + 8 + 5))
    x2 = FakeXbe(base, bytes(blob2))
    assert call_target(x2, base + 8) == 0x2000
    assert call_target(x2, base + 0) is None
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", nargs="?", choices=["sites", "slots"])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--sites", default=os.path.join(INVENTORY, "registration_sites.csv"))
    ap.add_argument("--check", action="store_true",
                    help="synthetic self-test; reads no dump and writes nothing")
    args = ap.parse_args()
    if args.check:
        selftest()
        return
    if not args.xbe or not args.mode:
        ap.error("a mode (sites|slots) and --xbe are required without --check")
    if args.mode == "sites":
        args.out = args.out or os.path.join(INVENTORY, "registration_arguments.csv")
        cmd_sites(args)
    else:
        args.out = args.out or os.path.join(INVENTORY, "class_table_slot_pairs.csv")
        cmd_slots(args)


if __name__ == "__main__":
    main()
