#!/usr/bin/env python3
"""One row per class registration site: the literal it pushes, its constructor thunk, and the table.

`inventory/xbe_class_table_names.csv` (`tools/class-table-names.py`) is keyed by **table**, so when more
than one registration site resolves to the same table the writer keeps the last one and the others are
dropped without a word. This tool keeps every site, and the difference is not academic: **25 of the image's
493 registration sites pass the same constructor thunk `0x00034650`**, whose guarded `jmp` lands on the
object root's ctor `0x00062ce0`. A class with no native constructor of its own is registered with that
shared default, so its registration names the *root's* table and not its own -- and the per-table artifact
showed whichever of those 25 sites happened to be written last (`VertexStreamBase`, `0x001eb186`) where the
site at `0x00064af6` registers `Object` with the identical thunk; `tools/class-table-names.py` now resolves
that row from the whole site list and this page's `committed name` column follows it (`Object`).

Every row therefore carries both counts, and the **size** the site registers -- the call's second
argument, which is the second-to-last push at all 493 sites whether the outer pointer is a literal or a
register. That argument separates classes the thunk cannot: the 25 sites sharing the object root's
constructor push **25 distinct sizes**, so the registration does carry a per-class fact where its
constructor does not. A registration that cannot name its own table is marked `shared-thunk` rather than
being read as evidence:

    tools/class-registrations.py DUMP/default.xbe -o inventory
    tools/class-registrations.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-registrations.py --check-doc docs/systems/CLASS_REGISTRATION.md   # from the committed CSV
    tools/class-registrations.py --check        # synthetic bodies, no dump, writes nothing

The decode is objdump over each enclosing body, so it needs no capstone (`tools/class-table-names.py` does,
and that is why its artifact cannot be regenerated on a host without it). Addresses, class literals and
counts only: no package or image content is written.
"""
import argparse
import bisect
import csv
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["site_va", "class_literal", "object_size", "thunk_va", "thunk_target", "table_va",
          "table_class", "sites_for_table", "sites_for_thunk", "verdict"]
ARTIFACT = os.path.join(ROOT, "inventory/class_registrations.csv")
DOC = os.path.join(ROOT, "docs/systems/CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-registrations -->"
DOC_END = "<!-- /generated:class-registrations -->"
THUNK_PREFIX = b"\x8b\x4c\x24\x04"      # mov ecx,[esp+4]
IMM = re.compile(r"0x([0-9a-f]+)")


# --------------------------------------------------------------------------- image helpers

class Image:
    """The four reads this census needs, over an Xbe or a synthetic buffer."""

    def sec_of(self, va):
        for s in self.xbe.sections:
            if s.va <= va < s.va + s.vsize:
                return s
        return None

    def read(self, va, n):
        s = self.sec_of(va)
        if not s or va + n > s.va + s.vsize:
            return None
        return self.data[s.foff + (va - s.va):s.foff + (va - s.va) + n]

    def widestr(self, va, maxchars=48):
        b = self.read(va, maxchars * 2)
        if not b:
            return None
        out = []
        for i in range(0, len(b) - 1, 2):
            ch = struct.unpack_from("<H", b, i)[0]
            if ch == 0:
                break
            if 32 <= ch < 127:
                out.append(chr(ch))
            else:
                return None
        name = "".join(out)
        return name if 3 <= len(name) <= maxchars else None

    def thunk_target(self, va):
        """A constructor thunk's forward target: mov ecx,[esp+4]; test ecx,ecx; je; jmp rel32."""
        b = self.read(va, 16)
        if not b or b[:4] != THUNK_PREFIX:
            return None
        i = 4
        while i + 5 < len(b):
            if b[i:i + 2] == b"\x85\xc9" or b[i] == 0x74 or b[i] == 0xEB:
                i += 2
            elif b[i] == 0xE9:
                return va + i + 5 + struct.unpack_from("<i", b, i + 1)[0]
            else:
                return None
        return None

    def installed_table(self, ctor_va):
        b = self.read(ctor_va, 400)
        if not b:
            return None
        for i in range(len(b) - 6):
            if b[i] != 0xC7:                  # mov r/m32, imm32
                continue
            modrm = b[i + 1]
            if (modrm >> 6) or ((modrm >> 3) & 7):
                continue
            imm = struct.unpack_from("<I", b, i + 2)[0]
            if imm in self.tables:
                return imm
        return None

    def disasm(self, start, size):
        key = (start, size)
        if not hasattr(self, "cache"):
            self.cache = {}
        if key in self.cache:
            return self.cache[key]
        blob = self.read(start, size) or b""
        path = tempfile.mktemp(suffix=".bin")
        with open(path, "wb") as fh:
            fh.write(blob)
        try:
            out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                                  "--adjust-vma=0x%x" % start, path],
                                 capture_output=True, text=True).stdout
        finally:
            os.unlink(path)
        rows = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            head = parts[0].strip()
            if not head.endswith(":") or not all(c in "0123456789abcdef" for c in head[:-1]):
                continue
            rows.append((int(head[:-1], 16), parts[-1].strip()))
        self.cache[key] = rows
        return rows


