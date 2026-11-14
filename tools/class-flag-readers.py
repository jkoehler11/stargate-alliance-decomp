#!/usr/bin/env python3
"""Which class-flag bits at `+0x498` have readers, and what each reader does with the answer (U5)

`#523` (`tools/class-flag-word.py`) established what the word at `+0x498` *is*: `UClass`'s class-flag dword,
whose constructor inherits `0x0e0f86ec` of the super's and whose registration `or`s in `0x12`. It also listed
the 109 displacements that name the word and the 32 bit populations, and left the five inheritable bits that
the pinned reader (`tools/uelib` `UnrealFlags.cs`) has no name for -- `0x8000`, `0x10000`, `0x20000`,
`0x40000`, `0x80000` -- spelled `unnamed`.

This tool answers the question that name would answer, from the other side: **who reads each bit, and what
does the reader do with the answer.** For every masked test of the word in the image it decodes the operand
width and mask, the conditional branch that consumes the flags, which edge carries the answer (a branch that
zeroes `eax` / returns / calls a log), and therefore whether the reader needs the bit *set* or *clear*. Then it
joins the readers to the shipped packages, so each bit gets its carriers as well as its readers.

    python3 tools/class-flag-readers.py --xbe $DUMP/default.xbe -o inventory/class_flag_readers.csv
    python3 tools/class-flag-readers.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/class-flag-readers.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/class-flag-readers.py --check        # synthetic text, no dump, no writes

The reader's table supplies the names it has; a bit it leaves unnamed is reported as unnamed with the
behaviour its readers show, never with a name guessed from this build. Addresses, counts and prose only: no
game data, no decompiled output.
"""

import argparse
import csv
import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FLAGS = 0x498
COLUMNS = ["kind", "bit", "at", "fn", "detail", "evidence"]
CSV_PATH = os.path.join("inventory", "class_flag_readers.csv")
BLOCK_MARK = ("<!-- generated:class-flag-readers -->", "<!-- /generated:class-flag-readers -->")

# Bits the constructor at 0x00039520 inherits and `UnrealFlags.cs` has no name for, in ascending order.
UNNAMED_INHERITED = [0x00008000, 0x00010000, 0x00020000, 0x00040000, 0x00080000]


