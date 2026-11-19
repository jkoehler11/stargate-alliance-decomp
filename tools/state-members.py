#!/usr/bin/env python3
"""What the image itself names in a state: the frame's members, the record's field order, and the `BadToken` sites.

`docs/systems/STATE_NATIVES.md` recorded six field offsets as *shapes* ("the state frame", "a state's name", "the
WORD label offset") because the engine headers are not in the image. Four of those names are in the image after all:
`Core_UnObj` asserts on a script frame's code pointer with an expression that *spells the members* --

    StateFrame->Code>=&StateFrame->Node->Script(0) && StateFrame->Code<&StateFrame->Node->Script(StateFrame->Node->Script.Num())

and the same body is what loads them, so the assertion's own def-use chain names the displacements: the pointer read
from `[this+0xc]` is `StateFrame`; `[frame+0x4]` is `Node`; `[frame+0xc]` is `Code`; and `Script`'s data and count are
`[Node+0x48]`/`[Node+0x4c]`.  This tool reads that chain instead of restating it: it finds the expression string, the
site that pushes it, the body that carries the site, and the last write of each register the comparison uses -- so a
row's evidence is an instruction, and a body that stops matching the shape fails instead of keeping a stale name.

Two more questions the same read answers, and one it corrects:

* **The 22-byte `UState` record's field order.** `UState::Serialize` (`0x394c0`) hands four field pointers to four
  size-fixed writers (`0x371d0` 8, `0x37080` 2, `0x370e0` 4, `0x37110` 4).  The writer call is *cdecl with two
  stack arguments*, so the **last** push is the archive and the **second-to-last** is the data -- and the pointer a
  caller leaves on the stack for the next call is written *second*.  Reading the pushes in that order, the record is
  `+0x80` 8, `+0x88` 8, **`+0x94` 2**, **`+0x90` 4** -- which is what `label_lookup` reads at `+0x94` (`mov ax,[esi+0x94]`,
  `cmp ax,0xffff`) and what `state_set` reads at `+0x90` (`mov cl,[eax+0x90]`, `test cl,0x2`).  `docs/NATIVE_VM.md`'s
  table had the last two swapped.
* **The `0x2b2` "sentinel" is a name, and the image says which one.** Every instruction in `.text` that carries
  `0x2b2` as an immediate is either a `cmp` (four: two in `execGotoState`, two in the state setter) or the reserve's
  own index store for that slot -- and `inventory/gname_seed.csv` files that slot as **690 `Auto`**.  So the index the
  setter recognises is the `Auto` state's name, not a `BadToken` the name table cannot resolve, and the join is
  asserted rather than stated: the store site in this census must be the seed row's own `store` column.  (This
  corrects `docs/UNKNOWNS.md` U26, which read the slot as a hole.)

    python3 tools/state-members.py --xbe DUMP/default.xbe -o inventory/state_members.csv \\
        --write-doc docs/systems/STATE_NATIVES.md
    python3 tools/state-members.py --check-doc docs/systems/STATE_NATIVES.md   # no dump, no write
    python3 tools/state-members.py --check                                   # no dump, no write
    python3 tools/state-members.py --xbe DUMP/default.xbe --verify           # re-read every row

Nothing here publishes a decompiled body: a row is a member name the image prints, a displacement, and the
instruction that carries one or the other.
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
CSV_PATH = os.path.join(ROOT, "inventory", "state_members.csv")
DOC_PATH = os.path.join(ROOT, "docs", "systems", "STATE_NATIVES.md")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
MARK_BEGIN = "<!-- generated:state-members -->"
MARK_END = "<!-- /generated:state-members -->"

FIELDS = ["kind", "owner", "offset", "size", "name", "basis", "site", "evidence"]

# The expression the assert prints.  It is the only string in the image that spells these members.
EXPR = "StateFrame->Code>=&StateFrame->Node->Script(0)"
ASSERT_PRINTER = 0x58A30

# `UState::Serialize` and the size-fixed writers it calls: `{va: bytes}`.
SERIALIZE = 0x394C0
WRITERS = {0x371D0: 8, 0x37080: 2, 0x370E0: 4, 0x37110: 4}
RECORD = 22

# The index `execGotoState` compares its evaluated argument against to pick the `auto` state.  A census, not a claim:
# every immediate site in `.text` is a row, and the reserve's own store for the slot is joined to `gname_seed.csv`.
SENTINEL = 0x2B2
SEED_CSV = os.path.join(ROOT, "inventory", "gname_seed.csv")

# Members read by a fixed instruction.  `needle` is the decoded instruction the claim is read at; a body that no
# longer decodes to it fails the read rather than keeping the row.
FIXED = [
    ("UState", 0x90, 4, "Flags", "0x48f14",
     "mov cl,BYTE PTR [eax+0x90]", "the state setter reads the flag byte: `test cl,0x2` marks the `auto` state"),
    ("UState", 0x80, 8, "ProbeMask", "0x4900d",
     "mov edx,DWORD PTR [eax+0x80]", "the state setter recomputes the frame's probe mask from it"),
    ("UState", 0x88, 8, "IgnoreMask", "0x49027",
     "and edx,DWORD PTR [edi+0x88]", "the second half of the same recompute"),
    ("UState", 0x94, 2, "LabelTableOffset", "0x46f22",
     "mov ax,WORD PTR [esi+0x94]", "the label lookup reads it and compares it against 0xffff"),
]


def die(msg):
    sys.exit("state-members: " + msg)


def symbol_sizes():
    """`{VA: (size, name)}` from the committed symbol map -- the body bounds, never a `0xcc` guess."""
    out = {}
    if not os.path.exists(SYMBOLS):
        return out
    with open(SYMBOLS, encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 3:
                continue
            try:
                out[int(row[0], 16)] = (int(row[2]), row[1])
            except ValueError:
                continue
    return out


class Image:
    def __init__(self, path):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import xbe as X

        self.x = X.Xbe(path)
        self.syms = symbol_sizes()
        self.starts = sorted(self.syms)
        self._dis = {}

    def dis(self, va, size):
        key = (va, size)
        if key in self._dis:
            return self._dis[key]
        blob = self.x.read_va(va, size)
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(blob)
            path = fh.name
        try:
            out = subprocess.run(
                ["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                 "--adjust-vma", hex(va), path],
                capture_output=True, text=True, check=True).stdout
        finally:
            os.unlink(path)
        rows = []
        for line in out.splitlines():
            m = re.match(r"^\s+([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*(\S+)\s*(.*)$", line)
            if not m:
                continue
            ops = " ".join(m.group(4).split())
            rows.append({"va": int(m.group(1), 16), "mnem": m.group(3), "ops": ops,
                         "full": (m.group(3) + " " + ops).strip()})
        self._dis[key] = rows
        return rows

    def body(self, va):
        if va not in self.syms:
            die("%s: no size in %s" % (hex(va), SYMBOLS))
        size = self.syms[va][0]
        return self.dis(va, size)

    def owner(self, va):
        i = bisect.bisect_right(self.starts, va) - 1
        if i >= 0:
            st = self.starts[i]
            size, name = self.syms[st]
            if st <= va < st + size:
                return name, st
        return None, None

    def strings(self):
        """`[(va, text)]` for every printable ASCII run in the image's data sections.

        `.rdata` carries the executable flag in this XBE (0x6), so the split is by section size, not by flag: the
        two `$$` image sections are the only ones skipped.
        """
        out = []
        for s in self.x.sections:
            if s.name.startswith("$$"):
                continue
            blob = self.x.data[s.foff:s.foff + s.fsize]
            for m in re.finditer(rb"[\x20-\x7e]{8,300}", blob):
                out.append((s.va + m.start(), m.group().decode("ascii")))
        return out

    def xrefs(self, va):
        return [h for h in self.x.xrefs_to(va)]


# ---- instruction-level readers -------------------------------------------------------------------------------

MOV_LOAD = re.compile(r"^mov\s+(e\w+),DWORD PTR \[(e\w+)\+0x([0-9a-f]+)\]$")
LEA_FIELD = re.compile(r"^lea\s+(e\w+),\[(e\w+)\+0x([0-9a-f]+)\]$")
ADD_FIELD = re.compile(r"^add\s+(e\w+),0x([0-9a-f]+)$")


def last_write(ins, upto, reg):
    """The last instruction before index `upto` that writes `reg` as a `[base+disp]` field, as (insn, disp, base)."""
    for i in range(upto - 1, -1, -1):
        m = MOV_LOAD.match(ins[i]["full"])
        if m and m.group(1) == reg:
            return ins[i], int(m.group(3), 16)
    return None, None


def assert_members(img):
    """The four names the assert's own def-use chain carries, as rows."""
    hit = None
    for va, text in img.strings():
        if EXPR in text:
            hit = (va, text)
            break
    if not hit:
        die("the frame assert's expression is not in the image")
    expr_va, expr_text = hit
    sites = []
    for site in img.xrefs(expr_va):
        name, base = img.owner(site)
        if base is None:
            continue
        ins = img.body(base)
        # `xrefs_to` answers the offset of the immediate operand, so the push that carries it starts one byte
        # earlier -- take the instruction that contains the site, not the one that starts on it.
        idx = [k for k, i in enumerate(ins) if i["va"] <= site < i["va"] + 8]
        if not idx:
            continue
        # the push must be followed by the assert printer within a few instructions
        tail = ins[idx[0]: idx[0] + 4]
        if any(i["mnem"] == "call" and i["ops"].strip() == hex(ASSERT_PRINTER) for i in tail):
            sites.append((name, base, ins, idx[0]))
    if not sites:
        die("the expression is not referenced by an assert printer call")
    name, base, ins, at = sites[0]

    # `cmp ecx,DWORD PTR [edx+0x48]` (Code vs Script.Data) is the assertion's own first test.
    cmp_i = None
    for k, i in enumerate(ins[:at]):
        m = re.match(r"^cmp\s+(e\w+),DWORD PTR \[(e\w+)\+0x(48|4c)\]$", i["full"])
        if m:
            cmp_i = (k, i, m.group(1), m.group(2))
            break
    if not cmp_i:
        die("%s: the assert no longer compares a pointer against [+0x48]" % name)
    k, i48, code_reg, node_reg = cmp_i

    node_ins, node_disp = last_write(ins, k, node_reg)
    if node_ins is None or node_disp != 0x4:
        die("%s: the register compared against Script.Data is not loaded from a +0x4 field" % name)
    frame_reg = MOV_LOAD.match(node_ins["full"]).group(2)

    code_ins, code_disp = last_write(ins, k, code_reg)
    if code_ins is None or code_disp != 0xC:
        die("%s: the pointer compared against Script.Data is not loaded from a +0xc field" % name)
    if MOV_LOAD.match(code_ins["full"]).group(2) != frame_reg:
        die("%s: Code and Node are not read from the same base" % name)

    # the frame itself: the last load of the frame register from the object
    frame_ins, frame_disp = last_write(ins, k, frame_reg)
    if frame_ins is None:
        die("%s: no load of the frame register" % name)
    frame_base = MOV_LOAD.match(frame_ins["full"]).group(2)

    rows = []
    probe = name + "+0x%x"
    rows.append(("member", "UObject", frame_disp, 4, "StateFrame", "assert",
                 "0x%06x" % frame_ins["va"],
                 "%s: the assert dereferences the pointer loaded here (`%s`)"
                 % (probe % (frame_ins["va"] - base), frame_ins["full"])))
    rows.append(("member", "FStateFrame", node_disp, 4, "Node", "assert",
                 "0x%06x" % node_ins["va"],
                 "its `+0x%x` is compared against `Script` at `0x%06x`" % (node_disp, i48["va"])))
    rows.append(("member", "FStateFrame", code_disp, 4, "Code", "assert",
                 "0x%06x" % code_ins["va"],
                 "compared against `Node->Script.Data` at `0x%06x` (`%s`)" % (i48["va"], i48["full"])))
    rows.append(("member", "UStruct", 0x48, 12, "Script", "assert",
                 "0x%06x" % i48["va"],
                 "`%s` -- the TArray the assert indexes; `+0x4c` is its `Num()`" % i48["full"]))
    rows.append(("member", "FStateFrame", 0x1C, 4, "", "shape",
                 "0x%06x" % 0x48EAC,
                 "the state setter reads `[frame+0x1c]` and takes its `+0x20` as a name; the image names no member"))
    rows.append(("member", "UObject", 0x20, 4, "Name", "runtime", "",
                 "`tools/xemu/names.py --verify`: the dword that resolves through `GNames` to the object's name"))
    rows.append(("member", "UObject", 0x24, 4, "Class", "runtime", "",
                 "`tools/xemu/names.py --verify`: the dword pointing at the object's `UClass`"))
    return rows, expr_text


