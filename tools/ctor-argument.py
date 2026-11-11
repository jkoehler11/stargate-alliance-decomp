"""What the constructor pointer a class-registration site pushes actually is.

`registration-sites.py` names a class from the literals a registration site pushes and, with
`--xbe`, follows the pushed pointer through a thunk to the dword its body stores. This tool asks the
next question, the one U8's residual hinged on: **does that stored dword distinguish the class at
all?** A site's pointer is evidence for a name only when the body it lands in is that class's own
constructor. If one tiny body receives twenty-four sites and stores one fixed dword, then every one
of those sites is described identically by it and the dword names none of them.

For each site it reads the `68 imm32` pushes in the 96 bytes before the call, keeps the ones whose
immediate is mapped by a section *and* whose resolved body is a known function start (or, failing
that, sits in the section that holds the build's code), resolves a direct `E9` thunk or the MSVC
null-check prologue (`MOV ECX,[ESP+4]; TEST ECX,ECX; JZ +5; JMP rel32`), decodes the `C7 /0` store in
the target body, and scores the result three ways:

  sites_sharing   how many registration sites push that same constructor
  live_classes    how many distinct classes object memory pairs with the stored dword (optional,
                  from a live class-table read: `inventory/live_class_tables*.csv`)
  verdict         `per-class`  one site, one class -- the stored dword can carry the name
                  `shared-default`  several sites push it: it is the default constructor, not a name
                  `shared-table`    one site, but one dword for many classes: it is not an identity
                  `ambiguous-ctor`  more than one function start is pushed in the window
                  `neighbour-window`  the ctor installs a table named for a *different* class than the
                  site's own literal: the 96-byte window caught the next registration's push
                  `no-store`  the body stores no immediate in its first bytes

`--tables inventory/xbe_class_table_names.csv` adds `ctor_class` (the name the class-table map gives
the stored table) and `agrees`, so a `per-class` row can be told from a row whose constructor belongs
to the registration site that follows it.

**Section flags cannot tell code from data in this build.** Every section of this XBE carries an
executable bit (`flags 0x36` for `.text`, and `0x6` for `.rdata`, `0x7` for `.data`), so a filter on
`section_for(va, executable=True)` admits the `.rdata` pointer `0x004c7008` that *every* registration
site pushes as a literal and resolves all 493 sites to it. A push is code evidence only when the body
it lands in is a **known function start** (`symbols/xbox_functions.csv`), which is what this tool
requires; the section that holds those starts is derived from the symbol map, not from the flags.

Usage:
    ctor-argument.py DUMP/default.xbe -o inventory/registration_ctor_arguments.csv \
        [--sites inventory/registration_sites.csv] [--live inventory/live_class_tables_m00.csv]
    ctor-argument.py --check          # synthetic, no dump

The stored dword is game-derived, but a virtual address is a locator, not content: this artifact is
addresses and counts, the same shape as `inventory/xbe_class_table_names.csv`.
"""

import argparse
import csv
import struct
import sys

BACK = 96          # bytes before the call scanned for pushes; same window registration-sites.py uses
BODY = 64          # bytes of a target body scanned for the store; 32 missed every
                   # constructor that calls its base before installing the table (71 no-store, 7 at 64)
NULLCHECK = bytes([0x8B, 0x4C, 0x24, 0x04, 0x85, 0xC9, 0x74])


def reader(image):
    return getattr(image, "read", None) or image.read_va


def load_functions(path):
    """VA -> name from `symbols/xbox_functions.csv`; the proof that a pointer names code."""
    out = {}
    for r in csv.DictReader(open(path, newline="")):
        va = r.get("address") or r.get("va") or ""
        if not va:
            continue
        try:
            out[int(va, 16)] = r.get("name", "")
        except ValueError:
            continue
    return out


def code_section(image, functions):
    """Name of the section holding the most known function starts (`.text` in this build)."""
    section_for = getattr(image, "section_for", None)
    if section_for is None or not functions:
        return None
    counts = {}
    for va in functions:
        try:
            s = section_for(va)
        except Exception:
            continue
        if s is not None:
            name = getattr(s, "name", None) or str(s)
            counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _section_name(image, va):
    section_for = getattr(image, "section_for", None)
    if section_for is None:
        return None
    try:
        s = section_for(va)
    except Exception:
        return None
    return None if s is None else (getattr(s, "name", None) or str(s))


