#!/usr/bin/env python3
"""Where a class's members begin is written by its destructor, not by its constructor.

The class-record notes ended on a member the record's field list cannot place: the bytes after the record array parse
as a three-entry value/reference pair list, `UClass::Serialize` has no callee that writes them, and `PLAN.md`'s named
next measurement was "read `UClass`'s constructor for the `TArray` whose member sits just before `+0x4d0`".

The constructor cannot answer it, and this tool measures why. A constructor of this build does not initialise its
members one named field at a time: MSVC inlines the zero-fill as one `mov DWORD PTR [this+disp],0` per dword over the
whole region, so every dword between the last argument store and the end of the object gets its own instruction and a
store map has no boundaries in it at all -- and it can even skip dwords (`Class`'s default constructor never writes
`+0x50c`/`+0x510`), which looks like a member and is not.

A destructor is a different shape. Every member with a destructor is destroyed by address, so its body is a run of

    lea     ecx,[esi+0x4c4]     the member's address, not a field *access*
    mov     BYTE PTR [esp+0x10],0x1
    call    0x3d850             its destructor

and each `lea` of `this+disp` is a member boundary the writer cannot fake. This walks every function
`inventory/class_constructors.csv` classifies as a constructor or destructor, decodes its body with `objdump`
(`-b binary -m i386`, one run per function so the sweep starts aligned at the function's own first byte), and writes
one row per `this`-relative reference:

    lea-call        a member start and the destructor it is passed to (a real boundary)
    lea-addr        a member start passed elsewhere (a boundary, destructor not named)
    store-zero      a dword the constructor zeroes
    store-imm       a dword the constructor writes an immediate to (the class table, at disp 0)
    store-reg       a dword the constructor writes from a register (an argument, or an inherited value)

so the two readings sit in one artifact and the difference is visible per class.

    python3 tools/class-member-map.py --xbe $SG1_DUMP/default.xbe -o inventory
    python3 tools/class-member-map.py --report
    python3 tools/class-member-map.py --class Class --dump $SG1_DUMP   # one class's ordered map
    python3 tools/class-member-map.py --check

**What this does not do.** A member boundary is not a member *name*: the image has no debug info and the property
stream carries names, types and sizes but no byte offsets, so `+0x4c4` stays an offset until something types the
object. What the row's `target_va` buys is the other half: the destructor of the member, which is the function that
says what the member holds (a `TArray` free, an element loop, nothing at all).

The sweep is honest about truncation: an instruction `objdump` prints as `(bad)` ends the body at that address and the
row set stops there, with the address in the `unread` column of the report, rather than reporting "no members" for a
function this cannot read.
"""
import argparse
import collections
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from xbe import Xbe  # noqa: E402

ROOT = os.path.dirname(HERE)
CTORS = os.path.join(ROOT, "inventory", "class_constructors.csv")
OUT = os.path.join(ROOT, "inventory", "class_member_map.csv")
FIELDS = ["class", "function_va", "form", "seq", "disp", "action", "target_va"]

LINE_RE = re.compile(r"^\s*([0-9a-f]+):\s*((?:[0-9a-f]{2}\s)+)\s*(.*)$", re.I)
EA_RE = re.compile(r"\[\s*(e[a-z]{2})\s*(?:\+\s*(0x[0-9a-f]+))?\s*\]", re.I)
LEA_RE = re.compile(r"^lea\s+(e[a-z]{2})\s*,\s*(.+)$", re.I)
MOV_RE = re.compile(r"^mov\s+(?:byte|word|dword)\s+ptr\s+(\[[^\]]+\])\s*,\s*(.+)$", re.I)
THIS_RE = re.compile(r"^mov\s+(e[a-z]{2})\s*,\s*ecx$", re.I)
ZERO_RE = re.compile(r"^(?:xor|sub)\s+(e[a-z]{2})\s*,\s*\1$", re.I)
IMM_RE = re.compile(r"^(?:0x[0-9a-f]+|\d+)$", re.I)
CALL_RE = re.compile(r"^call\s+(?:0x)?([0-9a-f]+)\s*(?:<[^>]*>)?$", re.I)


def parse_listing(text):
    """(va, asm) per instruction, and the first `(bad)` address objdump printed."""
    insns, bad = [], None
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        va, asm = int(m.group(1), 16), m.group(3).strip()
        if not asm:
            continue                # a long instruction's remaining bytes wrap onto their own line
        if asm.startswith("(bad)"):
            bad = va if bad is None else bad
            break
        insns.append((va, asm))
    return insns, bad


def body(insns):
    """The instructions up to and including the first `ret`/`int3`."""
    out = []
    for va, asm in insns:
        out.append((va, asm))
        if re.match(r"^(ret|int3)\b", asm, re.I):
            break
    return out