def record_rows(img):
    """`UState::Serialize`'s writes, in the order the body performs them."""
    ins = img.body(SERIALIZE)
    this_regs = set()
    field = {}
    pending = []
    writes = []
    for i in ins:
        f = i["full"]
        m = re.match(r"^mov\s+(e\w+),ecx$", f)
        if m:
            this_regs.add(m.group(1))
            continue
        m = LEA_FIELD.match(f)
        if m:
            field[m.group(1)] = (int(m.group(3), 16), i)
            continue
        m = ADD_FIELD.match(f)
        if m and m.group(1) in this_regs:
            field[m.group(1)] = (int(m.group(2), 16), i)
            continue
        m = re.match(r"^push\s+(e\w+)$", f)
        if m:
            if m.group(1) in field:
                pending.append(field[m.group(1)])
            continue
        m = re.match(r"^call\s+0x([0-9a-f]+)$", f)
        if m and int(m.group(1), 16) in WRITERS:
            if not pending:
                die("%s: a writer call with no field pointer on the stack" % hex(i["va"]))
            disp, at = pending.pop()
            writes.append((disp, WRITERS[int(m.group(1), 16)], at))
            # the helper returns the archive in `eax`, so a `push eax` after it carries the archive, not the last
            # field pointer the register happened to hold.
            field.pop("eax", None)
    if not writes:
        die("%s: no writer calls" % hex(SERIALIZE))
    if sum(w[1] for w in writes) != RECORD:
        die("the writer chain writes %d bytes, not %d" % (sum(w[1] for w in writes), RECORD))
    names = {0x80: "ProbeMask", 0x88: "IgnoreMask", 0x90: "Flags", 0x94: "LabelTableOffset"}
    rows = []
    for order, (disp, size, at) in enumerate(writes, 1):
        if disp not in names:
            die("%s: unexpected record field +0x%x" % (hex(at["va"]), disp))
        rows.append(("record", "UState", disp, size, names[disp], "writer",
                     "0x%06x" % at["va"],
                     "write %d of 4, `%s`, handed to the %d-byte writer"
                     % (order, at["full"], size)))
    if sorted(r[2] for r in rows) != sorted(names):
        die("the writer chain does not cover the four fields")
    return rows