def resolve(image, va, depth=4):
    """Follow a direct `E9` thunk, or the null-check prologue, to the body that runs."""
    rd = reader(image)
    seen = set()
    for _ in range(depth):
        if va in seen:
            return va
        seen.add(va)
        try:
            b = rd(va, 16)
        except Exception:
            return va
        target = None
        if b[0] == 0xE9:
            target = va + 5 + struct.unpack_from("<i", b, 1)[0]
        elif b[:7] == NULLCHECK and b[8] == 0xE9:
            # 8B 4C 24 04  MOV ECX,[ESP+4]
            # 85 C9        TEST ECX,ECX
            # 74 05        JZ +5
            # E9 rel32     JMP ctor     (the compiler's forwarding constructor)
            target = va + 13 + struct.unpack_from("<i", b, 9)[0]
        if target is None:
            return va
        va = target
    return va


def ctor_pushes(image, addr, functions, code=None, back=BACK):
    """Every push in the window whose pointer is code evidence, nearest the call last.

    A push counts as a *strong* candidate when the body it resolves to is a known function start,
    and as *weak* when it at least lands in the section that holds the build's code. Data pointers
    (`.rdata` literals, tables) are excluded by the function-start test, not by section flags.
    """
    rd = reader(image)
    try:
        raw = rd(addr - back, back)
    except Exception:
        return []
    strong, weak = [], []
    for j in range(len(raw) - 4):
        if raw[j] != 0x68:
            continue
        va = struct.unpack_from("<I", raw, j + 1)[0]
        pushed_in = _section_name(image, va)
        if pushed_in is None:                 # not a mapped VA: an integer argument
            continue
        body = resolve(image, va)
        if body in functions:
            strong.append((addr - back + j, va, body, "function-start"))
        elif code is not None and pushed_in == code:
            weak.append((addr - back + j, va, body, "weak-code"))
    return strong or weak


def stored_immediates(body):
    """Every `C7 /0 imm32` store (mod=00) in `body` as (offset, imm32)."""
    out = []
    for i in range(len(body) - 6):
        if body[i] != 0xC7:
            continue
        if body[i + 1] & 0xF8:          # modrm mod=00, reg=/0: C7 00/C7 03/C7 06 (mov [reg],imm32)
            continue
        out.append((i, struct.unpack_from("<I", body, i + 2)[0]))
    return out


def body_size(body):
    """Bytes up to and including the first RET, or '' -- a 9-byte body is the whole story."""
    i = body.find(b"\xC3")
    return i + 1 if i >= 0 else ""


def analyse(image, sites, functions=None, live=None, tables=None):
    """One row per site: the pushed constructor, what it stores, and whether that names anything."""
    rd = reader(image)
    functions = functions or {}
    code = code_section(image, functions)
    rows = []
    for site in sites:
        pushes = ctor_pushes(image, site["site_va"], functions, code)
        row = {
            "site_va": "0x%08x" % site["site_va"],
            "class": site.get("class", ""),
            "class_table": site.get("class_table", ""),
            "code_pushes": len(pushes),
            "ctor_candidates": len(pushes) if pushes and pushes[0][3] == "function-start" else "",
            "ctor_evidence": pushes[-1][3] if pushes else "",
            "ctor_pointer": "0x%08x" % pushes[-1][1] if pushes else "",
            "ctor": "",
            "ctor_size": "",
            "stored_table": "",
            "stored_stores": "",
            "ctor_class": "",
            "agrees": "",
            "sites_sharing": 1,
            "live_classes": "",
            "live_objects": "",
            "verdict": "pending",
        }
        if pushes:
            body_va = pushes[-1][2]
            row["ctor"] = "0x%08x" % body_va
            try:
                body = rd(body_va, BODY)
            except Exception:
                body = b""
            row["ctor_size"] = body_size(body)
            stores = stored_immediates(body)
            row["stored_stores"] = len(stores)
            if stores:
                row["stored_table"] = "0x%08x" % stores[0][1]
        rows.append(row)

    sharing = {}
    for row in rows:
        if row["ctor"]:
            sharing[row["ctor"]] = sharing.get(row["ctor"], 0) + 1
    for row in rows:
        row["sites_sharing"] = sharing.get(row["ctor"], 0) if row["ctor"] else ""

    if tables:
        for row in rows:
            if not row["stored_table"]:
                continue
            name = tables.get(int(row["stored_table"], 16), "")
            row["ctor_class"] = name
            if name and row["class"]:
                row["agrees"] = "yes" if name == row["class"] else "no"

    if live:
        table_classes = {}
        for table_va, (classes, objects) in live.items():
            table_classes["0x%08x" % table_va] = (len(classes), objects)
        for row in rows:
            hit = table_classes.get(row["stored_table"])
            if hit:
                row["live_classes"], row["live_objects"] = hit

    for row in rows:
        if not row["ctor"]:
            row["verdict"] = "no-ctor-push"
        elif isinstance(row["ctor_candidates"], int) and row["ctor_candidates"] > 1:
            row["verdict"] = "ambiguous-ctor"
        elif row["sites_sharing"] and row["sites_sharing"] > 1:
            row["verdict"] = "shared-default"
        elif row["agrees"] == "no":
            row["verdict"] = "neighbour-window"
        elif row["stored_table"]:
            row["verdict"] = "shared-table" if (row["live_classes"] or 0) > 1 else "per-class"
        else:
            row["verdict"] = "no-store"
    return rows