def load_symbols(path):
    funcs = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            funcs.append((int(row["address"], 16), int(row["size"]), row["name"]))
    funcs.sort()
    return funcs, [f[0] for f in funcs]


def resolve_sites(img, funcs, fstarts, ctor):
    """Every `call ctor` site, with the literal and the thunk its pushes name."""
    sites = [img.text.va + i for i in range(len(img.tb) - 5)
             if img.tb[i] == 0xE8
             and img.text.va + i + 5 + struct.unpack_from("<i", img.tb, i + 1)[0] == ctor]
    out = []
    for site in sites:
        i = bisect.bisect_right(fstarts, site) - 1
        own = funcs[i] if i >= 0 else None
        start, size = (own[0], own[1]) if own and site < own[0] + own[1] else (site - 200, 200)
        ins = img.disasm(start, size)
        idx = next((k for k, (va, t) in enumerate(ins) if va == site and t.startswith("call")), None)
        if idx is None:
            continue
        pushes, args = [], []
        for k in range(max(0, idx - 40), idx):
            va, t = ins[k]
            if t.startswith("push"):
                args.append(t)
                m = IMM.search(t)
                if m:
                    pushes.append(int(m.group(1), 16))
        # The call's second argument is the registered object size and it is the second-to-last push:
        # arg1 (the outer) is the last push at every site, literal or register.
        m = IMM.search(args[-2]) if len(args) >= 2 else None
        size = int(m.group(1), 16) if m else None
        literal = None
        for value in reversed(pushes):
            w = img.widestr(value)
            if w and w != "System":
                literal = w
                break
        table = thunk = target = None
        for value in reversed(pushes):
            if value in img.tables or not img.read(value, 4):
                continue
            for cand in (img.thunk_target(value), value):
                if not cand:
                    continue
                got = img.installed_table(cand)
                if got:
                    table, thunk, target = got, value, cand
                    break
            if table:
                break
        out.append({"site_va": site, "class_literal": literal, "object_size": size,
                    "thunk_va": thunk, "thunk_target": target, "table_va": table})
    return out