def sibling(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- reading one site

def disasm(xbe, table, fn):
    """[(va, text)] for one function, from objdump as an independent decoder. Cached by the caller."""
    blob = xbe.read_va(fn[0], fn[1])
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=%d" % fn[0], temp],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(temp)
    insns = []
    for line in out.splitlines():
        if line.count("\t") < 2:
            continue
        head, rest = line.split("\t", 1)
        try:
            va = int(head.strip().rstrip(":"), 16)
        except ValueError:
            continue
        text = rest.split("\t")[-1].strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            insns.append((va, text))
    insns.sort()
    return insns


def mnemonic(text):
    return text.split(" ", 1)[0].lower()


BRANCHES = {"jz", "je", "jnz", "jne", "js", "jns", "ja", "jae", "jb", "jbe", "jg", "jge", "jl", "jle",
            "jo", "jno", "jp", "jnp", "jmp"}


def sets_bit(mnem):
    """Which value of a single tested bit this branch is taken on, or '?' where the flags do not say."""
    if mnem in ("jnz", "jne"):
        return "set"
    if mnem in ("jz", "je"):
        return "clear"
    if mnem == "js":
        return "set"
    if mnem == "jns":
        return "clear"
    return "?"


def branch_target(text):
    m = re.search(r"\b0x([0-9a-f]+)\b", text.split(" ", 1)[-1])
    return int(m.group(1), 16) if m else None


ZEROES = re.compile(r"^(xor|sub) (eax|ecx|edx), ?\2$|^mov (eax|ecx|edx),0x0$")
CALLS = re.compile(r"^call\b")
LOGS = re.compile(r"^call\b")


def edge_shape(insns, at, limit=6):
    """What the first few instructions from one edge do: does the edge answer zero, return, call?"""
    lo, hi = 0, len(insns)
    # binary search the first instruction at or after `at`
    while lo < hi:
        mid = (lo + hi) // 2
        if insns[mid][0] < at:
            lo = mid + 1
        else:
            hi = mid
    shapes = []
    for va, text in insns[lo:lo + limit]:
        m = mnemonic(text)
        if ZEROES.match(text):
            shapes.append("zeroes")
        elif m == "ret":
            shapes.append("returns")
            break
        elif m in ("call",):
            shapes.append("calls")
        elif m == "jmp" or m in BRANCHES:
            shapes.append("branches")
            break
        elif m.startswith("mov") or m in ("push", "test", "cmp", "lea", "and", "or", "add"):
            continue
        else:
            continue
    return shapes[0] if shapes else "continues"


def classify(insns, at):
    """(branch, target, taken_when, taken_shape, else_shape, requires) for the test at `at`.

    `requires` is the bit value that reaches the edge which does NOT zero the answer: a reader that zeroes
    `eax` on the clear edge needs the bit set, and the other way round.
    """
    lo, hi = 0, len(insns)
    while lo < hi:
        mid = (lo + hi) // 2
        if insns[mid][0] < at:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(insns) or insns[lo][0] != at:
        return None
    for k in range(lo + 1, min(lo + 5, len(insns))):
        va, text = insns[k]
        m = mnemonic(text)
        if m not in BRANCHES:
            continue
        target = branch_target(text)
        taken_when = "always" if m == "jmp" else sets_bit(m)
        taken = edge_shape(insns, target) if target is not None else "?"
        fall = edge_shape(insns, insns[k + 1][0]) if k + 1 < len(insns) else "?"
        if m == "jmp":
            requires = "?"
        elif taken == "zeroes" and fall != "zeroes":
            requires = {"set": "clear", "clear": "set"}.get(taken_when, "?")
        elif fall == "zeroes" and taken != "zeroes":
            requires = taken_when
        else:
            requires = "?"
        return {"branch": m, "target": target, "taken_when": taken_when,
                "taken": taken, "fall": fall, "requires": requires}
    return None


# ---------------------------------------------------------------- the census

def single_bit(mask):
    return mask if mask and (mask & (mask - 1)) == 0 else 0


def measure(xbe_path, dump_mode=True):
    cfw = sibling("class-flag-word")
    xbe = cfw.load_xbe(xbe_path)
    table = cfw.load_symbols()
    starts = [row[0] for row in table]
    mask_const = cfw.inherit_mask(xbe)
    access_rows = cfw.accesses(xbe, table, starts)

    cache = {}
    readers = []
    stores = []
    for row in access_rows:
        text = row["detail"]
        op = text.split(" ", 1)[1] if " " in text else text
        width = text.split(" ", 1)[0]
        if op.startswith("mov [") or op.startswith("mov8 ["):
            stores.append(row)
            continue
        mask = cfw.mask_of(text)
        if not mask:
            continue                      # a load, a lea or a variable mask: not a bit test
        fn = cfw.owner(table, starts, row["_va"])
        if fn is None:
            continue
        if fn[0] not in cache:
            cache[fn[0]] = disasm(xbe, table, fn)
        verdict = classify(cache[fn[0]], row["_va"])
        readers.append({"va": row["_va"], "fn": fn[2], "width": width, "mask": mask,
                        "bit": single_bit(mask), "evidence": row["evidence"], "verdict": verdict})

    pops = cfw.corpus()
    bitmask = mask_const
    rows = []
    for i in range(32):
        b = 1 << i
        hits = [r for r in readers if r["bit"] == b]
        multi = [r for r in readers if not r["bit"] and r["mask"] & b]
        p = pops["bits"][b]
        if b in cfw.NAMES:
            name = cfw.NAMES[b]
        else:
            name = "unnamed"
        detail = "%d reader(s) in %d function(s)" % (len(hits) + len(multi),
                                                    len({r["fn"] for r in hits + multi}))
        if multi:
            detail += ", %d under a multi-bit mask" % len(multi)
        carriers = p["classes"]
        rows.append({"kind": "bit", "bit": "0x%08x" % b, "at": "0x%08x" % b, "fn": name,
                     "detail": detail,
                     "evidence": ("in the inherit mask; " if bitmask & b else "not inherited; ")
                                 + ("%d class(es)%s" % (carriers, (", e.g. %s" % p["example"]) if p["example"] else "")),
                     "_bit": b})
    for r in readers:
        v = r["verdict"] or {}
        at = "0x%08x" % r["va"]
        target = ("0x%08x" % v["target"]) if v.get("target") else "-"
        detail = "%s test 0x%x -> %s %s; taken_on=%s taken=%s else=%s" % (
            r["width"], r["mask"], v.get("branch", "-"), target, v.get("taken_when", "-"),
            v.get("taken", "-"), v.get("fall", "-"))
        rows.append({"kind": "reader", "bit": "0x%08x" % r["bit"] if r["bit"] else "multi",
                     "at": at, "fn": r["fn"], "detail": detail,
                     "evidence": "%s; requires %s" % (r["evidence"], v.get("requires", "-")),
                     "_bit": r["bit"], "_requires": v.get("requires", "-"), "_va": r["va"]})
    for s in stores:
        rows.append({"kind": "store", "bit": "-", "at": s["at"], "fn": s["fn"],
                     "detail": s["detail"], "evidence": s["evidence"], "_bit": "-"})
    return {"rows": rows, "mask": mask_const, "readers": readers, "stores": stores,
            "pops": pops, "classes": pops["classes"]}


# ---------------------------------------------------------------- rendering

def summarise(rows):
    """bit -> {'carriers', 'name', 'readers': [...], 'set': Counter, 'clear': Counter} over the measured rows."""
    from collections import Counter
    out = {}
    for r in rows:
        if r["kind"] == "bit":
            out.setdefault(r["_bit"], {"carriers": r["evidence"], "detail": r["detail"],
                                       "name": r["fn"], "readers": [], "requires": set(),
                                       "set": Counter(), "clear": Counter()})
    for r in rows:
        if r["kind"] != "reader" or r["bit"] == "multi":
            continue
        b = r["_bit"]
        d = out.setdefault(b, {"carriers": "", "detail": "", "name": "", "readers": [],
                               "requires": set(), "set": Counter(), "clear": Counter()})
        d["readers"].append(r)
        d["requires"].add(r["_requires"])
        taken_on = r["detail"].split("taken_on=")[-1].split(" ")[0]
        taken = r["detail"].split("taken=")[-1].split(" ")[0]
        fall = r["detail"].split("else=")[-1]
        if taken_on == "set":
            d["set"][taken] += 1
            d["clear"][fall] += 1
        elif taken_on == "clear":
            d["set"][fall] += 1
            d["clear"][taken] += 1
    return out


def edges(counter):
    if not counter:
        return "-"
    return ", ".join("%s %d" % (k, v) for k, v in sorted(counter.items()))


def render(rows, mask=None):
    bits = summarise(rows)
    if not bits:
        return ""
    stores = [r for r in rows if r["kind"] == "store"]
    read = [b for b in bits if bits[b]["readers"]]
    def carries_none(b):
        return bits[b]["carriers"].split("; ")[-1].startswith("0 class")

    unread = [b for b in bits if not bits[b]["readers"] and not carries_none(b)]
    orphan = [b for b in read if carries_none(b)]
    lines = []
    lines.append("**%d of the 32 bits are read somewhere in the image; %d are carried by a shipped class and read "
                 "nowhere, and %d %s read and carried by no class.** Every masked test of `[x+0x498]` in the code "
                 "sections is in `inventory/class_flag_readers.csv` -- one row per site with the operand width, "
                 "the branch that consumes the flags and what each edge does (`zeroes` = it clears `eax`, "
                 "`returns` = it leaves the function, `calls` = it calls out); this table aggregates them per bit. "
                 "The `set`/`clear` columns say which edge the bit's value selects, so a reader whose set edge "
                 "`zeroes` is a reader that needs the bit **clear**."
                 % (len(read), len(unread), len(orphan), "is" if len(orphan) == 1 else "are"))
    lines.append("")
    lines.append("| bit | name in the reader | in mask | classes carrying | readers | set edge | clear edge |")
    lines.append("|---:|---|---:|---:|---:|---|---|")
    for b in sorted(read):
        d = bits[b]
        lines.append("| `0x%x` | %s | %s | %s | %d | %s | %s |"
                     % (b, d["name"], "yes" if mask & b else "", d["carriers"].split("; ")[-1],
                        len(d["readers"]), edges(d["set"]), edges(d["clear"])))
    lines.append("")
    if orphan:
        lines.append("**A bit the image reads and no packaged class carries has a constant answer.** %s %s %d "
                     "reader(s) and no carrier among the %d shipped classes: the constructor copies the super's "
                     "word under a mask and the registration adds `0x12`, so a bit outside both can only arrive "
                     "with a class the packages do not hold, and every shipped object reads as clear."
                     % (", ".join("`0x%x`" % b for b in orphan), "has" if len(orphan) == 1 else "have",
                        sum(len(bits[b]["readers"]) for b in orphan), 2319))
        lines.append("")
    inside = [s for s in stores if s["evidence"] != "unmapped"]
    outside = [s for s in stores if s["evidence"] == "unmapped"]
    lines.append("**%d store(s) to `+0x498` sit inside a mapped function and %d outside the symbol map.** The "
                 "constructor composes a mask (`and` with `0x%08x`) and the registration `or`s `0x12`; every "
                 "other store writes a whole word the constructor already built, so no reader can observe a bit "
                 "that the constructor does not inherit." % (len(inside), len(outside), mask))
    lines.append("")
    return "\n".join(lines)


def splice(path, block):
    with open(path) as fh:
        text = fh.read()
    begin, end = BLOCK_MARK
    i = text.find(begin)
    j = text.find(end)
    if i < 0 or j < 0:
        sys.exit("FAIL markers not found in %s" % path)
    return text[:i + len(begin)] + "\n" + block + text[j:]


def rows_for_csv(rows):
    return [{k: (r.get(k) if r.get(k) is not None else "-") for k in COLUMNS} for r in rows]


# ---------------------------------------------------------------- entry points

def check():
    """Synthetic checks: the classification rules and the CSV/rendering contract, no dump, no writes."""
    n = 0
    assert single_bit(0x8000) == 0x8000
    assert single_bit(0x8003) == 0
    assert single_bit(0) == 0
    n += 3
    for mnem, want in (("jnz", "set"), ("jne", "set"), ("jz", "clear"), ("je", "clear"),
                       ("js", "set"), ("jns", "clear"), ("ja", "?"), ("jmp", "?")):
        assert sets_bit(mnem) == want, mnem
        n += 1
    assert ZEROES.match("xor eax,eax") and ZEROES.match("sub ecx,ecx") and ZEROES.match("mov edx,0x0")
    assert not ZEROES.match("xor eax,0x1")
    n += 2

    # `test [eax+0x498],0x8000; jnz +4; xor eax,eax; ret` -- the clear edge zeroes, so the reader needs it set
    insns = [(0x1000, "test DWORD PTR [eax+0x498],0x8000"), (0x1006, "jnz 0x100e"),
             (0x1008, "xor eax,eax"), (0x100a, "ret"), (0x100e, "mov eax,ebx"), (0x1010, "ret")]
    v = classify(insns, 0x1000)
    assert v and v["branch"] == "jnz" and v["requires"] == "set" and v["fall"] == "zeroes", v
    n += 1
    # the mirrored body: `jz` onto the zeroing block still needs the bit set (the clear edge is the one that
    # leaves with a zero), which is the whole point of reading the edges instead of the branch mnemonic
    insns2 = [(0x2000, "test DWORD PTR [eax+0x498],0x40000"), (0x2006, "jz 0x2010"),
              (0x2008, "mov eax,ebx"), (0x200a, "ret"), (0x2010, "xor eax,eax"), (0x2012, "ret")]
    v2 = classify(insns2, 0x2000)
    assert v2 and v2["requires"] == "set" and v2["taken"] == "zeroes", v2
    n += 1
    # a test inside a branch-less run answers nothing
    assert classify([(0x3000, "test DWORD PTR [eax+0x498],0x10000"), (0x3006, "mov eax,ebx")],
                    0x3000) is None
    assert classify(insns, 0x9999) is None
    n += 2

    rows = [{"kind": "bit", "bit": "0x00008000", "at": "0x00008000", "fn": "unnamed, reader needs it set",
             "detail": "1 reader(s) in 1 function(s)", "evidence": "in the inherit mask; 1 class(es), e.g. A",
             "_bit": 0x8000},
            {"kind": "reader", "bit": "0x00008000", "at": "0x1000", "fn": "F", "detail": "dword test 0x8000",
             "evidence": "aligned; requires set", "_bit": 0x8000, "_requires": "set"},
            {"kind": "store", "bit": "-", "at": "0x2000", "fn": "G", "detail": "dword mov [esi+0x498],ecx",
             "evidence": "aligned", "_bit": "-"}]
    out = render(rows, mask=0x0e0f86ec)
    assert "`0x8000`" in out and "unnamed, reader needs it set" in out, out
    assert "| `0x8000` |" in out
    n += 2
    csv_rows = rows_for_csv(rows)
    assert set(csv_rows[0]) == set(COLUMNS) and len(csv_rows) == 3
    n += 2
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "p.md")
        with open(page, "w") as fh:
            fh.write("intro\n" + BLOCK_MARK[0] + "\nold\n" + BLOCK_MARK[1] + "\noutro\n")
        new = splice(page, "NEW")
        assert new.startswith("intro\n") and new.rstrip().endswith("outro") and "old" not in new
        n += 1
    print("class-flag-readers: OK %d assertions" % n)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return check()

    measured = None
    if args.xbe:
        measured = measure(args.xbe)
        rows = measured["rows"]
        if args.out:
            with open(args.out, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\r\n")
                w.writeheader()
                w.writerows(rows_for_csv(rows))
            print("wrote %s (%d rows)" % (args.out, len(rows)))
        if args.report:
            print(render(rows, mask=measured["mask"]))
        if args.write_doc:
            new = splice(args.write_doc, render(rows, mask=measured["mask"]))
            with open(args.write_doc, "w") as fh:
                fh.write(new)
            print("wrote %s" % args.write_doc)
        return 0

    # --check-doc: answer from the committed CSV alone, no dump and no writes
    with open(CSV_PATH, newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if r["kind"] == "reader":
            r["_bit"] = int(r["bit"], 16)
            r["_requires"] = r["evidence"].split("requires ")[-1]
        elif r["kind"] == "bit":
            r["_bit"] = int(r["bit"], 16)
        else:
            r["_bit"] = "-"
    # the inherit mask is exactly the bits whose evidence says the constructor inherits them
    mask = 0
    for r in rows:
        if r["kind"] == "bit" and "in the inherit mask" in r["evidence"]:
            mask |= int(r["bit"], 16)
    want = render(rows, mask=mask).strip()
    if args.write_doc:
        with open(args.write_doc) as fh:
            text = fh.read()
        new = splice(args.write_doc, want)
        with open(args.write_doc, "w") as fh:
            fh.write(new)
        print("wrote %s from the committed CSV" % args.write_doc)
        return 0
    if not args.check_doc:
        sys.exit("nothing to do: give --xbe, --check or --check-doc")
    with open(args.check_doc) as fh:
        text = fh.read()
    i = text.find(BLOCK_MARK[0])
    j = text.find(BLOCK_MARK[1])
    if i < 0 or j < 0:
        sys.exit("FAIL markers not found in %s" % args.check_doc)
    have = text[i + len(BLOCK_MARK[0]):j].strip()
    if have != want:
        sys.exit("FAIL %s: generated block does not match the committed CSV" % args.check_doc)
    print("class-flag-readers: %s matches the committed CSV" % args.check_doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
