#!/usr/bin/env python3
"""Name the class tables from the whole registration-site census, and from the hierarchy when it cannot.

`inventory/xbe_class_tables.csv` (tools/class-tables.py) measures 493 tables of code pointers and the
slot that dispatches `ProcessEvent`, but not which class each table belongs to. The build names that
itself: every class is registered by a call to `FUN_0003af60` with

    FUN_0003af60(outer, <object size>, <class flags>, 0, 0, 0, 0, L"<Class>", ?, L"System",
                 <class flags 2>, &<constructor thunk>, &<parent>)

and the constructor thunk forwards to the constructor that installs the class's table with
`mov dword ptr [this], <table>`. So the chain is

registration -> class literal -> constructor thunk -> constructor -> `mov [this], table`.

**The chain does not name a table on its own, and this tool no longer pretends it does.** Two things
the earlier version got wrong, both measured:

  * **Write order is not evidence.** 30 of the image's 493 registration sites resolve to only three
    tables: 25 pass the shared default thunk `0x00034650` (the root's ctor, so every class with no
    native constructor of its own lands on the root's table) and the texture family passes one thunk
    each. Keeping the last site written named the object root `VertexStreamBase` -- a literal no
    package in the dump declares -- because `0x001eb186` sorts after `0x00064af6`. Nor does the
    *lowest* site help: that would have named the same table `Exporter` (`0x000349e6`).
  * **The read is bounded by the body, and in this image the bound fires nowhere.** A flat 400-byte read
    walks past a short constructor's own `ret` into the next function, which may store its own table, and
    the store is then attributed to the wrong body. The read now stops at the size
    `symbols/xbox_functions.csv` gives the function; reading all 493 registration targets both ways agrees
    on 493 of 493, because every target has a store of its own. The bound is a guard, not a correction,
    and `--check` keeps a case where it bites.

So the name comes from one of two joins, in this order, and the `name_rule` column says which:

  * `unshared-thunk`   -- exactly one site on this table uses a thunk no other site uses; that thunk's
                          constructor installs the table for this class and no other.
  * `hierarchy-ancestor` -- every site shares a thunk, so no site owns the table. Among the literals
                          that `inventory/classes.csv` knows, exactly one is an ancestor-or-self of
                          every other: that one is the class whose constructor the others reuse
                          (`Object` for the object root, `Texture` for the texture family).
  * `ancestor-derived` -- no literal in the group is a common ancestor, but the group has one nearest
                          common ancestor (`FireTexture`/`IceTexture` -> `FractalTexture`). The table
                          is named for the ancestor, and the row says the name is derived.
  * `unresolved`       -- no common ancestor either; the row keeps an empty class and lists its
                          candidates, rather than silently taking one.
  * `no-registration`  -- no site resolves to this table at all.

    tools/class-table-names.py DUMP/default.xbe -o inventory
    tools/class-table-names.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-table-names.py --check-doc docs/systems/CLASS_REGISTRATION.md   # from the committed CSV
    tools/class-table-names.py --check        # synthetic bodies, no dump, writes nothing

The decode is objdump over each enclosing body, so it needs no capstone (the earlier version imported
it, and its artifact therefore could not be regenerated on a host without the package). Inputs are the
image, `inventory/xbe_class_tables.csv`, `inventory/classes.csv` and `symbols/xbox_functions.csv`.
Output is derived measurement: names the build ships plus the addresses that link them.
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
FIELDS = ["table_va", "class", "object_size", "registration_va", "constructor_thunk_va",
          "constructor_va", "installed_at", "evidence", "sites", "name_rule", "site_literals"]
ARTIFACT = os.path.join(ROOT, "inventory/xbe_class_table_names.csv")
DOC = os.path.join(ROOT, "docs/systems/CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-table-names -->"
DOC_END = "<!-- /generated:class-table-names -->"
TABLES = os.path.join(ROOT, "inventory/xbe_class_tables.csv")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
SYMBOLS = os.path.join(ROOT, "symbols/xbox_functions.csv")
THUNK_PREFIX = b"\x8b\x4c\x24\x04"          # mov ecx,[esp+4] -- a constructor thunk's first bytes
WINDOW = 400                                # bytes read when a body is not in the symbol map
IMM = re.compile(r"0x([0-9a-f]+)")


# --------------------------------------------------------------------------- image helpers

class Image:
    """The reads this census needs, over an Xbe or a synthetic buffer."""

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

    def installed_table(self, ctor_va, limit=None):
        """The first `mov [reg], <known table>` inside the body -- bounded by its own size.

        Returns (table, store_va, bounded) or None. `bounded` says whether the read stopped at the
        function's end (a size from the symbol map) or at the flat window.
        """
        n = limit if limit else WINDOW
        b = self.read(ctor_va, n)
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
                return imm, ctor_va + i, bool(limit)
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


def load_tables(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[int(row["table_va"], 16)] = row
    return out


def load_hierarchy(path):
    """class -> super, from the parsed packages. A name declared twice keeps its deepest row."""
    out, depth = {}, {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                d = int(row.get("super_depth") or 0)
            except ValueError:
                d = 0
            name = row["class"]
            if name not in out or d > depth.get(name, -1):
                out[name] = (row["super"] or "").strip()
                depth[name] = d
    return out


def ancestors(hierarchy, name):
    """name and every super above it, nearest first (empty when the name is unknown)."""
    if name not in hierarchy:
        return []
    chain, seen = [], set()
    cur = name
    while cur and cur in hierarchy and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = hierarchy[cur]
    return chain


def is_ancestor(hierarchy, a, b):
    return a in ancestors(hierarchy, b)


def nearest_common(hierarchy, names):
    """The deepest class every name in `names` is a descendant of, or None."""
    known = [n for n in names if n in hierarchy]
    if not known:
        return None
    for a in ancestors(hierarchy, known[0]):
        if all(is_ancestor(hierarchy, a, n) for n in known):
            return a
    return None


def owner(funcs, fstarts, va):
    i = bisect.bisect_right(fstarts, va) - 1
    if i < 0:
        return None
    a, s, n = funcs[i]
    return (a, s, n) if va < a + s else None


def resolve_sites(img, funcs, fstarts, ctor):
    """Every `call ctor` site, with the literal, thunk, target, table and object size it carries."""
    sites = [img.text.va + i for i in range(len(img.tb) - 5)
             if img.tb[i] == 0xE8
             and img.text.va + i + 5 + struct.unpack_from("<i", img.tb, i + 1)[0] == ctor]
    out = []
    for site in sites:
        own = owner(funcs, fstarts, site)
        start, size = (own[0], own[1]) if own else (site - 200, 200)
        ins = img.disasm(start, size)
        idx = next((k for k, (va, t) in enumerate(ins) if va == site and t.startswith("call")), None)
        if idx is None:
            continue
        pushes = []
        for k in range(max(0, idx - 40), idx):
            va, t = ins[k]
            if t.startswith("push"):
                m = IMM.search(t)
                if m:
                    pushes.append(int(m.group(1), 16))
        literal = None
        for value in reversed(pushes):
            w = img.widestr(value)
            if w and w != "System":
                literal = w
                break
        # Argument order is (outer, size, class flags, ...), pushed right to left, so the size is
        # the last immediate push before the call unless the outer pointer was pushed as a literal 0.
        obj = None
        if pushes:
            obj = pushes[-1] if pushes[-1] else (pushes[-2] if len(pushes) > 1 else None)
        table = thunk = target = store = bounded = None
        for value in reversed(pushes):
            if value in img.tables or not img.read(value, 4):
                continue
            for cand in (img.thunk_target(value), value):
                if not cand:
                    continue
                got = img.installed_table(cand, img.size_of(cand))
                if got:
                    table, thunk, target = got[0], value, cand
                    store, bounded = got[1], got[2]
                    break
            if table:
                break
        out.append({"site_va": site, "class_literal": literal, "object_size": obj,
                    "thunk_va": thunk, "thunk_target": target, "table_va": table,
                    "installed_at": store, "bounded": bounded})
    return out


def choose_name(sites, per_thunk, hierarchy):
    """(name, rule, chosen_site) for one table, from its whole site list."""
    unshared = [s for s in sites if per_thunk.get(s["thunk_va"], 0) == 1]
    if len(unshared) == 1:
        return unshared[0]["class_literal"], "unshared-thunk", unshared[0]
    literals = [s["class_literal"] for s in sites if s["class_literal"]]
    known = sorted({l for l in literals if l in hierarchy})
    common = [l for l in known if all(is_ancestor(hierarchy, l, o) for o in known)]
    if len(common) == 1:
        site = next(s for s in sites if s["class_literal"] == common[0])
        return common[0], "hierarchy-ancestor", site
    derived = nearest_common(hierarchy, known)
    if derived and derived not in literals:
        return derived, "ancestor-derived", sites[0]
    return "", "unresolved", sites[0]


def census(xbe, ctor):
    tables = load_tables(TABLES)
    hierarchy = load_hierarchy(CLASSES)
    img = Image()
    img.xbe, img.data, img.tables = xbe, xbe.data, set(tables)
    img.text = next(s for s in xbe.sections if s.name == ".text")
    img.tb = xbe.data[img.text.foff:img.text.foff + img.text.fsize]
    funcs, fstarts = load_symbols(SYMBOLS)
    sizes = {a: s for a, s, _ in funcs}
    img.size_of = lambda va: sizes.get(va)
    sites = resolve_sites(img, funcs, fstarts, ctor)

    per_table, per_thunk = {}, {}
    for s in sites:
        if s["table_va"] is not None:
            per_table.setdefault(s["table_va"], []).append(s)
        if s["thunk_va"] is not None:
            per_thunk[s["thunk_va"]] = per_thunk.get(s["thunk_va"], 0) + 1

    rows = []
    for table in sorted(tables):
        group = per_table.get(table, [])
        row = {"table_va": "0x%08x" % table, "class": "", "object_size": "", "registration_va": "",
               "constructor_thunk_va": "", "constructor_va": "", "installed_at": "",
               "evidence": "no registration names this table", "sites": "0",
               "name_rule": "no-registration", "site_literals": ""}
        if group:
            name, rule, site = choose_name(group, per_thunk, hierarchy)
            row.update({
                "class": name or "",
                "object_size": "" if site["object_size"] is None else site["object_size"],
                "registration_va": "0x%08x" % site["site_va"],
                "constructor_thunk_va": ("0x%08x" % site["thunk_va"]
                                         if site["thunk_va"] is not None else ""),
                "constructor_va": ("0x%08x" % site["thunk_target"]
                                   if site["thunk_target"] is not None else ""),
                "installed_at": ("0x%08x" % site["installed_at"]
                                 if site["installed_at"] is not None else ""),
                "evidence": evidence(rule, site, len(group), name),
                "sites": str(len(group)),
                "name_rule": rule,
                "site_literals": ", ".join(s["class_literal"] or "?" for s in
                                           sorted(group, key=lambda s: s["site_va"])),
            })
        rows.append(row)
    return rows


def evidence(rule, site, group, name):
    if rule == "unshared-thunk":
        return ("class literal at 0x%08x, whose constructor thunk no other registration site uses"
                % site["site_va"])
    if rule == "hierarchy-ancestor":
        return ("%d sites share one constructor thunk; this literal is the common ancestor of the "
                "group, so the table is the class whose constructor the others reuse" % group)
    if rule == "ancestor-derived":
        return ("%d sites share one constructor thunk and name no common ancestor of their own; %s is "
                "the nearest common ancestor, and the name is derived from it" % (group, name))
    if rule == "unresolved":
        return ("%d sites share one constructor thunk and have no common ancestor in the parsed "
                "packages, so no name is claimed" % group)
    return "no registration names this table"


def render(rows):
    """The tables more than one registration resolves to, and how the rule named them."""
    multi = [r for r in rows if r["sites"] not in ("", "0", "1")]
    multi.sort(key=lambda r: (-int(r["sites"]), r["table_va"]))
    lines = ["| table | name | rule | sites | literals |", "|---|---|---|---|---|"]
    for r in multi:
        lines.append("| %s | %s | %s | %s | %s |"
                     % (r["table_va"], r["class"] or "-", r["name_rule"], r["sites"],
                        r["site_literals"]))
    rules = {}
    for r in rows:
        rules[r["name_rule"]] = rules.get(r["name_rule"], 0) + 1
    named = sum(1 for r in rows if r["class"])
    lines.append("")
    lines.append("%d tables: %d named (%d by an unshared constructor thunk, %d by the hierarchy a "
                 "shared thunk forces, %d derived from a nearest common ancestor), %d unresolved, "
                 "%d with no registration. %d tables are reached by more than one registration site, "
                 "so a write-order rule would name them by whichever site happened to be written "
                 "last."
                 % (len(rows), named, rules.get("unshared-thunk", 0),
                    rules.get("hierarchy-ancestor", 0), rules.get("ancestor-derived", 0),
                    rules.get("unresolved", 0), rules.get("no-registration", 0), len(multi)))
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
    """A 0x8000-byte buffer at 0x1000 with two shared registrations, one unshared, and a short ctor.

    Layout: registration window at 0x1000 (three call sites), thunk at 0x2000, root ctor at 0x4000
    (installs 0x5000), short ctor at 0x4100 (8 bytes, no store of its own) and the table store the
    short ctor must *not* inherit at 0x4120 (inside the next body).

    The two shared literals are `Root` (the ancestor) and `Leaf`; the unshared one is `Own`.
    """

    def __init__(self):
        def site(at, literal, thunk):
            return (b"\x68" + struct.pack("<I", literal)
                    + b"\x68" + struct.pack("<I", thunk)
                    + b"\xe8" + struct.pack("<i", 0x1000 + 0x4000 - (0x1000 + at + 10)))
        body = (site(0, 0x6000, 0x2000)                 # Root, thunk 0x2000 (shared)
                + site(6, 0x6008, 0x2000)               # Leaf, same thunk
                + site(0xc, 0x6010, 0x2010))            # Own, its own thunk
        def thunk_to(target, va):
            # mov ecx,[esp+4]; test ecx,ecx; je +3; jmp target  -- 13 bytes
            return (b"\x8b\x4c\x24\x04\x85\xc9\x74\x03\xe9" + struct.pack("<i", target - (va + 13)))
        thunk = thunk_to(0x4000, 0x2000) + b"\xcc\xcc\xcc" + thunk_to(0x4100, 0x2010)
        root = b"\x8b\xc1\xc7\x00\x00\x50\x00\x00\xc3"  # mov eax,ecx; mov [eax],0x5000; ret
        short = b"\x8b\xc1\xc3"                         # mov eax,ecx; ret  -- stores nothing
        later = b"\x8b\xc1\xc7\x00\x00\x50\x00\x00\xc3"  # a neighbour body that stores 0x5000
        self.data = bytearray(b"\xcc" * 0x8000)
        self.data[0:len(body)] = body
        self.data[0x1000:0x1000 + len(thunk)] = thunk       # VA 0x2000
        self.data[0x3000:0x3000 + len(root)] = root         # VA 0x4000
        self.data[0x3100:0x3100 + len(short)] = short       # VA 0x4100
        self.data[0x3120:0x3120 + len(later)] = later       # VA 0x4120
        self.data[0x5000:0x5000 + 6] = "Root".encode("utf-16-le") + b"\x00\x00"
        self.data[0x5008:0x5008 + 6] = "Leaf".encode("utf-16-le") + b"\x00\x00"
        self.data[0x5010:0x5010 + 6] = "Own".encode("utf-16-le") + b"\x00\x00"
        self.tables = {0x5000}
        self.text = type("S", (), {"va": 0x1000, "vsize": 0x8000, "foff": 0, "fsize": 0x8000})()
        self.tb = bytes(self.data)
        self.size_of = lambda va: {0x4000: 9, 0x4100: 3}.get(va)

    def sec_of(self, va):
        return self.text if 0x1000 <= va < 0x9000 else None


def self_check():
    bad = []
    img = Fake()
    if img.thunk_target(0x2000) != 0x4000:
        bad.append("thunk target: %r" % (img.thunk_target(0x2000),))
    if img.thunk_target(0x2010) != 0x4100:
        bad.append("second thunk target: %r" % (img.thunk_target(0x200a),))
    got = img.installed_table(0x4000, img.size_of(0x4000))
    if not got or got[0] != 0x5000 or not got[2]:
        bad.append("bounded install: %r" % (got,))
    if img.installed_table(0x4100, 3) is not None:
        bad.append("the short ctor inherited its neighbour store")
    hier = {"Root": "", "Leaf": "Root", "Other": "Root"}
    shared = [{"site_va": 0x10, "class_literal": "Root", "object_size": 8, "thunk_va": 0x2000,
               "thunk_target": 0x4000, "table_va": 0x5000, "installed_at": 0x4002},
              {"site_va": 0x20, "class_literal": "Leaf", "object_size": 8, "thunk_va": 0x2000,
               "thunk_target": 0x4000, "table_va": 0x5000, "installed_at": 0x4002}]
    name, rule, _ = choose_name(shared, {0x2000: 2}, hier)
    if (name, rule) != ("Root", "hierarchy-ancestor"):
        bad.append("hierarchy-ancestor: %r %r" % (name, rule))
    name, rule, _ = choose_name(shared[:1], {0x2000: 1}, hier)
    if (name, rule) != ("Root", "unshared-thunk"):
        bad.append("unshared-thunk: %r %r" % (name, rule))
    sib = [{"site_va": 0x10, "class_literal": "Leaf", "object_size": 8, "thunk_va": 0x2000,
            "thunk_target": 0x4000, "table_va": 0x5000, "installed_at": 0x4002},
           {"site_va": 0x20, "class_literal": "Other", "object_size": 8, "thunk_va": 0x2008,
            "thunk_target": 0x4000, "table_va": 0x5000, "installed_at": 0x4002}]
    name, rule, _ = choose_name(sib, {0x2000: 1, 0x2008: 1}, hier)
    if (name, rule) != ("Root", "ancestor-derived"):
        bad.append("ancestor-derived: %r %r" % (name, rule))
    orphan = [{"site_va": 0x10, "class_literal": "Aaa", "object_size": 8, "thunk_va": 0x2000,
               "thunk_target": 0x4000, "table_va": 0x5000, "installed_at": 0x4002},
              {"site_va": 0x20, "class_literal": "Bbb", "object_size": 8, "thunk_va": 0x2000,
               "thunk_target": 0x4000, "table_va": 0x5000, "installed_at": 0x4002}]
    name, rule, _ = choose_name(orphan, {0x2000: 2}, {})
    if (name, rule) != ("", "unresolved"):
        bad.append("unresolved: %r %r" % (name, rule))

    rows = [{"table_va": "0x00005000", "class": "Root", "object_size": "8",
             "registration_va": "0x00000010", "constructor_thunk_va": "0x00002000",
             "constructor_va": "0x00004000", "installed_at": "0x00004002",
             "evidence": "x", "sites": "2", "name_rule": "hierarchy-ancestor",
             "site_literals": "Root, Leaf"}]
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "a.csv")
    write_artifact(rows, path)
    if read_artifact(path) != rows:
        bad.append("artifact round trip differs")
    out = render(read_artifact(path))
    if "| 0x00005000 | Root | hierarchy-ancestor | 2 |" not in out or "1 tables: 1 named" not in out:
        bad.append("render: %r" % out)
    if splice_doc(path, out) is not None:
        bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL class-table-names: " + "; ".join(bad))
        return 1
    print("ok   class-table-names: an unshared thunk wins, a shared one falls back to the hierarchy, "
          "and a short body does not inherit its neighbour store")
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
            path = os.path.join(args.out, "xbe_class_table_names.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    print(render(rows))
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice_doc(path, render(rows))
        if new is None:
            print("FAIL class-table-names: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL class-table-names: %s is stale against the committed artifact" % path)
                return 1
            print("ok   class-table-names: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