def fixed_rows(img):
    """The four members a fixed instruction reads, re-read from the body that carries it."""
    rows = []
    for owner, disp, size, name2, site, needle, note in FIXED:
        va = int(site, 16)
        body, base = img.owner(va)
        if base is None:
            die("%s: no body" % site)
        ins = img.body(base)
        found = [i for i in ins if i["va"] == va]
        if not found:
            die("%s: no instruction" % site)
        if found[0]["full"] != needle:
            die("%s decodes to `%s`, not `%s`" % (site, found[0]["full"], needle))
        rows.append(("member", owner, disp, size, name2, "measured", site,
                     "%s: `%s` -- %s" % (body, needle, note)))
    return rows


def sentinel_rows(img):
    """Every immediate in `.text` equal to 0x2b2, classified, and joined to the reserve's own row for the slot."""
    seed = {}
    with open(SEED_CSV, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            seed[int(r["index"])] = r
    if SENTINEL not in seed:
        die("inventory/gname_seed.csv has no row for index %d" % SENTINEL)
    rows = []
    for s in img.x.sections:
        if not s.executable:
            continue
        needle = struct.pack("<I", SENTINEL)
        blob = img.x.data[s.foff:s.foff + s.fsize]
        pos = blob.find(needle)
        while pos >= 0:
            va = s.va + pos
            body, base = img.owner(va)
            if base is not None:
                for i in img.body(base):
                    if i["va"] <= va < i["va"] + 16 and re.search(r"0x2b2\b", i["ops"]):
                        if i["mnem"] == "cmp":
                            rows.append(("sentinel", body, "", 4, "0x2b2", "cmp", "0x%06x" % i["va"], i["full"]))
                        elif i["mnem"] == "mov":
                            rows.append(("sentinel", body, "", 4, seed[SENTINEL]["name"], "seed-store",
                                         "0x%06x" % i["va"], i["full"]))
                        else:
                            die("%s: %s carries the index and is neither a compare nor a store" % (hex(va), i["full"]))
                        break
            pos = blob.find(needle, pos + 1)
    cmps = [r for r in rows if r[5] == "cmp"]
    stores = [r for r in rows if r[5] == "seed-store"]
    if len(cmps) != 4:
        die("%d compare sites, expected 4" % len(cmps))
    if len(stores) != 1:
        die("%d index stores, expected 1 (the reserve's row for %d)" % (len(stores), SENTINEL))
    if stores[0][6] != seed[SENTINEL]["store"]:
        die("the index store at %s is not the reserve's row (%s)" % (stores[0][6], seed[SENTINEL]["store"]))
    if stores[0][4] != seed[SENTINEL]["name"]:
        die("the seed names slot %d differently" % SENTINEL)
    return rows


def collect(xbe):
    img = Image(xbe)
    member_rows, expr = assert_members(img)
    rows = member_rows + record_rows(img) + fixed_rows(img) + sentinel_rows(img)
    return rows, img, expr


# ---- the committed-CSV gate -----------------------------------------------------------------------------------

def check(rows):
    kinds = {}
    for r in rows:
        kinds.setdefault(r["kind"], []).append(r)
    if set(kinds) != {"member", "record", "sentinel"}:
        die("kinds are %s, not member/record/sentinel" % sorted(kinds))
    rec = kinds["record"]
    if [int(r["offset"], 0) for r in rec] != [0x80, 0x88, 0x94, 0x90]:
        die("the record's write order is not +0x80, +0x88, +0x94, +0x90")
    if sum(int(r["size"]) for r in rec) != RECORD:
        die("the record's fields do not sum to %d bytes" % RECORD)
    if sorted(int(r["offset"], 0) for r in rec) != [0x80, 0x88, 0x90, 0x94]:
        die("the record's fields are not the four the writer chain covers")
    sent = kinds["sentinel"]
    if sum(r["basis"] == "cmp" for r in sent) != 4:
        die("the sentinel is not four compares")
    store = [r for r in sent if r["basis"] == "seed-store"]
    if len(store) != 1 or store[0]["name"] != "Auto":
        die("the sentinel's index store is not the reserve's `Auto` row")
    names = {}
    for r in kinds["member"]:
        if not r["name"]:
            continue
        key = (r["owner"], int(r["offset"], 0))
        if key in names and names[key] != r["name"]:
            die("%s+%s named twice" % key)
        names[key] = r["name"]
    for want in [("UObject", 0x0C, "StateFrame"), ("FStateFrame", 0x04, "Node"),
                 ("FStateFrame", 0x0C, "Code"), ("UStruct", 0x48, "Script"),
                 ("UState", 0x90, "Flags"), ("UState", 0x94, "LabelTableOffset")]:
        if names.get((want[0], want[1])) != want[2]:
            die("%s+0x%x is not named %s" % want)
    if len(rows) != 19 + 1:
        die("%d rows, expected 20" % len(rows))
    return True


# ---- the page's block ------------------------------------------------------------------------------------------

def render(rows):
    members = [r for r in rows if r["kind"] == "member"]
    record = [r for r in rows if r["kind"] == "record"]
    sent = [r for r in rows if r["kind"] == "sentinel"]
    out = []
    out.append("**The image names six of the members itself.** The frame assert's expression is the source: a row "
               "below is a displacement whose name is the identifier the expression prints for it, read out of the "
               "same body's def-use chain, plus the four members a fixed instruction reads.")
    out.append("")
    out.append("| owner | offset | size | name | how it is known | site |")
    out.append("| --- | ---: | ---: | --- | --- | --- |")
    for r in members:
        mark = "`%s`" % r["name"] if r["name"] else "*(no name in the image)*"
        out.append("| `%s` | `+0x%s` | %s | %s | %s | `%s` |"
                   % (r["owner"], format(int(r["offset"], 0), "x"), r["size"], mark, r["basis"],
                      r["site"] or "-"))
    out.append("")
    out.append("**The 22-byte record's fields are written in this order** -- `UState::Serialize` hands their pointers "
               "to four size-fixed writers, and cdecl makes the *second-to-last* push the field:")
    out.append("")
    out.append("| write | owner | offset | size | field | instruction |")
    out.append("| ---: | --- | ---: | ---: | --- | --- |")
    for k, r in enumerate(record, 1):
        out.append("| %d | `%s` | `+0x%s` | %s | `%s` | `%s` |"
                   % (k, r["owner"], format(int(r["offset"], 0), "x"), r["size"], r["name"],
                      r["evidence"].replace("`", "")))
    out.append("")
    out.append("**The `0x2b2` index has five sites in `.text`** -- four compares that ask for it, and the reserve's "
               "own store that names it (`inventory/gname_seed.csv` row 690):")
    out.append("")
    out.append("| body | site | what it is | instruction |")
    out.append("| --- | --- | --- | --- |")
    for r in sent:
        out.append("| `%s` | `%s` | %s | `%s` |" % (r["owner"], r["site"], r["basis"], r["evidence"]))
    out.append("")
    out.append("The last two writes are the correction this read carries: the two-byte field is at **`+0x94`**, the "
               "four-byte one at **`+0x90`** -- the order the label lookup and the setter read them in.")
    return "\n".join(out)


def write_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    if MARK_BEGIN not in text or MARK_END not in text:
        die("%s: no %s / %s markers" % (path, MARK_BEGIN, MARK_END))
    head, rest = text.split(MARK_BEGIN, 1)
    _old, tail = rest.split(MARK_END, 1)
    open(path, "w", encoding="utf-8").write(head + MARK_BEGIN + "\n" + render(rows) + "\n" + MARK_END + tail)


def check_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    if MARK_BEGIN not in text or MARK_END not in text:
        die("%s: no %s / %s markers" % (path, MARK_BEGIN, MARK_END))
    got = text.split(MARK_BEGIN, 1)[1].split(MARK_END, 1)[0]
    want = "\n" + render(rows) + "\n"
    if got != want:
        die("FAIL check-doc: %s's generated block is stale (regenerate it with --write-doc)" % path)


def read_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({k: r[k] for k in FIELDS})
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc", metavar="PATH")
    ap.add_argument("--check-doc", metavar="PATH")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        rows = read_csv(CSV_PATH)
        check(rows)
        print("state-members: %d rows (%d members, %d record fields, %d sentinel sites)"
              % (len(rows), sum(r["kind"] == "member" for r in rows),
                 sum(r["kind"] == "record" for r in rows), sum(r["kind"] == "sentinel" for r in rows)))
        return 0
    if args.check_doc:
        check_doc(args.check_doc, read_csv(CSV_PATH))
        print("state-members: %s's block is current" % args.check_doc)
        return 0
    if args.report:
        rows = read_csv(CSV_PATH)
        check(rows)
        print(render(rows))
        return 0
    if not args.xbe:
        die("--xbe is required unless --check/--check-doc/--report")
    rows, _img, _expr = collect(args.xbe)
    if args.verify:
        committed = read_csv(CSV_PATH)
        got = [(r["kind"], r["owner"], str(r["offset"]), str(r["size"]), r["name"], r["site"]) for r in rows]
        want = [(r["kind"], r["owner"], str(r["offset"]), str(r["size"]), r["name"], r["site"]) for r in committed]
        if got != want:
            die("--verify: the committed rows differ from the image")
        print("state-members: %d rows re-read from the image" % len(rows))
        return 0
    out_rows = [{"kind": k, "owner": o, "offset": hex(d) if isinstance(d, int) else d,
                 "size": s, "name": n, "basis": b, "site": st, "evidence": ev}
                for (k, o, d, s, n, b, st, ev) in rows]
    write_csv(args.out, out_rows)
    if args.write_doc:
        write_doc(args.write_doc, out_rows)
    print("state-members: %d rows -> %s" % (len(out_rows), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