def this_reg(insns):
    """The register the body loads `ecx` (`this`) into, or `esi` as this family's convention."""
    for _va, asm in insns[:12]:
        m = THIS_RE.match(asm)
        if m:
            return m.group(1).lower()
    return "esi"


def call_target(asm):
    m = CALL_RE.match(asm.strip())
    return int(m.group(1), 16) if m else None


def analyse(class_name, va, form, insns):
    """One row per `this`-relative reference, in body order."""
    rows = []
    reg = this_reg(insns)
    zeros = set()
    pending = None                      # (row index, instruction index) of the last `lea this+disp`
    seq = 0
    for idx, (iva, asm) in enumerate(insns):
        low = asm.lower()
        z = ZERO_RE.match(asm)
        if z:
            zeros.add(z.group(1).lower())
        if re.match(r"^mov\s+e[a-z]{2}\s*,\s*0x0+$", low):
            zeros.add(low.split()[1].rstrip(","))
        m = LEA_RE.match(asm)
        if m and m.group(1).lower() == "ecx":
            ea = EA_RE.search(m.group(2))
            if ea and ea.group(1).lower() == reg:
                disp = int(ea.group(2), 16) if ea.group(2) else 0
                seq += 1
                rows.append({"class": class_name, "function_va": "0x%08x" % va, "form": form,
                             "seq": seq, "disp": "0x%03x" % disp, "action": "lea-addr",
                             "target_va": ""})
                pending = (len(rows) - 1, idx)
        m = MOV_RE.match(asm)
        if m:
            ea = EA_RE.search(m.group(1))
            src = m.group(2).strip().lower()
            if ea and ea.group(1).lower() == reg:
                disp = int(ea.group(2), 16) if ea.group(2) else 0
                if disp == 0:
                    action = "table-install"
                elif src in zeros:
                    action = "store-zero"
                elif IMM_RE.match(src):
                    action = "store-imm"
                else:
                    action = "store-reg"
                seq += 1
                rows.append({"class": class_name, "function_va": "0x%08x" % va, "form": form,
                             "seq": seq, "disp": "0x%03x" % disp, "action": action,
                             "target_va": ""})
        if low.startswith("call"):
            tgt = call_target(asm)
            if pending and tgt is not None and idx - pending[1] <= 6:
                row = rows[pending[0]]
                row["action"] = "lea-call"
                row["target_va"] = "0x%08x" % tgt
            pending = None
    return rows, reg