def load_live(path):
    """table_va -> (set of classes, object count) from a live class-table read."""
    live = {}
    for r in csv.DictReader(open(path, newline="")):
        va = int(r["table_va"], 16)
        classes, objects = live.get(va, (set(), 0))
        classes.add(r["class"])
        live[va] = (classes, objects + int(r.get("objects") or 0))
    return live


def load_tables(path):
    """table_va -> class name from `inventory/xbe_class_table_names.csv`."""
    out = {}
    for r in csv.DictReader(open(path, newline="")):
        va = r.get("table_va") or ""
        if not va:
            continue
        try:
            out[int(va, 16)] = r.get("class", "")
        except ValueError:
            continue
    return out


def load_sites(path):
    out = []
    for r in csv.DictReader(open(path, newline="")):
        if not r.get("site_va"):
            continue
        out.append({"site_va": int(r["site_va"], 16),
                    "class": r.get("class", ""),
                    "class_table": r.get("class_table", "")})
    return out


FIELDS = ["site_va", "class", "class_table", "code_pushes", "ctor_candidates", "ctor_evidence",
          "ctor_pointer", "ctor", "ctor_size", "stored_table", "stored_stores", "ctor_class",
          "agrees", "sites_sharing", "live_classes", "live_objects", "verdict"]


class Code:
    """A stand-in image for `--check`: code at one base, data at another, no real sections."""

    def __init__(self, base, code, data=None):
        self.base, self.code = base, code
        self.data = data
        self.data_base = base + 0x1000

    def read(self, va, n=200):
        if self.base <= va < self.base + len(self.code):
            off = va - self.base
            return self.code[off:off + n]
        raise KeyError(hex(va))

    def section_for(self, va, executable=None):
        if self.base <= va < self.base + len(self.code):
            return Section(".text", self.base)
        if self.data is not None and self.data_base <= va < self.data_base + self.data:
            return Section(".rdata", self.data_base)
        return None


class Section:
    def __init__(self, name, va):
        self.name, self.va = name, va


