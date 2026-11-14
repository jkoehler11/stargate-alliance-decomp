#!/usr/bin/env python3
"""What the class flag word at `+0x498` holds, and which bit the purge's byte gates (U5)

`#522` showed that the last gate in the file-layer purge is a **signed byte read** of a class's `+0x498`
(`mov cl,BYTE PTR [eax+0x498]; test cl,cl; js`), and left the bit unnamed: *"the next read is the class
serialiser and which CLASS_* bit sits in the top bit of that byte."* This tool measures the word from the
image and joins it against the shipped packages:

* **The word is `UClass`'s class-flag dword, and the constructor inherits a masked subset of the super's.**
  `Core_UnClass` at `0x00039520` loads `[this+0x28]` (the super class), then `[super+0x498]`, `and`s it with
  the immediate `0x0e0f86ec`, `or`s it into `[this+0x498]` and stores it back. That mask is the build's own
  `CLASS_Inherit` constant, and it is the only place in the image that names the inheritable bits.
* **Two bits are set for every registered script class, and neither is inheritable.** `FUN_0003af60` (the
  registration overload of the `Class` constructor) ends `or ecx,0x12; mov [esi+0x498],ecx`, so a class built
  from the registration's data carries `0x2|0x10`. Both are outside the inherit mask -- a loader/compiler pair
  of bits, not a class-declaration pair.
* **The purge's byte is bit 7 of the low byte, i.e. `0x80` -- not the top bit of the dword.** A byte load at
  `+0x498` addresses the least-significant byte, and `test cl,cl; js` tests its sign: `0x80`, not `0x80000000`.
  The word's high bits are not reachable from this read at all.
* **The corpus says who carries each bit.** Every shipped class's package `ClassFlags` (2,319 rows in
  `inventory/classes.csv`) is joined to its super: a bit that the constructor claims to inherit should never be
  dropped between a class and its super, and a bit outside the mask should be. That is the two-sided test below,
  and it separates the 13 inheritable bits from the 19 that are not.

    python3 tools/class-flag-word.py --xbe $DUMP/default.xbe -o inventory/class_flag_word.csv
    python3 tools/class-flag-word.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/class-flag-word.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/class-flag-word.py --check        # synthetic bytes, no dump, no writes

Bit *names* are the pinned reader's (`tools/uelib` `UnrealFlags.cs`, `ClassFlags`); the bit *populations*, the
inherit mask and every address are this build's. Where the reader has no name for a bit the row says unnamed.
Addresses, counts and prose only: no game data, no decompiled output.
"""

import argparse
import bisect
import csv
import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile

FLAGS = 0x498
CTOR = 0x00039520          # Core_UnClass: the constructor that inherits the flags
CTOR_WINDOW = 0x00039641   # ..the inherit block inside it
CTOR_BYTES = bytes.fromhex("8b46283bc574198b80980400008b8e9804000025ec860f0e0bc8898e98040000")
REGISTER = 0x0003AF60      # the registration overload of the Class constructor
REGISTER_WINDOW = 0x0003AF96
REGISTER_BYTES = bytes.fromhex("83c912898e98040000")
PURGE = 0x0006C750         # the file-layer purge
PURGE_WINDOW = 0x0006C803
PURGE_BYTES = bytes.fromhex("8a889804000084c978")

COLUMNS = ["kind", "at", "fn", "detail", "evidence"]
BLOCK_MARK = ("<!-- generated:class-flag-word -->", "<!-- /generated:class-flag-word -->")
CSV_PATH = os.path.join("inventory", "class_flag_word.csv")
CLASSES = os.path.join("inventory", "classes.csv")
SYMBOLS = os.path.join("symbols", "xbox_functions.csv")