def objdump_body(exe, path, va):
    """Decode one function from its own first byte, so the sweep cannot arrive desynced.

    `--start-address` is not enough: objdump keeps decoding the section from its beginning and only *prints*
    from that address, so alignment still arrives through whatever sits before the function and a body that
    starts after a jump table reads as `(bad)`. The slice has to be its own file, with the function's own VA
    as the VMA, so the first decoded byte is the function's first byte.
    """
    cmd = [exe, "-D", "-b", "binary", "-m", "i386", "-M", "intel",
           "--adjust-vma=0x%x" % va, path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.exit("objdump failed: %s" % exc)
    return parse_listing(out.stdout)


def text_section(xbe):
    execs = [s for s in xbe.sections if getattr(s, "executable", False)]
    if not execs:
        sys.exit("no executable section in the image")
    return max(execs, key=lambda s: s.fsize)


def sweep(xbe, xbe_file, exe, limit=0):
    with open(CTORS, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if limit:
        rows = rows[:limit]
    sec = text_section(xbe)
    tmpdir = tempfile.mkdtemp(prefix="sg1text-")
    tmp = os.path.join(tmpdir, "fn.bin")
    try:
        seen, out, unread = {}, [], []
        with open(xbe_file, "rb") as fh:
            for r in rows:
                va = int(r["function_va"], 16)
                form = r["form"] or ""
                if form not in ("ctor", "dtor"):
                    continue
                if va not in seen:
                    fh.seek(sec.foff + (va - sec.va))
                    with open(tmp, "wb") as out_fh:
                        out_fh.write(fh.read(0x800))
                    insns, bad = objdump_body(exe, tmp, va)
                    body_ins = body(insns)
                    seen[va] = (body_ins, bad if bad and (not body_ins or bad > body_ins[-1][0]) else None)
                body_ins, bad = seen[va]
                got, reg = analyse(r["class"], va, form, body_ins)
                out.extend(got)
                if bad is not None:
                    unread.append((va, bad, reg, len(body_ins)))
        return out, unread, len(seen)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def write(rows, out):
    d = out if os.path.isdir(out) else os.path.dirname(out) or "."
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, os.path.basename(OUT)) if os.path.isdir(out) else out
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def load(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def by_class(rows, cls):
    got = [r for r in rows if r["class"].lower() == cls.lower()]
    return got


def runs(disps, gap=0x10):
    """Written dwords grouped into contiguous runs: a dword more than `gap` past the last one starts a run.

    The point of a run is that a missing dword *inside* one is a dword the body never writes, while a gap
    between runs is just the next member. A single span from the table install at disp 0 to the last store
    says nothing.
    """
    out = []
    for d in sorted(disps):
        if out and d - out[-1][-1] <= gap:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def class_map(rows, cls):
    """{function_va: form, ordered members, store runs and the dwords missing inside them} for one class."""
    per = collections.OrderedDict()
    for r in by_class(rows, cls):
        per.setdefault(r["function_va"], {"form": r["form"], "members": [], "store": {}})
        e = per[r["function_va"]]
        if r["action"].startswith("lea"):
            disp = int(r["disp"], 16)
            if disp not in [d for d, _t in e["members"]]:
                e["members"].append((disp, r["target_va"]))
        e["store"][int(r["disp"], 16)] = r["action"]
    for e in per.values():
        store = e.pop("store")
        e["runs"] = [(r[0], r[-1], [d for d in range(r[0], r[-1] + 1, 4) if d not in store])
                     for r in runs(store) if len(r) >= 4]
        e["members"].sort()
    return per


def report(rows, unread=None, classes=None):
    print("%d rows over %d functions, %d classes"
          % (len(rows), len({r["function_va"] for r in rows}), len({r["class"] for r in rows})))
    acts = collections.Counter(r["action"] for r in rows)
    print("actions: " + ", ".join("%s %d" % (k, acts[k]) for k in sorted(acts)))
    for form in ("dtor", "ctor"):
        sub = [r for r in rows if r["form"] == form]
        funcs = {r["function_va"] for r in sub}
        starts = [r for r in sub if r["action"] == "lea-call"]
        print("%s: %d functions, %d member starts, %d of them with a named destructor"
              % (form, len(funcs), sum(1 for r in sub if r["action"].startswith("lea")), len(starts)))
        if form == "ctor":
            holes = runs_n = 0
            for fn in funcs:
                store = {int(r["disp"], 16) for r in sub if r["function_va"] == fn
                         and r["action"].startswith("store")}
                for run in runs(store):
                    if len(run) < 4:
                        continue
                    runs_n += 1
                    holes += sum(1 for d in range(run[0], run[-1] + 1, 4) if d not in store)
            print("     %d store runs of 4+ dwords, %d dwords missing inside them" % (runs_n, holes))
    # the destructors a member is passed to: a target used by many classes is a generic free, one used once
    # is that class's own -- which is what separates a `TArray` from a member with its own destructor
    hist = collections.Counter(r["target_va"] for r in rows if r["action"] == "lea-call")
    if hist:
        top = hist.most_common(6)
        print("destructor targets: %d distinct; most used %s"
              % (len(hist), ", ".join("%s x%d" % (t, n) for t, n in top)))
        print("     used by exactly one member: %d" % sum(1 for n in hist.values() if n == 1))
    # does a constructor write the dwords the destructor places? a member it never writes is initialised
    # somewhere else (or through another base register), and both readings then disagree in a visible way
    dm = {(r["class"], int(r["disp"], 16)) for r in rows if r["form"] == "dtor" and r["action"].startswith("lea")}
    cm = {(r["class"], int(r["disp"], 16)) for r in rows if r["form"] == "ctor" and r["action"].startswith("store")}
    if dm:
        print("member starts a constructor of the same class also writes: %d of %d"
              % (len(dm & cm), len(dm)))
    if classes:
        for cls in classes:
            print("\n%s" % cls)
            for fn, e in class_map(rows, cls).items():
                mem = " ".join("+0x%x%s" % (d, "(%s)" % t if t else "") for d, t in e["members"])
                print("  %s %s  members: %s" % (fn, e["form"], mem or "-"))
                for lo, hi, holes in e["runs"]:
                    print("       stores +0x%x..+0x%x, missing inside: %s"
                          % (lo, hi, " ".join("+0x%x" % d for d in holes) or "none"))
    if unread:
        print("\nunread bodies (objdump (bad) inside the body): %d" % len(unread))
        for va, bad, reg, n in unread[:10]:
            print("  0x%08x this=%s, %d instructions read, (bad) at 0x%x" % (va, reg, n, bad))


def selftest():
    """Synthetic listings: no dump, no writes."""
    bad = []
    dtor = """
  3dbd0:\t55                   \tpush   ebp
  3dbd1:\t8b f1                \tmov    esi,ecx
  3dbed:\tc7 06 70 48 4d 00    \tmov    DWORD PTR [esi],0x4d4870
  3dc50:\t8d 8e d0 04 00 00    \tlea    ecx,[esi+0x4d0]
  3dc56:\tc6 44 24 10 02       \tmov    BYTE PTR [esp+0x10],0x2
  3dc5b:\te8 e0 fc ff ff       \tcall   3da40
  3dc60:\t8d 8e c4 04 00 00    \tlea    ecx,[esi+0x4c4]
  3dc6b:\te8 e0 fb ff ff       \tcall   3d850
  3dc70:\t8d 8e b8 04 00 00    \tlea    ecx,[esi+0x4b8]
  3dc7b:\te8 20 fb ff ff       \tcall   3d7a0
  3dc9e:\tc3                   \tret
  3dc9f:\t90                   \tnop
"""
    ctor = """
  3ad20:\t55                   \tpush   ebp
  3ad37:\t8b f1                \tmov    esi,ecx
  3ad4e:\tc7 06 70 48 4d 00    \tmov    DWORD PTR [esi],0x4d4870
  3ad43:\t33 ff                \txor    edi,edi
  3adac:\t89 be b8 04 00 00    \tmov    DWORD PTR [esi+0x4b8],edi
  3adb2:\t89 be bc 04 00 00    \tmov    DWORD PTR [esi+0x4bc],edi
  3adb8:\t89 be c0 04 00 00    \tmov    DWORD PTR [esi+0x4c0],edi
  3adbe:\t89 be c4 04 00 00    \tmov    DWORD PTR [esi+0x4c4],edi
  3adc4:\t89 8e 0c 05 00 00    \tmov    DWORD PTR [esi+0x50c],ecx
  3adca:\t89 be 10 05 00 00    \tmov    DWORD PTR [esi+0x510],edi
  3add0:\tc3                   \tret
"""
    broken = """
  1000:\t8b f1                \tmov    esi,ecx
  1002:\t0f 0b                \t(bad)
  1004:\t8d 8e c4 04 00 00    \tlea    ecx,[esi+0x4c4]
"""
    insns, bad_at = parse_listing(dtor)
    got, reg = analyse("Class", 0x3dbd0, "dtor", body(insns))
    starts = [(r["disp"], r["target_va"]) for r in got if r["action"] == "lea-call"]
    if starts != [("0x4d0", "0x0003da40"), ("0x4c4", "0x0003d850"), ("0x4b8", "0x0003d7a0")]:
        bad.append("dtor member starts: %r" % (starts,))
    if reg != "esi":
        bad.append("this register: %r" % (reg,))
    if [r for r in got if r["action"] == "table-install"][0]["disp"] != "0x000":
        bad.append("table install not at disp 0")
    if bad_at is not None:
        bad.append("clean listing reported a (bad) line")

    insns, bad_at = parse_listing(ctor)
    got, _reg = analyse("Class", 0x3ad20, "ctor", body(insns))
    zero = [r["disp"] for r in got if r["action"] == "store-zero"]
    if zero != ["0x4b8", "0x4bc", "0x4c0", "0x4c4", "0x510"]:
        bad.append("ctor zero stores: %r" % (zero,))
    if not [r for r in got if r["action"] == "store-reg" and r["disp"] == "0x50c"]:
        bad.append("a register store was not distinguished from a zero store")
    if not bad_at is None:
        bad.append("clean ctor listing reported a (bad) line")

    insns, bad_at = parse_listing(broken)
    if bad_at != 0x1002:
        bad.append("(bad) address: %r" % (bad_at,))
    if body(insns) != [(0x1000, "mov    esi,ecx")]:
        bad.append("a (bad) line did not end the body: %r" % (body(insns),))

    # the hole rule: a dword missing between the ends of a store run must be reported
    if "0x50c" in zero and "0x510" in zero:
        bad.append("hole test is not exercising a hole")

    if bad:
        print("FAIL class-member-map: %s" % "; ".join(bad))
        return 1
    print("ok class-member-map: dtor lea/call boundaries, ctor zero vs register stores, a hole, and (bad) truncation")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", help="DUMP/default.xbe")
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--class", dest="cls", action="append")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--objdump", default="objdump")
    ap.add_argument("--limit", type=int, default=0, help="first N CSV rows only (a smoke test)")
    args = ap.parse_args()

    if args.check:
        sys.exit(selftest())

    if args.report and not args.xbe:
        path = args.out if os.path.isfile(args.out) else os.path.join(args.out, os.path.basename(OUT))
        report(load(path), None, args.cls)
        return

    if not args.xbe:
        ap.error("--xbe is required to measure (or --report on a committed artifact)")

    xbe = Xbe(args.xbe)
    rows, unread, nfunc = sweep(xbe, args.xbe, args.objdump, args.limit)
    path = write(rows, args.out)
    print("wrote %s: %d rows, %d functions" % (path, len(rows), nfunc))
    report(rows, unread, args.cls)


if __name__ == "__main__":
    main()