def check():
    base = 0x4000
    code = bytearray()
    pad_n = lambda n: code.extend(b"\x90" * max(0, n))
    here = lambda: base + len(code)

    pad_n(0x40)
    shared = here()                                            # the shared 9-byte body
    code += b"\x8B\xC1" + b"\xC7\x00" + struct.pack("<I", 0x004DD2A8) + b"\xC3"
    thunk = here()                                             # the null-check forwarder
    code += NULLCHECK + b"\x05" + b"\xE9" + struct.pack("<i", shared - (thunk + 13)) + b"\xC3\xCC\xCC"
    own = here()                                               # a per-class body
    code += b"\x8B\xC1" + b"\xC7\x00" + struct.pack("<I", 0x004D00AA) + b"\xC3"
    spare = here()                                             # a second known function start
    code += b"\x8B\xC1" + b"\xC7\x00" + struct.pack("<I", 0x004D00BB) + b"\xC3"

    def site(*pushes):
        """A registration site: each `push imm32` (plus filler), the call 8 bytes after the last."""
        pad_n(0x200 - len(code) % 0x200)
        for p in pushes:
            code.extend(b"\x68" + struct.pack("<I", p) + b"\x90\x90\x90\x90")
        return here() + 8

    # a data pointer first, then the thunk: the data pointer must lose
    site_a = site(base + 0x1000, thunk)
    site_b = site(own)                                         # nearest push is the per-class body
    site_c = site(own, spare)                                  # two function starts in one window
    image = Code(base, bytes(code), data=0x400)
    functions = {shared: "ctor_shared", own: "ctor_own", spare: "ctor_spare"}

    body = image.read(shared, BODY)
    assert stored_immediates(body)[0] == (2, 0x004DD2A8), stored_immediates(body)[:3]
    assert body_size(body) == 9, body_size(body)
    assert resolve(image, thunk) == shared, hex(resolve(image, thunk))
    assert resolve(image, shared) == shared

    # THE BUG: a .rdata pointer pushed as a literal must not become the site's constructor.
    pushes = ctor_pushes(image, site_a, functions, ".text")
    assert [p[1] for p in pushes] == [thunk], pushes
    assert pushes[0][2] == shared and pushes[0][3] == "function-start", pushes

    # No known function start in the window: fall back to the code section, never to data.
    pushes = ctor_pushes(image, site_a, {}, ".text")
    assert [p[1] for p in pushes] == [thunk], pushes
    assert pushes[0][3] == "weak-code", pushes

    rows = analyse(image, [{"site_va": site_a, "class": "Engine", "class_table": ""},
                           {"site_va": site_a + 1, "class": "Player", "class_table": ""}],
                   functions, live={0x004DD2A8: ({"GameMenu", "VoiceSet"}, 550)})
    for row in rows:
        assert row["ctor"] == "0x%08x" % shared, row
        assert row["stored_table"] == "0x004dd2a8", row
        assert row["sites_sharing"] == 2, row
        assert row["verdict"] == "shared-default", row
        assert row["live_classes"] == 2 and row["live_objects"] == 550, row

    alone = analyse(image, [{"site_va": site_b, "class": "FireTexture", "class_table": ""}],
                    functions)
    assert alone[0]["stored_table"] == "0x004d00aa", alone[0]
    assert alone[0]["verdict"] == "per-class", alone[0]

    both = analyse(image, [{"site_va": site_c, "class": "Texture", "class_table": ""}],
                   functions)
    assert both[0]["ctor_candidates"] == 2, both[0]
    assert both[0]["verdict"] == "ambiguous-ctor", both[0]

    # The window can catch the *next* registration's push: a ctor that installs another class's table.
    neigh = analyse(image, [{"site_va": site_b, "class": "IceTexture", "class_table": ""}],
                    functions, tables={0x004D00AA: "FireTexture"})
    assert neigh[0]["ctor_class"] == "FireTexture", neigh[0]
    assert neigh[0]["agrees"] == "no", neigh[0]
    assert neigh[0]["verdict"] == "neighbour-window", neigh[0]

    same = analyse(image, [{"site_va": site_b, "class": "FireTexture", "class_table": ""}],
                   functions, tables={0x004D00AA: "FireTexture"})
    assert same[0]["agrees"] == "yes" and same[0]["verdict"] == "per-class", same[0]

    # A one-dword table that object memory pairs with many classes is not an identity either.
    wide = analyse(image, [{"site_va": site_b, "class": "", "class_table": ""}], functions,
                   live={0x004D00AA: ({"CarpetSurface", "DirtSurface", "FabricSurface"}, 3)})
    assert wide[0]["verdict"] == "shared-table", wide[0]
    print("ok")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("--sites", default="inventory/registration_sites.csv")
    ap.add_argument("--functions", default="symbols/xbox_functions.csv")
    ap.add_argument("--live", default="")
    ap.add_argument("--tables", default="")
    ap.add_argument("-o", "--out", default="inventory/registration_ctor_arguments.csv")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.xbe:
        ap.error("xbe is required unless --check")

    import xbe
    image = xbe.Xbe(args.xbe)
    sites = load_sites(args.sites)
    functions = load_functions(args.functions)
    live = load_live(args.live) if args.live else None
    tables = load_tables(args.tables) if args.tables else None
    rows = analyse(image, sites, functions, live, tables)
    if args.out:
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    counts = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print(f"{len(rows)} sites, {len(functions)} known function starts: "
          + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    unnamed = [r for r in rows if not r["class_table"]]
    if unnamed:
        un = {}
        for row in unnamed:
            un[row["verdict"]] = un.get(row["verdict"], 0) + 1
        print(f"  unnamed sites: {len(unnamed)}: "
              + ", ".join(f"{k} {v}" for k, v in sorted(un.items())))
    shared = {}
    for row in rows:
        if isinstance(row["sites_sharing"], int) and row["sites_sharing"] > 1:
            shared.setdefault((row["ctor"], row["stored_table"]), set()).add(row["class"])
    for (ctor, table), classes in sorted(shared.items(), key=lambda kv: -len(kv[1])):
        names = sorted(c for c in classes if c)
        print(f"  {ctor} stores {table}: {len(classes)} sites"
              + (f" ({', '.join(names[:4])}{' ...' if len(names) > 4 else ''})" if names else
                 " (all unnamed)"))
    if args.out:
        print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
