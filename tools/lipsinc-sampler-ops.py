#!/usr/bin/env python3
"""Which memory the two LIPSinc sampler bodies touch, and which of it they write.

The `.lad` census left one half of a question open: 48.5% of the key values survive an `f32` round
trip and the rest carry digits only a `double` holds, so *who wrote them* -- the runtime, or the tool
that built the pack -- decides whether the file's `f64` is a store width or an authoring artefact. The
runtime side is two bodies: `0x000b2230` evaluates one channel's curve at a time and `0x000b24f0` calls
it once per channel. If nothing in that pair writes an `f64` back into a key, the wider half came from
the pack's producer.

`objdump -D -b binary -m i386 -M intel` over each body's span from `symbols/xbox_functions.csv`, one row
per instruction that names memory, with the operand and whether the instruction reads, writes, or does
both. Bounded by the symbol map's size (decimal third column), because a flat window walks into the next
function and credits its stores to this body.

    tools/lipsinc-sampler-ops.py DUMP/default.xbe -o inventory
    tools/lipsinc-sampler-ops.py --write-doc docs/formats/lipsinc-lad.md
    tools/lipsinc-sampler-ops.py --check-doc docs/formats/lipsinc-lad.md   # from the committed CSV
    tools/lipsinc-sampler-ops.py --check        # synthetic listings, no dump, writes nothing

No boot: the image and the committed symbol map.
"""
import argparse
import csv
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["body_va", "body_name", "insn_va", "mnemonic", "operands", "mem_operand", "kind",
          "target"]
ARTIFACT = os.path.join(ROOT, "inventory/lipsinc_sampler_ops.csv")
DOC = os.path.join(ROOT, "docs/formats/lipsinc-lad.md")
SYMBOLS = os.path.join(ROOT, "symbols/xbox_functions.csv")
DOC_START = "<!-- generated:lipsinc-sampler-ops -->"
DOC_END = "<!-- /generated:lipsinc-sampler-ops -->"
DEFAULT_BODIES = ["0x000b2230", "0x000b24f0"]
# one operand and a memory reference: these store the x87/SSE top into memory, everything else single
# operand (`fld`, `fild`, `fcom`, `push`, `call`, `mul`) reads it.
STORE_ONE = ("fst", "fist", "stos", "stmxcsr", "fbstp")
RW = ("add", "sub", "and", "or", "xor", "inc", "dec", "adc", "sbb", "shl", "shr", "sar", "rol", "ror",
      "neg", "not", "xadd", "cmpxchg", "xchg")
PREFIX = ("lock", "rep", "repe", "repz", "repne", "repnz", "data16", "addr32", "notrack")
LINE = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*(.*)$")


def split_operands(text):
    """Intel operands, split on commas outside brackets; `[eax+ebx*4+0x8]` stays whole."""
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def where(mem):
    """`stack` for the body's own frame, `object` for anything reached through another register."""
    base = mem.split("[", 1)[1].split("]")[0].lstrip("^ ") if "[" in mem else ""
    reg = re.split(r"[+*\-]", base)[0].strip().lower()
    return "stack" if reg in ("esp", "ebp") else "object"


def classify(mnemonic, operands):
    """(kind, mem operand or "") for one instruction's text."""
    mem = [o for o in operands if "[" in o]
    if not mem:
        return "", ""
    m = mnemonic.lower()
    if len(operands) == 1:
        return ("write" if m.startswith(STORE_ONE) else "read"), mem[0]
    if "[" in operands[0]:
        return ("read-write" if m.startswith(RW) else "write"), operands[0]
    return "read", mem[0]


def parse_listing(text):
    """Rows from an objdump listing; the byte column is skipped, the mnemonic and operands are kept."""
    rows = []
    for line in text.splitlines():
        hit = LINE.match(line)
        if not hit:
            continue
        va, body = int(hit.group(1), 16), hit.group(3).strip()
        if not body or body.startswith("(") and body.endswith(")"):
            continue
        parts = body.split(None, 1)
        mnemonic = parts[0]
        operands = split_operands(parts[1]) if len(parts) > 1 else []
        while mnemonic.lower() in PREFIX and " " in body:
            body = body.split(None, 1)[1]
            parts = body.split(None, 1)
            mnemonic = parts[0]
            operands = split_operands(parts[1]) if len(parts) > 1 else []
        kind, mem = classify(mnemonic, operands) if mnemonic.lower() != "lea" else ("", "")
        if kind:
            rows.append({"insn_va": "0x%08x" % va, "mnemonic": mnemonic,
                         "operands": ", ".join(operands), "mem_operand": mem, "kind": kind,
                         "target": where(mem)})
    return rows


def symbols(path=SYMBOLS):
    """{va: (name, size in bytes)} -- the third column is a byte count in DECIMAL."""
    out = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split(",")
            if len(parts) < 3 or not parts[0].startswith("0x"):
                continue
            try:
                out[int(parts[0], 16)] = (parts[1], int(parts[2]))
            except ValueError:
                continue
    return out


def body_rows(xbe, va, name, size):
    sec = next((s for s in xbe.sections if s.va <= va < s.va + s.vsize), None)
    if sec is None:
        return []
    data = xbe.data[sec.foff + (va - sec.va):sec.foff + (va - sec.va) + size]
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
        fh.write(data)
        path = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=0x%x" % va, path],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(path)
    rows = parse_listing(out)
    for row in rows:
        row.update({"body_va": "0x%08x" % va, "body_name": name})
    return rows