def census(xbe, ctor):
    tables = set()
    with open(os.path.join(ROOT, "inventory/xbe_class_tables.csv"), newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tables.add(int(row["table_va"], 16))
    names = {}
    with open(os.path.join(ROOT, "inventory/xbe_class_table_names.csv"), newline="",
              encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            names[int(row["table_va"], 16)] = row["class"]
    img = Image()
    img.xbe, img.data, img.tables, img.names = xbe, xbe.data, tables, names
    img.text = next(s for s in xbe.sections if s.name == ".text")
    img.tb = xbe.data[img.text.foff:img.text.foff + img.text.fsize]
    funcs, fstarts = load_symbols(os.path.join(ROOT, "symbols/xbox_functions.csv"))
    sites = resolve_sites(img, funcs, fstarts, ctor)
    per_table, per_thunk = {}, {}
    for s in sites:
        if s["table_va"] is not None:
            per_table[s["table_va"]] = per_table.get(s["table_va"], 0) + 1
        if s["thunk_va"] is not None:
            per_thunk[s["thunk_va"]] = per_thunk.get(s["thunk_va"], 0) + 1
    rows = []
    for s in sites:
        table = s["table_va"]
        shared = table is not None and per_table.get(table, 0) > 1
        rows.append({
            "site_va": "0x%08x" % s["site_va"],
            "class_literal": s["class_literal"] or "",
            "object_size": str(s["object_size"]) if s["object_size"] is not None else "",
            "thunk_va": "0x%08x" % s["thunk_va"] if s["thunk_va"] is not None else "",
            "thunk_target": "0x%08x" % s["thunk_target"] if s["thunk_target"] is not None else "",
            "table_va": "0x%08x" % table if table is not None else "",
            "table_class": names.get(table, "") if table is not None else "",
            "sites_for_table": str(per_table.get(table, 0)) if table is not None else "0",
            "sites_for_thunk": str(per_thunk.get(s["thunk_va"], 0)) if s["thunk_va"] is not None else "0",
            "verdict": ("resolve-failed" if table is None
                        else "shared-thunk" if shared else "names-own-table"),
        })
    return rows


def render(rows, total=None):
    """The tables that more than one registration resolves to, one line each."""
    total = total if total is not None else rows
    groups = {}
    for r in total:
        if r["table_va"]:
            groups.setdefault(r["table_va"], []).append(r)
    multi = {t: v for t, v in groups.items() if len(v) > 1}
    lines = ["| table | committed name | sites | class literals the registrations push |",
             "|---|---|---|---|"]
    for table, v in sorted(multi.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        literals = ", ".join("%s (`%s`)" % (r["class_literal"] or "?", r["site_va"]) for r in v)
        lines.append("| %s | %s | %d | %s |" % (table, v[0]["table_class"] or "-", len(v), literals))
    shared = [r for r in total if r["verdict"] == "shared-thunk"]
    unread = [r for r in total if r["verdict"] == "resolve-failed"]
    sized = sorted((int(r["object_size"]) for r in shared if r["object_size"]))
    lines.append("")
    lines.append("| class literal | size | site | table |")
    lines.append("|---|---|---|---|")
    for r in sorted(shared, key=lambda r: int(r["object_size"] or 0)):
        lines.append("| %s | %s | %s | %s |"
                     % (r["class_literal"] or "?", r["object_size"] or "-", r["site_va"],
                        r["table_va"] or "-"))
    lines.append("")
    lines.append("The %d sites that share a constructor thunk still register **%d distinct sizes**, %s to %s "
                 "bytes: the thunk cannot name the class, but the second argument separates them."
                 % (len(shared), len(set(sized)), sized[0] if sized else "-",
                    sized[-1] if sized else "-"))
    lines.append("")
    lines.append("%d registration sites: %d resolve to a class table, %d sit on a table that more than one "
                 "site resolves to, and %d resolve to no table at all. %d sites therefore do not name the "
                 "table they are attached to."
                 % (len(total), sum(1 for r in total if r["table_va"]),
                    sum(1 for r in total if r["table_va"] and int(r["sites_for_table"]) > 1),
                    len(unread), len(shared)))
    return "\n".join(lines)


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test

class Fake(Image):
    """A 0x8000-byte buffer at 0x1000 with a registration window, a thunk and a ctor."""

    def __init__(self):
        # push thunk; push literal; push size; push ebx (the outer); call ctor -- 16 + 5 bytes
        body = (b"\x68" + struct.pack("<I", 0x2000)                    # push thunk
                + b"\x68" + struct.pack("<I", 0x6000)                  # push the class literal
                + b"\x68" + struct.pack("<I", 0x40)                    # push the object size
                + b"\x53"                                             # push ebx (arg1, outer)
                + b"\xe8" + struct.pack("<i", 0x4000 - (0x1000 + 16 + 5)))  # call ctor
        thunk = (b"\x8b\x4c\x24\x04\x85\xc9\x74\x03\xe9"
                 + struct.pack("<i", 0x4000 - (0x2000 + 8 + 5)))       # jmp 0x4000
        ctor = b"\x8b\xc1\xc7\x00\x00\x50\x00\x00\xc3"                 # mov eax,ecx; mov [eax],0x5000; ret
        self.data = bytearray(b"\xcc" * 0x8000)
        self.data[0:len(body)] = body
        self.data[0x1000:0x1000 + len(thunk)] = thunk   # VA 0x2000
        self.data[0x3000:0x3000 + len(ctor)] = ctor
        self.data[0x5000:0x5000 + 8] = "Reg".encode("utf-16-le") + b"\x00\x00"  # VA 0x6000
        self.tables = {0x5000}
        self.text = type("S", (), {"va": 0x1000, "vsize": 0x8000, "foff": 0, "fsize": 0x8000})()
        self.tb = bytes(self.data)

    def sec_of(self, va):
        return self.text if 0x1000 <= va < 0x9000 else None


def self_check():
    bad = []
    img = Fake()
    if img.thunk_target(0x2000) != 0x4000:
        bad.append("thunk target: %r" % (img.thunk_target(0x2000),))
    if img.installed_table(0x4000) != 0x5000:
        bad.append("installed table: %r" % (img.installed_table(0x4000),))
    if img.widestr(0x6000) != "Reg":
        bad.append("wide literal: %r" % (img.widestr(0x5000),))
    got = resolve_sites(img, [(0x1000, 0x40, "FUN_reg")], [0x1000], 0x4000)
    if len(got) != 1 or got[0]["table_va"] != 0x5000 or got[0]["class_literal"] != "Reg":
        bad.append("site resolve: %r" % (got,))
    if got[0]["object_size"] != 0x40:
        bad.append("the size is the second-to-last push, not the last: %r" % (got[0]["object_size"],))

    rows = [{"site_va": "0x00001000", "class_literal": "A", "object_size": "40",
             "thunk_va": "0x0000100c", "thunk_target": "0x00004000", "table_va": "0x00005000",
             "table_class": "Root", "sites_for_table": "2", "sites_for_thunk": "2",
             "verdict": "shared-thunk"},
            {"site_va": "0x00002000", "class_literal": "B", "object_size": "44",
             "thunk_va": "0x0000100c", "thunk_target": "0x00004000", "table_va": "0x00005000",
             "table_class": "Root", "sites_for_table": "2", "sites_for_thunk": "2",
             "verdict": "shared-thunk"}]
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "a.csv")
    write_artifact(rows, path)
    if read_artifact(path) != rows:
        bad.append("artifact round trip differs")
    out = render(read_artifact(path))
    if "| 0x00005000 | Root | 2 |" not in out or "2 registration sites" not in out:
        bad.append("render: %r" % out)
    if bad:
        print("FAIL class-registrations: " + "; ".join(bad))
        return 1
    print("ok   class-registrations: a shared thunk is visible per site, and the artifact round-trips")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("--ctor", default="0x0003af60", help="the class-object constructor")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc:
        rows = read_artifact(ARTIFACT)
    else:
        if not args.xbe:
            ap.error("the image is required unless --check, --write-doc or --check-doc is given")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from xbe import Xbe

        rows = census(Xbe(args.xbe), int(args.ctor, 16))
        if args.out:
            path = os.path.join(args.out, "class_registrations.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    print(render(rows))
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice_doc(path, render(rows))
        if new is None:
            print("FAIL class-registrations: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL class-registrations: %s is stale against the committed artifact" % path)
                return 1
            print("ok   class-registrations: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
