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
  * **The table a body installs is its *last* store, not its first.** MSVC writes the base constructor's
    vtable itself when it inlines that constructor into a derived body, so a derived constructor stores
    the base's table first and its own last: FireTexture's `0x002e81f0` stores `0x004cfc88` and then
    `0x004cfd78`, IceTexture's `0x002e8370` stores `0x004cfc88` and then `0x004d0228`. Reading the first
    store gave both classes to FractalTexture's table and left `0x004cfd78`/`0x004d0228` with no
    registration at all.
  * **The read is bounded by the body, and here the bound does move rows.** A flat 400-byte read walks past
    a short constructor's own `ret` into the next function, which may store its own table, and the store is
    then attributed to the wrong body. The read stops at the size `symbols/xbox_functions.csv` gives the
    function, and at the body's own `int3` padding where the map has no size -- TextBuffer's target
    `0x00075820` is a 19-byte stub whose flat window reads the two stores of the body after it.
  * **A guarded stub can forward with `call`, and then the call's target is the constructor.** `0x00075820`
    is `mov ecx,[esp+4]; test ecx,ecx; je; push L""; call 0x000669e0; ret`, and `0x000669e0` installs
    TextBuffer's table. A body that opens with the thunk prefix, installs no table of its own, and ends in
    a direct call is followed one hop -- never more.