def census(xbe, bodies):
    syms = symbols()
    rows = []
    for text in bodies:
        va = int(text, 16)
        name, size = syms.get(va, ("", 0))
        if not size:
            print("no symbol size for %s -- refusing a flat window" % text)
            continue
        rows.extend(body_rows(xbe, va, name, size))
    return rows


def render(rows):
    lines = ["| body | insns naming memory | reads | writes | read-writes |",
             "|---|---|---|---|---|"]
    order, seen = [], set()
    for row in rows:
        if row["body_va"] not in seen:
            seen.add(row["body_va"])
            order.append(row["body_va"])
    for va in order:
        mine = [r for r in rows if r["body_va"] == va]
        writes = [r for r in mine if r["kind"] == "write"]
        lines.append("| `%s` %s | %d | %d | %d | %d |"
                     % (va, mine[0]["body_name"], len(mine),
                        len([r for r in mine if r["kind"] == "read"]), len(writes),
                        len([r for r in mine if r["kind"] == "read-write"])))
    writes = [r for r in rows if r["kind"] in ("write", "read-write")]
    into = [r for r in writes if r.get("target") == "object"]
    lines.append("")
    lines.append("**Of the %d store(s) in the pair, %d write into an object and %d only into the "
                 "body's own frame.** The object stores are:" % (len(writes), len(into),
                                                                len(writes) - len(into)))
    lines.append("")
    if into:
        lines.append("| body | insn | instruction | operand written |")
        lines.append("|---|---|---|---|")
        for r in into:
            lines.append("| `%s` | `%s` | `%s %s` | `%s` |"
                         % (r["body_va"], r["insn_va"], r["mnemonic"], r["operands"],
                            r["mem_operand"]))
    else:
        lines.append("none: every store in the pair is a spill to its own stack frame.")
    return "\n".join(lines)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice(path, block):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def self_check():
    bad = []
    text = """\n   b2230:\tdd 05 48 88 4c 00 \tfld    QWORD PTR [0x4c8848]\n
   b2313:\t8b 48 10             \tmov    ecx,DWORD PTR [eax+0x10]\n
   b2323:\tdd 5a 08             \tfstp   QWORD PTR [edx+0x8]\n
   b2333:\tff 52 50             \tcall   QWORD PTR [edx+0x50]\n
   b2343:\tf0 83 45 08 01       \tlock add DWORD PTR [ebp+0x8],0x1\n
   b2353:\t8d 04 85 00 00 00 00 \tlea    eax,[eax*4+0x0]\n
   b2363:\t50                   \tpush   eax\n
"""
    rows = parse_listing(text)
    kinds = {(r["mnemonic"], r["kind"]) for r in rows}
    if ("fld", "read") not in kinds:
        bad.append("a load was not a read: %r" % (sorted(kinds),))
    if ("mov", "read") not in kinds:
        bad.append("a register copy from memory was not a read")
    if ("fstp", "write") not in kinds:
        bad.append("an x87 store was not a write")
    if ("call", "read") not in kinds:
        bad.append("call [reg+disp] is a read of the pointer")
    if ("add", "read-write") not in kinds:
        bad.append("a locked add to memory was not a read-write")
    if any(r["mnemonic"] == "lea" for r in rows):
        bad.append("lea computes an address and names no memory: it must not be a row")
    if any(r["mnemonic"] == "push" for r in rows):
        bad.append("push eax names no memory: it must not be a row")
    ops = split_operands("QWORD PTR [edx+eax*1+0x8], esi")
    if ops != ["QWORD PTR [edx+eax*1+0x8]", "esi"]:
        bad.append("the bracket-aware split: %r" % (ops,))
    if where("QWORD PTR [esp+0x14]") != "stack" or where("QWORD PTR [eax+edx*8]") != "object":
        bad.append("the stack/object split: %r %r" % (where("QWORD PTR [esp+0x14]"),
                                                      where("QWORD PTR [eax+edx*8]")))
    if "| `0x0000b2230` X | 1 | 1 | 0 | 0 |" not in render([{"body_va": "0x0000b2230", "body_name": "X",
                                          "insn_va": "0x1", "mnemonic": "nop",
                                          "operands": "", "mem_operand": "", "kind": "read"}]):
        bad.append("render: the per-body row is missing")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        rows2 = [{"body_va": "0x1", "body_name": "X", "insn_va": "0x2", "mnemonic": "fstp",
                  "operands": "QWORD PTR [eax]", "mem_operand": "QWORD PTR [eax]", "kind": "write",
                  "target": "object"}]
        write_artifact(rows2, path)
        if read_artifact(path) != rows2:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL lipsinc-sampler-ops: " + "; ".join(bad))
        return 1
    print("ok   lipsinc-sampler-ops: a load is a read, an x87 store is a write, a locked add is a "
          "read-write, and lea/push name no memory")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("--body", action="append", dest="bodies",
                    help="body address (default 0x000b2230 and 0x000b24f0)")
    ap.add_argument("-o", "--out", default=None, help="write inventory/lipsinc_sampler_ops.csv here")
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

        rows = census(Xbe(args.xbe), args.bodies or DEFAULT_BODIES)
        if args.out:
            path = os.path.join(args.out, "lipsinc_sampler_ops.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL lipsinc-sampler-ops: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL lipsinc-sampler-ops: %s is stale against the committed artifact" % path)
                return 1
            print("ok   lipsinc-sampler-ops: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