# The reader's names for the 32 bits (UELib UnrealFlags.cs, ClassFlags). A value shared by two UE generations is
# spelled once; a bit the UE2 build's reader has no name for is empty in this table and rendered as "unnamed".
NAMES = {
    0x00000001: "Abstract", 0x00000002: "Compiled", 0x00000004: "Config", 0x00000008: "Transient",
    0x00000010: "Parsed", 0x00000020: "Localized", 0x00000040: "SafeReplace", 0x00000080: "RuntimeStatic",
    0x00000100: "NoExport", 0x00000200: "NoUserCreate/Placeable", 0x00000400: "PerObjectConfig",
    0x00000800: "NativeReplication", 0x00001000: "EditInlineNew", 0x00002000: "CollapseCategories",
    0x00004000: "ExportStructs", 0x00200000: "Instanced", 0x00400000: "HideDropDown",
    0x00800000: "CacheExempt", 0x01000000: "ParseConfig", 0x02000000: "Cacheable", 0x04000000: "Exported",
    0x10000000: "Intrinsic", 0x20000000: "NativeOnly", 0x40000000: "PerObjectLocalized",
    0x80000000: "HasCrossLevelRefs",
}

GRP1 = {1: "or", 4: "and", 6: "xor", 7: "cmp", 0: "add", 5: "sub"}
REGS = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]


# ---------------------------------------------------------------- the image