So the name comes from one of these joins, in this order, and the `name_rule` column says which:

  * `unshared-thunk`   -- exactly one site on this table uses a thunk no other site uses; that thunk's
                          constructor installs the table for this class and no other.
  * `hierarchy-ancestor` -- every site shares a thunk, so no site owns the table. Among the literals
                          that `inventory/classes.csv` knows, exactly one is an ancestor-or-self of
                          every other: that one is the class whose constructor the others reuse
                          (`Object` for the object root, `Texture` for the texture family).
  * `ancestor-derived` -- no literal in the group is a common ancestor, but the group has one nearest
                          common ancestor (`FireTexture`/`IceTexture` -> `FractalTexture`). The table
                          is named for the ancestor, and the row says the name is derived.
  * `base-store-ancestor` -- no registration names the table at all, but two or more registered
                          classes store it *first* in their own constructor bodies, and their nearest
                          common ancestor is the class whose constructor they inlined. One owner is
                          not evidence (a class's nearest ancestor is itself), so a single owner
                          leaves the table unnamed.
  * `unresolved`       -- no common ancestor either; the row keeps an empty class and lists its
                          candidates, rather than silently taking one.
  * `tree-paired`      -- no site resolves to this table at all, and the class-to-table pairing
                          measured in `inventory/class_table_name_pairs.csv` does: the row's `paired`
                          verdict names the class those children's script supers describe. The name is
                          the tree's, not a store's, and the row says so.
  * `child-super`      -- no site, no store and no tree names the table, but the classes that call its
                          installer all say their own parent is the same class: a class's table is
                          installed by its constructor and its children call that constructor, so the
                          table is the one class those children descend from. The vote is
                          `inventory/class_table_super_names.csv` (`tools/class-super-edges.py` reads the
                          class object's `+0x28` store); a table whose callers disagree is a class they
                          share higher up, not their parent, and it is left unnamed.
  * `slot-file-token`  -- no site, no store and no tree names the table, but the class it belongs to is a
                          literal the build ships, and the table's own *slots* say which one: every slot
                          body's assert inlines a source path, so a table's slots vote for a `.cpp` file,
                          and four files are the base of a family the census already resolved
                          (`UnNetDrv` -> `NetDriver`, `UnConn` -> `NetConnection`, `UnChan` -> `Channel`,
                          `UnCamera` -> `Viewport`). The file-to-class step is the one thing the image does
                          not state (`SLOT_FILE_CLASS` below), so the name is refused unless the
                          registration census shows that literal at a site that installs no table of its
                          own, the table carries the file, every *named* table carrying it is at least as
                          long (a derived class's table extends its base's), and no shorter unnamed table
                          carries it -- that one would be the better head and blocks the name.
  * `no-registration`  -- no site resolves to this table at all.

    tools/class-table-names.py DUMP/default.xbe -o inventory
    tools/class-table-names.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-table-names.py --check-doc docs/systems/CLASS_REGISTRATION.md   # from the committed CSV
    tools/class-table-names.py --check        # synthetic bodies, no dump, writes nothing

The decode is objdump over each enclosing body, so it needs no capstone (the earlier version imported
it, and its artifact therefore could not be regenerated on a host without the package). Inputs are the
image, `inventory/xbe_class_tables.csv`, `inventory/classes.csv`, `symbols/xbox_functions.csv`, the
paired-name census and `inventory/class_slot_tokens.csv`'s slot tallies.
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
          "constructor_va", "installed_at", "stores", "bound", "evidence", "sites", "name_rule",
          "site_literals"]
ARTIFACT = os.path.join(ROOT, "inventory/xbe_class_table_names.csv")
DOC = os.path.join(ROOT, "docs/systems/CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-table-names -->"
DOC_END = "<!-- /generated:class-table-names -->"
TABLES = os.path.join(ROOT, "inventory/xbe_class_tables.csv")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
SYMBOLS = os.path.join(ROOT, "symbols/xbox_functions.csv")
PAIRS = os.path.join(ROOT, "inventory/class_table_name_pairs.csv")
TOKENS = os.path.join(ROOT, "inventory/class_slot_tokens.csv")
SUPER_NAMES = os.path.join(ROOT, "inventory/class_table_super_names.csv")
REGISTRATIONS = os.path.join(ROOT, "inventory/class_registrations.csv")
# The one literal in this census: the engine's class for a source file. The image names every class below
# (`inventory/class_registrations.csv` carries the site that pushes it), and it names no class for these
# four files, so the file-to-class step is stated here and argued in docs/systems/CLASS_REGISTRATION.md,
# where the slot vote and the ladder that corroborate each entry are the evidence. A token not listed here
# names nothing, and so does a listed class the registration census does not show at a shared-thunk site.
SLOT_FILE_CLASS = {
    "UnNetDrv": "NetDriver",
    "UnConn": "NetConnection",
    "UnChan": "Channel",
    "UnCamera": "Viewport",
}
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

    def padding_end(self, va, cap=1200):
        """The body's own alignment pad: the offset of the first run of two or more `int3`.

        MSVC pads a function's end to a 16-byte boundary with `0xcc`, and a *run* of two is the mark a
        reader can trust -- a single `0xcc` can be a jump table's filler inside a body. None when the
        body runs to the cap with no pad.
        """
        b = self.read(va, cap)
        if not b:
            return None
        for i in range(len(b) - 1):
            if b[i] == 0xCC and b[i + 1] == 0xCC:
                return i
        return None

    def body_size(self, va):
        """(limit, how) for one body: the symbol map's size, else its own int3 pad, else the window."""
        size = self.size_of(va)
        if size:
            return size, "symbol"
        pad = self.padding_end(va)
        if pad:
            return pad, "padding"
        return WINDOW, "window"

    def installed_tables(self, va, limit):
        """Every `mov [reg], <known table>` in the body, in address order."""
        b = self.read(va, limit)
        if not b:
            return []
        out = []
        for i in range(len(b) - 6):
            if b[i] != 0xC7:                  # mov r/m32, imm32
                continue
            modrm = b[i + 1]
            if (modrm >> 6) or ((modrm >> 3) & 7):
                continue
            imm = struct.unpack_from("<I", b, i + 2)[0]
            if imm in self.tables:
                out.append((imm, va + i))
        return out

    def call_target(self, va, limit):
        """The last direct `call` in the body, as a target: a guarded stub's real constructor."""
        for _sa, text in reversed(self.disasm(va, limit)):
            m = re.match(r"call\s+0x([0-9a-f]+)", text)
            if m:
                return int(m.group(1), 16)
        return None

    def tables_of(self, va):
        """(tables, own store, how the read was bounded, stub followed) for one constructor body.

        The body's own table is its **last** store. A body that installs none and opens with the thunk
        prefix is a guarded stub; when it forwards with `call` rather than `jmp`, the tables are the
        call's target's, read one hop only.
        """
        limit, how = self.body_size(va)
        got = self.installed_tables(va, limit)
        if got:
            return got, got[-1], how, None
        head = self.read(va, 4)
        if head and head[:4] == THUNK_PREFIX:
            tgt = self.call_target(va, limit)
            if tgt:
                limit2, _how2 = self.body_size(tgt)
                got2 = self.installed_tables(tgt, limit2)
                if got2:
                    return got2, got2[-1], how, tgt
        return [], None, how, None

    def installed_table(self, ctor_va, limit=None):
        """(table, store_va, bounded) for the body's own table, or None -- kept for one-off reads."""
        limit, how = (limit, "symbol") if limit else self.body_size(ctor_va)
        got = self.installed_tables(ctor_va, limit)
        if not got:
            return None
        return got[-1][0], got[-1][1], how

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
        table = thunk = target = store = None
        how, stores, stub, got = "", 0, None, []
        for value in reversed(pushes):
            if value in img.tables or not img.read(value, 4):
                continue
            for cand in (img.thunk_target(value), value):
                if not cand:
                    continue
                got, own, bnd, followed = img.tables_of(cand)
                if own:
                    table, thunk, target = own[0], value, cand
                    store, how, stores, stub = own[1], bnd, len(got), followed
                    break
            if table:
                break
        out.append({"site_va": site, "class_literal": literal, "object_size": obj,
                    "thunk_va": thunk, "thunk_target": target, "table_va": table,
                    "installed_at": store, "bound": how, "stores": stores, "stub": stub,
                    "table_stores": got})
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


def base_store_names(sites, chosen, hierarchy):
    """Tables no registration names, but that a registered class's body stores before its own.

    A derived constructor that inlines its base's constructor stores the base's table first and its
    own last, so the first store names an ancestor of the class the site registered. One owner is not
    evidence -- the ancestor of a class is the class itself -- so a name is claimed only when two or
    more owners share one nearest common ancestor that none of them is (FireTexture and IceTexture,
    both subclasses of FractalTexture, which is why `0x004cfc88` keeps its name).
    """
    group_of = {}
    for s in sites:
        own = s["table_va"]
        if not own or not s.get("table_stores"):
            continue
        owner = chosen.get(own, ("", "", None))[0]
        if not owner:
            continue
        for t, at in s["table_stores"]:
            if t != own:
                group_of.setdefault(t, []).append((owner, s, at))
    out = {}
    for t, group in group_of.items():
        if t in chosen:
            continue
        owners = sorted({o for o, _s, _a in group})
        derived = nearest_common(hierarchy, owners)
        if derived and derived not in owners:
            out[t] = (derived, owners, group[0][1], group[0][2])
    return out


def paired_names(path=PAIRS):
    """{table_va: (name, basis)} for the tables `class-table-name-pair.py` paired from the tree."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("verdict") != "paired" or not row.get("class"):
                continue
            key = int(row["table_va"], 16) if row.get("table_va") else None
            if key is not None:
                out[key] = (row["class"], row.get("support", ""))
    return out


def apply_pairs(rows, pairs=None):
    """Name the tables no store reaches from the tree pairing, and only those.

    A name the image states is never overwritten: the join fills a row only where `class` is empty, so
    the six rows this adds are the ones that read `no-registration` before. The evidence names the
    child-super vote, because that is the measurement, not the row.
    """
    pairs = paired_names() if pairs is None else pairs
    for row in rows:
        if row["class"]:
            continue
        pair = pairs.get(int(row["table_va"], 16))
        if not pair:
            continue
        name, basis = pair
        row.update({
            "class": name,
            "name_rule": "tree-paired",
            "evidence": ("no registration names this table: its class's site pushes the shared default "
                         "constructor, and %s, so the table is %s's" % (basis or "the tree pairs it", name)),
            "site_literals": name,
        })
    return rows


def super_names(path=SUPER_NAMES):
    """{table_va: (class, children, votes)} for the tables the callers' `Super` names.

    The artifact is `tools/class-super-edges.py`'s join: a table no registration names is named by the
    classes that call its installer, and each of those names is a *child* -- so the class the table
    belongs to is the one its children agree their parent is. Only the `names-the-table` verdicts are
    read; a `split`, `partial` or `no-child-super` row states that the vote did not decide, and a table
    whose callers were already naming it lands as `agrees`, which is this census's own answer written
    back and is never read here.
    """
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("verdict") != "names-the-table" or not row.get("class"):
                continue
            key = int(row["table_va"], 16) if row.get("table_va") else None
            if key is not None:
                out[key] = (row["class"], row.get("children", ""), row.get("child_supers", ""))
    return out


def apply_super_names(rows, names=None):
    """Name the tables the callers' `Super` agrees on, and only the rows still empty.

    A row that already carries a name is never overwritten: the image's own store, the tree and the
    slots' assert files are each a stronger statement than this hierarchy join, which fills only what
    none of them reached. The evidence names the children and the class they vote for, because that is
    the measurement, not the row.
    """
    names = super_names() if names is None else names
    for row in rows:
        if row["class"]:
            continue
        hit = names.get(int(row["table_va"], 16))
        if not hit:
            continue
        name, children, _votes = hit
        kids = [k for k in children.split("|") if k]
        shown = ", ".join(kids[:6]) + (" (+%d more)" % (len(kids) - 6) if len(kids) > 6 else "")
        row.update({
            "class": name,
            "name_rule": "child-super",
            "evidence": ("no registration names this table: %d calling class(es) (%s) each say their "
                         "parent is %s, so the table is the one that class's constructor installs"
                         % (len(kids), shown, name)),
            "site_literals": ", ".join(kids),
        })
    return rows


def slot_token_tallies(path=TOKENS):
    """Per table: its slot count, and how many slots inline each assert-file token.

    `inventory/class_slot_tokens.csv` (`tools/class-slot-tokens.py`) tallies the assert paths a table's
    slot bodies inline, over the *same* extent rule this census uses, so the two reads cannot disagree
    about which dwords are slots. Only the tallies are read -- never that artifact's `class` column,
    which is this census's own output written back, and reading it would make the rule depend on its
    previous answer.
    """
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("table_va"):
                continue
            tokens = {}
            for item in (row.get("tokens") or "").split():
                name, _, count = item.rpartition(":")
                if not name:
                    continue
                try:
                    tokens[name] = int(count)
                except ValueError:
                    continue
            out[int(row["table_va"], 16)] = (int(row.get("slots") or 0), tokens)
    return out


def literal_sites(path=REGISTRATIONS):
    """class name -> (site_va, object_size, verdict) for every class literal the image ships.

    A class whose site pushes the shared default constructor installs no table of its own, so the store
    its site reaches is the object root's -- and that is the population this rule needs: the build names
    the class, and the class has no table a site can point at. Four of the literals are the base of a
    family the naming census already resolved (`NetDriver` at 0x00183599, 4524 bytes, `shared-thunk`),
    which is what lets a name the build states be attached to a table no site names.
    """
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("class_literal") or "").strip()
            if not name or not row.get("site_va"):
                continue
            try:
                size = int(row.get("object_size") or 0)
            except ValueError:
                size = 0
            cand = (int(row["site_va"], 16), size, (row.get("verdict") or "").strip())
            prev = out.get(name)
            if prev is None or cand[0] < prev[0]:
                out[name] = cand
    return out


def apply_slot_tokens(rows, tallies=None, mapping=None, sites=None):
    """Name a family's base from the file its slots assert in, where the image corroborates the name.

    The class for a file is the one thing here the image does not state (`SLOT_FILE_CLASS`), so the rule
    asks the image everything else, and refuses the name unless all of it holds: the mapped class must be
    a literal some registration site pushes, that site must install no table of its own (a shared-thunk
    site, so the class is exactly one the registration path cannot name), the table must carry the mapped
    token, at least one *named* table carrying the same token must be at least as long as it, and no
    shorter *unnamed* table carrying the token may exist -- that one would be the better head. A row only
    ever moves from empty to named: a name the image states is never overwritten.
    """
    tallies = slot_token_tallies() if tallies is None else tallies
    mapping = SLOT_FILE_CLASS if mapping is None else mapping
    sites = literal_sites() if sites is None else sites
    named = {int(r["table_va"], 16): r.get("class") for r in rows}
    family = {}
    for va, (slots, tokens) in tallies.items():
        for token in tokens:
            family.setdefault(token, []).append((va, slots))
    for row in rows:
        if row["class"]:
            continue
        va = int(row["table_va"], 16)
        slots, tokens = tallies.get(va, (0, {}))
        if not tokens:
            continue
        hits = []
        for token in sorted(tokens):
            name = mapping.get(token)
            if not name:
                continue
            site = sites.get(name)
            if not site or site[2] != "shared-thunk":
                continue            # the build does not name that class, or names a table for it
            kin = family.get(token, [])
            carriers = sorted((s, v) for v, s in kin
                              if v != va and named.get(v))
            shorter = [v for v, s in kin if v != va and s < slots and not named.get(v)]
            if not carriers or shorter:
                continue
            # a second ladder, on the object size: a base's object is no larger than every derived one
            sized = [(sites[named[v]][1], named[v]) for s, v in carriers
                     if named.get(v) in sites and sites[named[v]][1]]
            if any(size < site[1] for size, _ in sized):
                continue
            hits.append((token, name, carriers, site, sized))
        if len(hits) != 1:
            continue
        token, name, carriers, site, sized = hits[0]
        row.update({
            "class": name,
            "name_rule": "slot-file-token",
            # the class's own site -- its literal and the object size it pushes. There is no store to
            # record: the site pushes the shared default constructor, so `installed_at`/`stores`/`bound`
            # stay empty and the table this row names is the ladder's, not the site's.
            "object_size": str(site[1]) if site[1] else "",
            "registration_va": "0x%08x" % site[0],
            "evidence": ("no registration names this table; its class is the literal at 0x%08x (%d "
                         "bytes, pushes the shared default constructor, so that site installs the object "
                         "root's table), %d of this table's %d slots inline a %s assert path, and the %d "
                         "named table(s) carrying that file are at least as long (%s)%s"
                         % (site[0], site[1], tokens[token], slots, token, len(carriers),
                            ", ".join("%s %d" % (named[v], s) for s, v in carriers),
                            (" and no smaller object (%s)"
                             % ", ".join("%s %d" % (n, z) for z, n in sized)) if sized else "")),
            "site_literals": ", ".join(["%s@0x%08x" % (name, site[0])] +
                                       ["%s %d" % (named[v], s) for s, v in carriers]),
        })
    return rows


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
    chosen = {t: choose_name(g, per_thunk, hierarchy) for t, g in per_table.items()}
    base = base_store_names(sites, chosen, hierarchy)

    rows = []
    for table in sorted(tables):
        group = per_table.get(table, [])
        row = {"table_va": "0x%08x" % table, "class": "", "object_size": "", "registration_va": "",
               "constructor_thunk_va": "", "constructor_va": "", "installed_at": "", "stores": "",
               "bound": "", "evidence": "no registration names this table", "sites": "0",
               "name_rule": "no-registration", "site_literals": ""}
        if group:
            name, rule, site = chosen[table]
            row.update({
                "class": name or "",
                "object_size": "" if site["object_size"] is None else str(site["object_size"]),
                "registration_va": "0x%08x" % site["site_va"],
                "constructor_thunk_va": ("0x%08x" % site["thunk_va"]
                                         if site["thunk_va"] is not None else ""),
                "constructor_va": ("0x%08x" % site["thunk_target"]
                                   if site["thunk_target"] is not None else ""),
                "installed_at": ("0x%08x" % site["installed_at"]
                                 if site["installed_at"] is not None else ""),
                "stores": str(site["stores"]),
                "bound": site["bound"],
                "evidence": evidence(rule, site, len(group), name),
                "sites": str(len(group)),
                "name_rule": rule,
                "site_literals": ", ".join(s["class_literal"] or "?" for s in
                                           sorted(group, key=lambda s: s["site_va"])),
            })
        elif table in base:
            name, owners, site, at = base[table]
            # the derived name is a class the registration does know: its own site carries the size
            home = next((x for x in sites if x["class_literal"] == name), site)
            row.update({
                "class": name,
                "object_size": "" if home["object_size"] is None else str(home["object_size"]),
                "registration_va": "0x%08x" % home["site_va"],
                "constructor_va": ("0x%08x" % site["thunk_target"]
                                   if site["thunk_target"] is not None else ""),
                "installed_at": "0x%08x" % at,
                "stores": str(len(site.get("table_stores") or [])),
                "bound": site["bound"],
                "evidence": ("no registration names this table; %d classes (%s) store it before their "
                             "own table, and %s is their nearest common ancestor"
                             % (len(owners), ", ".join(owners), name)),
                "sites": "0",
                "name_rule": "base-store-ancestor",
                "site_literals": ", ".join(owners),
            })
        rows.append(row)
    return apply_slot_tokens(apply_super_names(apply_pairs(rows)))


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
    two = [r for r in rows if r.get("stores") not in ("", "0", "1", None)]
    padded = [r for r in rows if r.get("bound") == "padding"]
    lines.append("")
    lines.append("**A table is named from its constructor body's last store.** %d table(s) are named from "
                 "a body that installs two class tables -- the first store there is a base class's table, "
                 "written by the base constructor the compiler inlined -- and %d row(s) are read without a "
                 "symbol size, bounded at the body's own `int3` padding: %s."
                 % (len(two), len(padded),
                    ", ".join("`%s` (%s)" % (r["table_va"], r["site_literals"]) for r in
                              sorted(padded, key=lambda r: r["table_va"])) or "none"))
    lines.append("")
    lines.append("%d tables: %d named (%d by an unshared constructor thunk, %d by the hierarchy a "
                 "shared thunk forces, %d derived from a nearest common ancestor, %d from a base store "
                 "no registration names, %d from the tree the child supers describe, %d from the "
                 "children's `Super` the class object writes, %d from the assert "
                 "file their slots inline), %d unresolved, "
                 "%d with no registration. %d tables are reached by more than one registration site, "
                 "so a write-order rule would name them by whichever site happened to be written last."
                 % (len(rows), named, rules.get("unshared-thunk", 0),
                    rules.get("hierarchy-ancestor", 0), rules.get("ancestor-derived", 0),
                    rules.get("base-store-ancestor", 0), rules.get("tree-paired", 0),
                    rules.get("child-super", 0), rules.get("slot-file-token", 0),
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
        # a guarded stub that forwards with `call` instead of `jmp` (TextBuffer's shape at 0x00075820)
        stub = (b"\x8b\x4c\x24\x04\x85\xc9\x74\x0a\xe8"
                + struct.pack("<i", 0x4500 - (0x2200 + 13)) + b"\xc3")
        root = b"\x8b\xc1\xc7\x00\x00\x50\x00\x00\xc3"  # mov eax,ecx; mov [eax],0x5000; ret
        short = b"\x8b\xc1\xc3"                         # mov eax,ecx; ret  -- stores nothing
        later = b"\x8b\xc1\xc7\x00\x00\x50\x00\x00\xc3"  # a neighbour body that stores 0x5000
        two = (b"\x8b\xc1"                              # mov eax,ecx
               b"\xc7\x00\x00\x51\x00\x00"              # mov [eax],0x5100  -- the base's table
               b"\xc7\x00\x08\x51\x00\x00"              # mov [eax],0x5108  -- its own
               b"\xc3")
        padded = b"\x8b\xc1\xc7\x00\x00\x50\x00\x00\xc3"      # 9 bytes, no symbol size
        neighbour = b"\x8b\xc1\xc7\x00\x10\x51\x00\x00\xc3"   # 0x4520: stores 0x5110
        self.data = bytearray(b"\xcc" * 0x8000)
        self.data[0:len(body)] = body
        self.data[0x1000:0x1000 + len(thunk)] = thunk       # VA 0x2000
        self.data[0x1200:0x1200 + len(stub)] = stub         # VA 0x2200
        self.data[0x3000:0x3000 + len(root)] = root         # VA 0x4000
        self.data[0x3100:0x3100 + len(short)] = short       # VA 0x4100
        self.data[0x3120:0x3120 + len(later)] = later       # VA 0x4120
        self.data[0x3400:0x3400 + len(two)] = two           # VA 0x4400
        self.data[0x3500:0x3500 + len(padded)] = padded     # VA 0x4500
        self.data[0x3520:0x3520 + len(neighbour)] = neighbour  # VA 0x4520
        self.data[0x5000:0x5000 + 6] = "Root".encode("utf-16-le") + b"\x00\x00"
        self.data[0x5008:0x5008 + 6] = "Leaf".encode("utf-16-le") + b"\x00\x00"
        self.data[0x5010:0x5010 + 6] = "Own".encode("utf-16-le") + b"\x00\x00"
        self.tables = {0x5000, 0x5100, 0x5108, 0x5110}
        self.text = type("S", (), {"va": 0x1000, "vsize": 0x8000, "foff": 0, "fsize": 0x8000})()
        self.tb = bytes(self.data)
        self.size_of = lambda va: {0x4000: 9, 0x4100: 3, 0x4400: len(two)}.get(va)

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
    got, own, how, stub = img.tables_of(0x4400)
    if (len(got), own[0], how, stub) != (2, 0x5108, "symbol", None):
        bad.append("the body's own table is not its last store: %r" % ((got, own, how, stub),))
    got, own, how, stub = img.tables_of(0x4500)
    if ([g[0] for g in got], how) != ([0x5000], "padding"):
        bad.append("the int3 bound did not cut the body: %r" % ((got, how),))
    got, own, how, stub = img.tables_of(0x2200)
    if ([g[0] for g in got], stub) != ([0x5000], 0x4500):
        bad.append("the guarded stub was not followed through its call: %r" % ((got, stub),))
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

    # a table no registration names, stored first by two derived bodies: the nearest common ancestor
    deep = {"Root": "", "Fractal": "Root", "Fire": "Fractal", "Ice": "Fractal"}
    fire = {"site_va": 0x10, "class_literal": "Fire", "table_va": 0x5000, "thunk_target": 0x4000,
            "bound": "symbol", "table_stores": [(0x5010, 0x4002), (0x5000, 0x4008)]}
    ice = {"site_va": 0x20, "class_literal": "Ice", "table_va": 0x5008, "thunk_target": 0x4100,
           "bound": "symbol", "table_stores": [(0x5010, 0x4102), (0x5008, 0x4108)]}
    picked = {0x5000: ("Fire", "unshared-thunk", fire), 0x5008: ("Ice", "unshared-thunk", ice)}
    got = base_store_names([fire, ice], picked, deep)
    if got.get(0x5010, ("",))[0] != "Fractal":
        bad.append("base-store ancestor: %r" % (got,))
    if base_store_names([fire], picked, deep):
        bad.append("one owner was enough to name a base store")

    # the tree pairing names only an empty row, and the row says where the name came from
    rows = [{"table_va": "0x00005000", "class": "", "sites": "0", "name_rule": "no-registration",
             "site_literals": "", "evidence": "no registration names this table"},
            {"table_va": "0x00006000", "class": "Image", "sites": "1", "name_rule": "unshared-thunk",
             "site_literals": "Image", "evidence": "x"}]
    apply_pairs(rows, {0x5000: ("Tree", "3 child table(s) declare super Tree"),
                       0x6000: ("Other", "3 child table(s) declare super Other")})
    if (rows[0]["class"], rows[0]["name_rule"]) != ("Tree", "tree-paired"):
        bad.append("the tree did not name an empty row: %r" % (rows[0],))
    if "3 child table(s) declare super Tree" not in rows[0]["evidence"]:
        bad.append("the tree row's evidence does not carry the vote: %r" % (rows[0]["evidence"],))
    if rows[1]["class"] != "Image":
        bad.append("the tree overwrote a name the image states: %r" % (rows[1],))

    # the slots' assert file names a family's base -- and the guards hold
    def tok_row(va, cls=""):
        return {"table_va": "0x%08x" % va, "class": cls, "sites": "0", "name_rule": "no-registration",
                "site_literals": "", "evidence": "no registration names this table"}
    fam_site = {"Fam": (0x1800, 64, "shared-thunk")}
    fam = {0x5000: (8, {"UnFam": 2}), 0x5008: (8, {"UnFam": 2}), 0x5010: (9, {"UnFam": 1})}
    rows = [tok_row(0x5000), tok_row(0x5008, "Leaf")]
    apply_slot_tokens(rows, fam, {"UnFam": "Fam"}, fam_site)
    if (rows[0]["class"], rows[0]["name_rule"]) != ("Fam", "slot-file-token"):
        bad.append("slot-file-token did not name the base: %r" % (rows[0],))
    if "Leaf 8" not in rows[0]["evidence"] or "0x00001800" not in rows[0]["evidence"]:
        bad.append("slot-file-token evidence does not carry the ladder and the literal's site: %r"
                   % (rows[0]["evidence"],))
    # a shorter unnamed table carrying the same file is the better head, and the only one named
    fam = {0x5000: (8, {"UnFam": 2}), 0x5004: (6, {"UnFam": 1}), 0x5010: (9, {"UnFam": 1})}
    rows = [tok_row(0x5000), tok_row(0x5004), tok_row(0x5010, "Leaf")]
    apply_slot_tokens(rows, fam, {"UnFam": "Fam"}, fam_site)
    if [r["class"] for r in rows] != ["", "Fam", "Leaf"]:
        bad.append("slot-file-token named the head over a shorter unnamed carrier: %r"
                   % ([r["class"] for r in rows],))
    # a class the registration census does not ship is refused, and so is one whose own site names a table
    for site, why in (({}, "an unshipped class"), ({"Fam": (0x1800, 64, "names-own-table")},
                                                   "a class whose site installs its own table")):
        rows = [tok_row(0x5000), tok_row(0x5008, "Leaf")]
        apply_slot_tokens(rows, {0x5000: (8, {"UnFam": 2}), 0x5008: (8, {"UnFam": 2})},
                          {"UnFam": "Fam"}, site)
        if rows[0]["class"]:
            bad.append("slot-file-token named a table from %s: %r" % (why, rows[0]))
    # a token the map does not carry names nothing, and no named carrier means no family
    rows = [tok_row(0x5000), tok_row(0x5008, "Leaf")]
    apply_slot_tokens(rows, {0x5000: (8, {"UnOther": 2}), 0x5008: (8, {"UnOther": 2})},
                      {"UnFam": "Fam"}, fam_site)
    if rows[0]["class"]:
        bad.append("slot-file-token named a table from an unmapped token: %r" % (rows[0],))
    rows = [tok_row(0x5000)]
    apply_slot_tokens(rows, {0x5000: (8, {"UnFam": 2})}, {"UnFam": "Fam"}, fam_site)
    if rows[0]["class"]:
        bad.append("slot-file-token named a table with no named carrier: %r" % (rows[0],))

    # the children's `Super` names a table no store reaches -- and neither a named row nor a split moves
    rows = [tok_row(0x5000), tok_row(0x5008, "Kid"), tok_row(0x5010)]
    apply_super_names(rows, {0x5000: ("Super", "Child|Kid", "Super|Super"),
                             0x5010: ("", "Aaa|Bbb", "Xxx|Yyy")})
    if (rows[0]["class"], rows[0]["name_rule"]) != ("Super", "child-super"):
        bad.append("child-super did not name the empty row: %r" % (rows[0],))
    if rows[1]["class"] != "Kid":
        bad.append("child-super overwrote a name the image states: %r" % (rows[1],))
    if rows[2]["class"]:
        bad.append("child-super named a table its callers disagree on: %r" % (rows[2],))

    rows = [{"table_va": "0x00005000", "class": "Root", "object_size": "8",
             "registration_va": "0x00000010", "constructor_thunk_va": "0x00002000",
             "constructor_va": "0x00004000", "installed_at": "0x00004002",
             "stores": "2", "bound": "symbol",
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
          "a body's own table is its last store, a guarded stub is followed through its call, a "
          "short body does not inherit its neighbour store, and a slot's assert file names only a "
          "family's shortest unnamed head")
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