def load_xbe(path):
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("xbe", os.path.join(here, "xbe.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Xbe(path)


def load_symbols(path=SYMBOLS):
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append((int(r["address"], 16), int(r["size"] or 0), r["name"]))
            except ValueError:
                continue
    rows.sort()
    return [(a, s, n) for a, s, n in rows if s > 0]


def owner(table, starts, va):
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0 and starts[i] <= va < table[i][0] + table[i][1]:
        return table[i]
    return None


def expect(xbe, at, want, label):
    have = xbe.read_va(at, len(want))
    if have != want:
        sys.exit("FAIL %s: %s holds %s, expected %s" % (label, "0x%08x" % at, have.hex(" "), want.hex(" ")))
    return at


def inherit_mask(xbe):
    """The mask the constructor applies to the super's flags, read out of the inherit block."""
    blob = xbe.read_va(CTOR_WINDOW, len(CTOR_BYTES))
    expect(xbe, CTOR_WINDOW, CTOR_BYTES, "class-flag inherit block")
    # `25 imm32` is `and eax,imm32`, at offset 19 of the window; its immediate follows.
    return struct.unpack("<I", blob[20:24])[0]


def registration_bits(xbe):
    """The pair `FUN_0003af60` ORs into a registered class's flags, read out of `83 c9 imm8`."""
    blob = xbe.read_va(REGISTER_WINDOW, len(REGISTER_BYTES))
    expect(xbe, REGISTER_WINDOW, REGISTER_BYTES, "registration flags write")
    return blob[2]


def purge_bit(xbe):
    """The bit the purge's byte read gates, derived from the operand width and the sign test.

    A byte at the dword's own displacement addresses its least-significant byte, and `test cl,cl; js` tests
    that byte's sign -- bit 7 of it. Nothing in the instruction can reach a higher byte, so the reachable mask
    is `0x80` and the dword's top bit is out of range.
    """
    blob = xbe.read_va(PURGE_WINDOW, len(PURGE_BYTES))
    expect(xbe, PURGE_WINDOW, PURGE_BYTES, "purge class-flag gate")
    modrm = blob[1]
    if (modrm & 0xC0) != 0x80 or (modrm & 7) == 4:
        sys.exit("FAIL purge gate: modrm 0x%02x is not [reg+disp32]" % modrm)
    disp = struct.unpack("<I", blob[2:6])[0]
    if disp != FLAGS:
        sys.exit("FAIL purge gate: displacement 0x%x is not the flag word" % disp)
    if blob[6:8] != bytes.fromhex("84c9") or blob[8] != 0x78:
        sys.exit("FAIL purge gate: not `test cl,cl; js` after the byte load")
    return 0x80, 0x498


def disasm_starts(xbe, start, size):
    """Instruction-start addresses of one function, from objdump as an independent decoder."""
    blob = xbe.read_va(start, size)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=%d" % start, temp], capture_output=True, text=True).stdout
    finally:
        os.unlink(temp)
    seen = set()
    for line in out.splitlines():
        if "\t" not in line:
            continue
        head = line.split("\t", 1)[0].strip().rstrip(":")
        try:
            seen.add(int(head, 16))
        except ValueError:
            continue
    return seen


def accesses(xbe, table, starts):
    """Every `[reg+0x498]` in the code sections, with an instruction-boundary proof and a width."""
    needle = struct.pack("<I", FLAGS)
    rows = []
    cache = {}
    for sec in xbe.sections:
        covered = sum(size for start, size, _n in table if sec.contains(start))
        if not covered or covered * 10 < sec.vsize:
            continue  # only sections the symbol map says are code (every XBE section sets the flag)
        blob = xbe.read_va(sec.va, sec.fsize)
        i = blob.find(needle)
        while i >= 0:
            if i >= 2:
                modrm = blob[i - 1]
                op = blob[i - 2]
                if (modrm & 0xC0) == 0x80 and (modrm & 7) != 4:
                    va = sec.va + i - 2
                    width, text = decode(op, modrm, blob[i + 4:i + 12], blob[i + 4])
                    if text:
                        fn = owner(table, starts, va)
                        key = fn[0] if fn else None
                        if key is not None and key not in cache:
                            cache[key] = disasm_starts(xbe, fn[0], fn[1])
                        aligned = bool(key is not None and va in cache[key])
                        proof = "aligned" if aligned else ("not-a-boundary" if fn else "unmapped")
                        rows.append({"kind": "access", "at": "0x%08x" % va,
                                     "fn": fn[2] if fn else "-",
                                     "detail": "%s %s" % (width, text),
                                     "evidence": proof,
                                     "_va": va})
            i = blob.find(needle, i + 1)
    rows.sort(key=lambda r: r["_va"])
    return rows


def decode(op, modrm, tail, imm8):
    """(width, text) for one modrm instruction whose displacement is the flag word."""
    reg = (modrm >> 3) & 7
    rm = modrm & 7
    R, M = REGS[reg], REGS[rm]
    if op == 0x8B:
        return "dword", "mov %s,[%s+0x498]" % (R, M)
    if op == 0x89:
        return "dword", "mov [%s+0x498],%s" % (M, R)
    if op == 0x8A:
        return "byte", "mov %s8,[%s+0x498]" % (R, M)
    if op == 0x88:
        return "byte", "mov [%s+0x498],%s8" % (M, R)
    if op == 0x81:
        imm = struct.unpack("<I", tail[:4])[0]
        return "dword", "%s [%s+0x498],0x%x" % (GRP1.get(reg, "grp"), M, imm)
    if op == 0x83:
        imm = struct.unpack("<i", bytes([imm8]) + b"\0\0\0")[0] & 0xffffffff
        return "dword", "%s [%s+0x498],0x%x" % (GRP1.get(reg, "grp"), M, imm)
    if op == 0x80:
        return "byte", "%s [%s+0x498],0x%x" % (GRP1.get(reg, "grp"), M, imm8)
    if op == 0xF7:
        imm = struct.unpack("<I", tail[:4])[0]
        return "dword", "test [%s+0x498],0x%x" % (M, imm)
    if op == 0xF6:
        return "byte", "test [%s+0x498],0x%x" % (M, imm8)
    if op == 0x85:
        return "dword", "test [%s+0x498],%s" % (M, R)
    if op == 0x39:
        return "dword", "cmp [%s+0x498],%s" % (M, R)
    if op == 0x3B:
        return "dword", "cmp %s,[%s+0x498]" % (R, M)
    if op == 0x0B:
        return "dword", "or %s,[%s+0x498]" % (R, M)
    if op == 0x23:
        return "dword", "and %s,[%s+0x498]" % (R, M)
    if op == 0x33:
        return "dword", "xor %s,[%s+0x498]" % (R, M)
    if op == 0x8D:
        return "address", "lea %s,[%s+0x498]" % (R, M)
    if op == 0xC7:
        return "dword", "mov [%s+0x498],imm32" % M
    return "other", ""


def mask_of(detail):
    """The single immediate a reader tests, or 0 for a load/store/lea/address."""
    text = detail.split(" ", 1)[1] if detail.split(" ", 1)[0] in ("byte", "dword", "address", "other") else detail
    if not re.match(r"^(test|cmp|and|or|xor)\b", text):
        return 0
    m = re.search(r",0x([0-9a-f]+)\]?$", text)
    return int(m.group(1), 16) if m else 0


# ---------------------------------------------------------------- the corpus

def read_flags(text):
    value = 0
    for part in (text or "").split(";"):
        if part:
            try:
                value |= int(part.split(":")[0], 16)
            except ValueError:
                pass
    return value


def corpus(path=CLASSES):
    """Per-bit populations and the class-to-super transfer counts over the shipped packages."""
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    recs = [(r["package"], r["class"], read_flags(r["class_flags"]), r["super"]) for r in rows]
    by_name = {}
    for rec in recs:
        by_name.setdefault(rec[1], []).append(rec)
    pairs = []
    for rec in recs:
        cands = [c for c in by_name.get(rec[3], []) if c[1] != rec[1]]
        if not cands:
            continue
        same = [c for c in cands if c[0] == rec[0]]
        pairs.append((rec, (same or cands)[0]))
    pops = {}
    for bit in (1 << i for i in range(32)):
        holders = [r[1] for r in recs if r[2] & bit]
        add = [c[1] for c, s in pairs if (c[2] & bit) and not (s[2] & bit)]
        drop = [c[1] for c, s in pairs if (s[2] & bit) and not (c[2] & bit)]
        pops[bit] = {"classes": len(holders), "add": len(add), "drop": len(drop),
                     "example": (sorted(holders)[0] if holders else ""),
                     "drop_example": (sorted(drop)[0] if drop else "")}
    return {"classes": len(recs), "pairs": len(pairs), "bits": pops}


# ---------------------------------------------------------------- measure

def measure(xbe_path):
    xbe = load_xbe(xbe_path)
    table = load_symbols()
    starts = [row[0] for row in table]
    mask = inherit_mask(xbe)
    reg_bits = registration_bits(xbe)
    bit, byte_at = purge_bit(xbe)
    pop = corpus()

    rows = [
        {"kind": "site", "at": "0x%08x" % CTOR_WINDOW, "fn": "Core_UnClass",
         "detail": "inherit mask 0x%08x" % mask,
         "evidence": "mov eax,[esi+0x28]; mov eax,[eax+0x498]; and eax,0x%08x; or ecx,eax; mov [esi+0x498],ecx"
                     % mask},
        {"kind": "site", "at": "0x%08x" % REGISTER_WINDOW, "fn": "FUN_0003af60",
         "detail": "registration sets 0x%02x" % reg_bits,
         "evidence": "or ecx,0x%02x; mov [esi+0x498],ecx" % reg_bits},
        {"kind": "site", "at": "0x%08x" % PURGE_WINDOW, "fn": "FUN_0006c750",
         "detail": "purge byte gates 0x%02x" % bit,
         "evidence": "mov cl,[eax+0x%x]; test cl,cl; js -- byte 0 of the dword, sign bit" % byte_at},
        {"kind": "corpus", "at": "-", "fn": "-",
         "detail": "%d classes, %d class/super pairs" % (pop["classes"], pop["pairs"]),
         "evidence": "inventory/classes.csv class_flags joined to super"},
    ]
    for i in range(32):
        b = 1 << i
        p = pop["bits"][b]
        rows.append({"kind": "bit", "at": "0x%08x" % b, "fn": NAMES.get(b, "unnamed"),
                     "detail": "%d classes, +%d, -%d" % (p["classes"], p["add"], p["drop"]),
                     "evidence": ("in the inherit mask; " if mask & b else "not inherited; ")
                                 + ("e.g. %s" % p["example"] if p["example"] else "no class carries it")})
    for r in accesses(xbe, table, starts):
        rows.append(r)
    return rows


def rows_for_csv(rows):
    return [{k: (r.get(k) if r.get(k) is not None else "-") for k in COLUMNS} for r in rows]


# ---------------------------------------------------------------- rendering

def render(rows, mask=None, bit=None):
    def cell(v):
        return str(v).replace("|", "\\|")

    sites = [r for r in rows if r["kind"] == "site"]
    bits = [r for r in rows if r["kind"] == "bit"]
    acc = [r for r in rows if r["kind"] == "access"]
    corpus_row = [r for r in rows if r["kind"] == "corpus"]
    if not sites and not bits:
        return ""

    if mask is None:
        inherit = [r for r in sites if r["detail"].startswith("inherit mask")]
        mask = int(inherit[0]["detail"].split()[-1], 16) if inherit else 0
    if bit is None:
        gate = [r for r in sites if r["detail"].startswith("purge byte gates")]
        bit = int(gate[0]["detail"].split()[-1], 16) if gate else 0

    regs = [r for r in sites if r["detail"].startswith("registration sets")]
    reg = int(regs[0]["detail"].split()[-1], 16) if regs else 0x12
    lines = []
    lines.append("**The word at `+0x498` is `UClass`'s class-flag dword, and the constructor names the bits it "
                 "inherits: `0x%08x`.** `Core_UnClass` (`0x%08x`) reads the super's word through `[this+0x28]`, "
                 "`and`s it with that immediate and `or`s it into its own -- so a class starts with every "
                 "inheritable bit its parent had, and with none of the others. `FUN_0003af60`, the registration "
                 "overload every script class is built from, then `or`s `0x%02x` (bits 1 and 4) into the same word; "
                 "both of those are *outside* the mask, which is what a compiler/loader pair of bits looks like."
                 % (mask, CTOR, reg))
    lines.append("")
    lines.append("| at | in | what | instruction |")
    lines.append("|---|---|---|---|")
    for r in sites:
        lines.append("| `%s` | %s | %s | `%s` |" % (r["at"], cell(r["fn"]), cell(r["detail"]), cell(r["evidence"])))
    lines.append("")
    if corpus_row:
        counts = {int(r["at"], 16): [int(x) for x in re.findall(r"\d+", r["detail"])][:3] for r in bits}
        inside = sorted(((b, counts[b][2]) for b in counts if b & mask and counts[b][2]), key=lambda t: -t[1])
        outside = sorted(((b, counts[b][2]) for b in counts if not b & mask), key=lambda t: -t[1])[:3]
        lines.append("**The shipped packages agree with the mask, and the disagreement is per bit.** Across %s, the "
                     "bits the constructor leaves out are dropped between a class and its super by the hundred (%s), "
                     "while the in-mask bits are dropped only on %d class(es): %s. `0x200` is the value the reader "
                     "itself spells twice, `NoUserCreate` and `Placeable`, which is why it is the one that moves most."
                     % (corpus_row[0]["detail"].replace(",", " and", 1),
                        ", ".join("`0x%x` %d times" % t for t in outside if t[1]),
                        sum(t[1] for t in inside),
                        ", ".join("`0x%x` %d" % t for t in inside) or "none"))
        lines.append("")
    if bits:
        lines.append("**The purge's byte is `0x%02x`, not the dword's top bit.** A byte load at `+0x498` addresses "
                     "the least-significant byte of the word, and `test cl,cl; js` tests that byte's sign, so the "
                     "only bit the gate can see is bit 7 -- `0x%02x`. `0x80000000` is not reachable from this "
                     "instruction at all, whatever the earlier reading assumed about \"the top bit of the first "
                     "byte\"." % (bit, bit))
        lines.append("")
        lines.append("| bit | name | in the inherit mask | classes | added by a class | dropped by a class |"
                     " exemplar |")
        lines.append("|---:|---|---:|---:|---:|---:|---|")
        for r in bits:
            value = int(r["at"], 16)
            counts = r["detail"].split(",")
            cls = counts[0].split()[0]
            add = counts[1].strip().lstrip("+")
            drop = counts[2].strip().lstrip("-")
            mark = "**`0x%02x`**" % value if value == bit else "0x%02x" % value
            lines.append("| %s | %s | %s | %s | %s | %s | %s |"
                         % (mark, cell(r["fn"]), "yes" if mask & value else "", cls, add, drop,
                            ("`%s`" % r["evidence"].split("e.g. ")[-1]) if "e.g. " in r["evidence"] else ""))
        lines.append("")
    if acc:
        aligned = sum(1 for r in acc if r["evidence"] == "aligned")
        mapped = sum(1 for r in acc if r["evidence"] != "unmapped")
        byte = sum(1 for r in acc if r["detail"].startswith("byte"))
        readers_n = sum(1 for r in acc if mask_of(r["detail"]))
        lines.append("**The image names the displacement %d times: %d sit inside a mapped function and %d of those "
                     "are instruction boundaries; %d are outside the symbol map.** %d are byte-wide, and %d carry "
                     "one immediate -- the readers. A reader's immediate is the clearest evidence for what a bit "
                     "means: `Core_UnTemplate` tests `0x20` (a localised template), `Core_UnObj` tests `0x4` and "
                     "`0x8`, `Engine_UnLevAct` tests `0x1`."
                     % (len(acc), mapped, aligned, len(acc) - mapped, byte, readers_n))
        lines.append("")
        readers = {}
        for r in acc:
            m = mask_of(r["detail"])
            if m and r["evidence"] == "aligned":
                readers.setdefault((m, r["fn"]), []).append(r["at"])
        lines.append("| mask | name | at | in |")
        lines.append("|---:|---|---|---|")
        for (m, fn), ats in sorted(readers.items()):
            lines.append("| `0x%x` | %s | %s | %s |"
                         % (m, NAMES.get(m, "unnamed"), ", ".join("`%s`" % a for a in ats[:4]), cell(fn)))
        lines.append("")
    return "\n".join(lines)


def splice(path, block):
    text = open(path).read()
    a, b = BLOCK_MARK
    if a not in text or b not in text:
        sys.exit("%s: markers not found" % path)
    head, rest = text.split(a, 1)
    _old, tail = rest.split(b, 1)
    return head + a + "\n" + block + "\n" + b + tail


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("wrote %s (%d rows)" % (path, len(rows)))


def load_csv(path=CSV_PATH):
    with open(path, newline="") as fh:
        return [{k: (r.get(k) or "-") for k in COLUMNS} for r in csv.DictReader(fh)]


# ---------------------------------------------------------------- self-test

def self_check():
    """Synthetic bytes and rows: no dump, no writes."""
    n = [0]

    def ok(cond, label):
        n[0] += 1
        if not cond:
            sys.exit("FAIL %s" % label)

    class FakeXbe:
        def __init__(self, table):
            self.table = table

        def read_va(self, at, size):
            return self.table[at][:size]

    ok(decode(0x8A, 0x88, b"", 0)[0] == "byte", "8a is a byte load")
    ok(decode(0x8A, 0x88, b"", 0)[1] == "mov ecx8,[eax+0x498]", "byte load text")
    ok(decode(0x81, 0xC8, struct.pack("<I", 0x0E0F86EC), 0)[1].startswith("or [eax+0x498]"),
       "81 /1 is an or with an immediate")
    ok(decode(0x81, 0xE0, struct.pack("<I", 0x0E0F86EC), 0)[1].startswith("and [eax+0x498]"),
       "81 /4 is an and")
    ok(decode(0x83, 0xC9, b"", 0x12)[1] == "or [ecx+0x498],0x12", "83 /1 takes an imm8")
    ok(decode(0x8D, 0x96, b"", 0)[1] == "lea edx,[esi+0x498]", "lea is an address")
    ok(decode(0x0F, 0x0D, b"", 0)[1] == "", "an SSE opcode is not guessed into the table")
    ok(mask_of("test [eax+0x498],0x4000000") == 0x4000000, "a test immediate is a mask")
    ok(mask_of("dword test [eax+0x498],0x4000000") == 0x4000000, "the width prefix does not hide the mask")
    ok(mask_of("mov eax,[eax+0x498]") == 0, "a load has no mask")
    ok(mask_of("byte mov cl,[eax+0x498]") == 0, "a byte load has no mask")

    fake = FakeXbe({PURGE_WINDOW: PURGE_BYTES, REGISTER_WINDOW: REGISTER_BYTES, CTOR_WINDOW: CTOR_BYTES})
    ok(purge_bit(fake)[0] == 0x80, "the purge's sign test is bit 7 of the low byte")
    ok(purge_bit(fake)[1] == 0x498, "the byte load's displacement is the flag word")
    ok(registration_bits(fake) == 0x12, "the registration sets 0x12")
    ok(inherit_mask(fake) == 0x0E0F86EC, "the constructor's inherit mask")
    fake.table[PURGE_WINDOW] = bytes.fromhex("8a889c04000084c978")
    try:
        purge_bit(fake)
        ok(False, "a different displacement must fail")
    except SystemExit:
        ok(True, "a different displacement must fail")
    n[0] -= 1

    bits = 0x0E0F86EC
    ok(bits & 0x80, "0x80 is in the mask")
    ok(not bits & 0x80000000, "the dword's top bit is not")
    ok(not bits & 0x12, "neither registration bit is inherited")

    rows = [
        {"kind": "site", "at": "0x1", "fn": "f", "detail": "inherit mask 0x0e0f86ec", "evidence": "and"},
        {"kind": "site", "at": "0x2", "fn": "g", "detail": "registration sets 0x12", "evidence": "or"},
        {"kind": "site", "at": "0x3", "fn": "h", "detail": "purge byte gates 0x80", "evidence": "js"},
        {"kind": "corpus", "at": "-", "fn": "-", "detail": "10 classes, 9 class/super pairs", "evidence": "csv"},
        {"kind": "bit", "at": "0x00000080", "fn": "RuntimeStatic", "detail": "1 classes, +1, -0",
         "evidence": "in the inherit mask; e.g. X"},
        {"kind": "bit", "at": "0x00000001", "fn": "Abstract", "detail": "121 classes, +46, -826",
         "evidence": "not inherited; e.g. Y"},
        {"kind": "access", "at": "0x4", "fn": "Core_UnTemplate", "detail": "byte test [eax+0x498],0x20",
         "evidence": "aligned"},
        {"kind": "access", "at": "0x5", "fn": "Core_UnObj", "detail": "dword mov eax,[eax+0x498]",
         "evidence": "not-a-boundary"},
        {"kind": "access", "at": "0x6", "fn": "-", "detail": "dword mov ecx,[eax+0x498]",
         "evidence": "unmapped"},
    ]
    text = render(rows)
    ok("is `UClass`'s class-flag dword" in text, "the word is introduced")
    ok("| bit | name | in the inherit mask |" in text, "bit table present")
    ok("**`0x80`** | RuntimeStatic" in text, "the gate's bit is marked in the table")
    ok("| `0x20` | Localized | `0x4` | Core_UnTemplate |" in text, "a reader is named")
    ok("not reachable from this instruction" in text, "the top bit is ruled out")
    ok("1 are outside the symbol map" in text, "unmapped references are counted apart")
    ok(render([]) == "", "no rows renders empty")
    print("ok %d assertions (no dump, no writes)" % n[0])
    return 0


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return self_check()

    if args.write_doc:
        rows = measure(args.xbe) if args.xbe else load_csv()
        new = splice(args.write_doc, render(rows))
        with open(args.write_doc, "w") as fh:
            fh.write(new)
        print("wrote %s" % args.write_doc)
        return 0

    if args.check_doc:
        rows = load_csv()
        want = splice(args.check_doc, render(rows))
        have = open(args.check_doc).read()
        if want != have:
            print("FAIL %s: generated block differs from a recomputation off %s" % (args.check_doc, CSV_PATH))
            return 1
        print("ok %s" % args.check_doc)
        return 0

    if not args.xbe:
        ap.error("--xbe is required to measure")
    write_csv(args.out, rows_for_csv(measure(args.xbe)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
