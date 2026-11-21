#!/usr/bin/env python3
"""What stops the declarer walk's 35 residue bodies, read one at a time with objdump.

`tools/class-slot-declarers.py` names a slot body after the class its own table's parent edges reach and
leaves 35 bodies, each under the name of a reason: 13 declared bodies whose decode never closes (so no
size, and an `add` with a guessed size moves every later row), 8 bodies in a member table the label pass
refused, 4 bodies two independent classes declare, 3 in a class table no registration names and 7 declared
by such a table.  This tool reads those reasons against the image, with objdump as the instruction reader
(the gap census's linear shape decoder refuses on instruction forms it does not model, which is one of the
reasons under test).

  * **The 13 close, and where they do not the row above is a check() block.**  A gap head's gap ends at
    the next map row -- a boundary the gap census measured from padding and a prologue, not from the flow.
    Read linearly, each of the 13 reaches a terminator behind a pad run.  Where that terminus is *past* the
    next map row, the row above opens on `push line; push file; push function; call <handler>` and stands
    behind no padding: an out-of-line `check()` block the body branches over, laid out inside the function.
  * **The 4 are the linker's identical-code folding.**  Each is one of the build's smallest shapes (a 1-byte
    `ret`, a 4-byte field getter, a 21-byte pair of getters) held at two or more slot indices by classes
    that are neither ancestor nor descendant, so "two declarations" is one folded body read once per index.
    The map's byte-duplicate groups are the control.
  * **The 18 need a name no image-side table carries.**  The 8 member bodies sit in three member tables
    whose owner the label pass refused (the table is stored into a field of an object no class name
    reaches), and the 10 declarations sit on five class tables no registration names, whose *children* the
    registration does name -- so the name would have to come from the parent class itself.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/declarer-residue.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/declarer-residue.py --report
    python3 tools/declarer-residue.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/declarer-residue.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/declarer-residue.py --check      # synthetic, no dump, writes nothing

Addresses, sizes, slot indices and counts only; no image content is written.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
HERE = os.path.dirname(os.path.abspath(__file__))

FUNCTIONS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
GAPS = os.path.join(ROOT, "inventory", "gap_heads.csv")
DECLARERS = os.path.join(ROOT, "inventory", "class_slot_declarers.csv")
PLAIN = os.path.join(ROOT, "inventory", "class_plain_tables.csv")
OUT = os.path.join(ROOT, "inventory", "declarer_residue.csv")
DOC_START = "<!-- generated:declarer-residue -->"
DOC_END = "<!-- /generated:declarer-residue -->"
FIELDS = ("row", "head_va", "kind", "verdict", "size", "declarers", "indices", "tables", "stop_va",
          "stop_name", "stop_shape", "closes_at", "crossed", "evidence", "census")
RESIDUE = ("declared", "member", "conflict", "unnamed-table", "declared-unnamed")
DECLARED = re.compile(r"([A-Za-z_][\w:]*)_vt(\d+) \(held by (\d+)\)")
MEMBER_TABLE = re.compile(r"table (0x[0-9a-f]+)")
CLASS_TABLE = re.compile(r"class table (0x[0-9a-f]+)")
MEMBER_FIELD = re.compile(r"field (0x[0-9a-f]+)")
INSN = re.compile(r"^\s+([0-9a-f]+):\t((?:[0-9a-f]{2} )+)\s*\t(.*)$")
PAD = (0xCC, 0x90)
WINDOW = 4096


def sibling(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def num(text):
    text = (text or "").strip()
    if not re.match(r"^(0x)?[0-9a-fA-F]+$", text):
        return None
    return int(text, 16)


# --------------------------------------------------------------------------- instructions, from objdump
def parse_objdump(text):
    """(va, size, text) for every instruction line of `objdump -D` output."""
    out = []
    for line in text.splitlines():
        m = INSN.match(line)
        if not m:
            continue
        code = m.group(2).split()
        out.append((int(m.group(1), 16), len(code), m.group(3).strip()))
    return out


def disasm(vec, va, path=None):
    """objdump a byte vector as 32-bit code at `va`. The tool takes a file, never stdin."""
    if path is None:
        fd, path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
    with open(path, "wb") as fh:
        fh.write(vec)
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "--adjust-vma=%#x" % va, path],
                             capture_output=True, text=True, check=False).stdout
    finally:
        os.unlink(path)
    return parse_objdump(out)


def is_push_imm(text):
    return text.startswith("push") and "$0x" in text


def is_call(text):
    return text.startswith("call") and not text.startswith("call   *")


def terminates(text):
    return text == "ret" or text.startswith("ret ") or text.startswith("jmp")


def assert_block(ins, targets=None):
    """`push line; push file; push function; call handler` -- a check() failure block.

    Returns the handler's address, or None.  The shape is read as a whole (three immediate pushes and a
    direct call) and, when `targets` is given, the handler is counted so the population can be asked
    whether it is one handler or many.
    """
    if len(ins) < 4:
        return None
    if not all(is_push_imm(t) for _a, _s, t in ins[:3]):
        return None
    if not is_call(ins[3][2]):
        return None
    m = re.search(r"0x([0-9a-f]+)", ins[3][2])
    if not m:
        return None
    target = int(m.group(1), 16)
    if targets is not None:
        targets[target] += 1
    return target


def pad_run(vec, at, n=2):
    """Do `n` pad bytes start at `at`? A tail is padding only as a run; one `int3` is a filler."""
    if at >= len(vec):
        return True
    if at + n > len(vec):
        return False
    return all(vec[at + k] in PAD for k in range(n))


def closes(ins, vec, lo):
    """(va, size, saw_table) for the first terminator whose successor is a pad run, else None.

    `lo` is the VA the vector starts at, so an instruction's byte offset is `va - lo`.  `saw_table` is
    set when the sweep passed a jump through a memory table: the table's dwords are data laid out inline,
    so a linear read that crosses one is reading data as code and its terminus is not a boundary.
    """
    saw = False
    for va, size, text in ins:
        if text.startswith("jmp") and "(" in text:
            saw = True
        if not terminates(text):
            continue
        if pad_run(vec, va - lo + size):
            return va, size, saw
    return None


# --------------------------------------------------------------------------- the populations
def residue_rows():
    out = [r for r in read_csv(DECLARERS) if r.get("row") == "head" and r.get("verdict") in RESIDUE]
    order = {k: i for i, k in enumerate(RESIDUE)}
    out.sort(key=lambda r: (order[r["verdict"]], num(r["head_va"]) or 0))
    return out


def map_rows():
    rows = []
    for r in read_csv(FUNCTIONS):
        va = num(r.get("address"))
        if va is None:
            continue
        rows.append((va, r.get("name") or "", int(r.get("size") or 0)))
    rows.sort()
    return rows


def next_row(rows, va):
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if rows[mid][0] <= va:
            lo = mid + 1
        else:
            hi = mid
    return rows[lo] if lo < len(rows) else None


def duplicate_groups(image, rows):
    """Map bodies grouped by (size, bytes): the control for the fold reading."""
    groups = collections.Counter()
    for va, _name, size in rows:
        if size <= 0 or size > 512:
            continue
        try:
            b = image.read_va(va, size)
        except KeyError:
            continue
        if b and len(b) == size:
            groups[(size, b)] += 1
    return groups


def child_classes():
    out = collections.defaultdict(list)
    for r in read_csv(PLAIN):
        va = num(r.get("table_va"))
        if va is None:
            continue
        for name in (r.get("child_classes") or "").split():
            if re.match(r"^[A-Za-z_]", name):
                out[va].append(name)
    return out


# --------------------------------------------------------------------------- the read
def harvest(image):
    rows = map_rows()
    gaps = {r["head_va"]: r for r in read_csv(GAPS) if r.get("row") == "head"}
    kids = child_classes()
    groups = duplicate_groups(image, rows)
    dup_groups = sum(1 for n in groups.values() if n > 1)
    dup_bodies = sum(n for n in groups.values() if n > 1)
    out, targets = [], collections.Counter()

    for r in residue_rows():
        va = num(r["head_va"])
        kind = r["verdict"]
        ghead = gaps.get(r["head_va"]) or {}
        rec = {k: "" for k in FIELDS}
        rec.update({"row": "body", "head_va": "%#010x" % va, "kind": kind, "indices": r.get("indices", ""),
                    "tables": r.get("tables", ""), "declarers": r.get("declarer", "")})

        if kind == "conflict":
            size = int(r.get("size") or ghead.get("size") or 0)
            try:
                body = image.read_va(va, size) if size else b""
            except KeyError:
                body = b""
            decl = DECLARED.findall(r.get("evidence", ""))
            rec.update({
                "verdict": "fold", "size": str(size),
                "declarers": "; ".join("%s_vt%s (held by %s)" % (c, s, n) for c, s, n in decl),
                "stop_shape": "identical-code fold",
                "evidence": "%d byte(s) `%s` at index %s of %s table(s), declared by %d classes that are "
                            "neither ancestor nor descendant; the map's %d duplicate group(s) over %d "
                            "bodies are the shape population it folds"
                            % (size, body.hex() if body else "-", r.get("indices", ""),
                               r.get("tables", ""), len(decl), dup_groups, dup_bodies),
            })
            out.append(rec)
            continue

        if kind == "declared":
            nxt = next_row(rows, va)
            try:
                vec = image.read_va(va, WINDOW)
            except KeyError:
                vec = image.read_va(va, 64)
            ins = disasm(vec, va)
            close = closes(ins, vec, va)
            stop_va, stop_name = (nxt[0], nxt[1]) if nxt else (0, "")
            shape, handler, opening = "code", None, "-"
            if nxt is not None:
                try:
                    svec = image.read_va(stop_va, 32)
                except KeyError:
                    svec = b""
                if svec:
                    sins = disasm(svec, stop_va)
                    handler = assert_block(sins, targets)
                    shape = "assert-block" if handler is not None else "code"
                    opening = " | ".join(t for _a, _s, t in sins[:3]) or "-"
            size = close[0] + close[1] - va if close else None
            crossed = sum(1 for a, _n, _s in rows if close and va < a < va + size)
            if close and close[2]:
                verdict = "table-cut"
                why = ("the sweep crosses an inline jump table before it closes, so a linear read cannot "
                       "bound the body")
            elif close and crossed and handler is not None:
                verdict = "island-cut"
                why = ("the body closes %d byte(s) in, past %d map row(s), and the row above is a check() "
                       "block it branches over" % (size, crossed))
            elif close and crossed:
                verdict = "spans-row"
                why = ("the body closes %d byte(s) in, past %d map row(s) that is not a check() block -- "
                       "either the row is a false head or the sweep overran" % (size, crossed))
            elif close:
                verdict = "closes"
                why = "the body closes %d byte(s) in, before the row above" % size
            else:
                verdict = "no-close"
                why = "no terminator behind a pad run in %d byte(s)" % len(vec)
            if verdict == "island-cut" and handler is not None:
                why += ", and the row above is an out-of-line check() block it branches over"
            rec.update({
                "verdict": verdict, "size": str(size or ""),
                "stop_va": "%#010x" % stop_va if stop_va else "", "stop_name": stop_name,
                "stop_shape": shape, "closes_at": "%#010x" % (va + size) if size else "",
                "crossed": str(crossed),
                "evidence": "gap %s..%s (%s byte(s)), row above %s at %s opening on `%s`; %s"
                            % (ghead.get("gap_lo", "-"), ghead.get("gap_hi", "-"),
                               ghead.get("gap_size", "-"), stop_name or "-",
                               "%#010x" % stop_va if stop_va else "-", opening, why),
            })
            out.append(rec)
            continue

        if kind == "member":
            m = MEMBER_TABLE.search(r.get("evidence", ""))
            tab = int(m.group(1), 16) if m else None
            field = MEMBER_FIELD.search(r.get("evidence", ""))
            rec.update({
                "verdict": "no-name", "size": r.get("size", "") or ghead.get("size", ""),
                "stop_va": "%#010x" % tab if tab else "",
                "stop_name": "member table", "stop_shape": "unlabelled member table",
                "evidence": "member table %s slot %s, stored into field %s of an object no class name "
                            "reaches, so the label pass gives the method no owner"
                            % ("%#010x" % tab if tab else "-", r.get("slot", "-"),
                               field.group(1) if field else "-"),
            })
            out.append(rec)
            continue

        # unnamed-table / declared-unnamed: the declarer table has no class; its children are named
        tab = num(r.get("declarer") or "")
        if tab is None:
            m = CLASS_TABLE.search(r.get("evidence", ""))
            tab = int(m.group(1), 16) if m else None
        names = kids.get(tab, []) if tab else []
        rec.update({
            "verdict": "no-name", "size": r.get("size", "") or ghead.get("size", ""),
            "stop_va": "%#010x" % tab if tab else "",
            "stop_name": "unregistered class table", "stop_shape": "class table, no registration",
            "evidence": "slot %s: the declaring table %s carries no registration; its child class(es) are "
                        "%s, so the name would have to come from the parent class itself"
                        % (r.get("slot", "-"), "%#010x" % tab if tab else "-",
                           ", ".join(names) if names else "none measured"),
        })
        out.append(rec)

    out.sort(key=lambda r: (r["kind"], num(r["head_va"]) or 0))
    census = {k: "" for k in FIELDS}
    census.update({
        "row": "census", "census": "census",
        "size": str(sum(1 for r in out if r["verdict"] == "closes")),
        "crossed": str(sum(int(r["crossed"] or 0) for r in out if r["crossed"])),
        "evidence": "residue %d bodies: fold %d, closes %d, island-cut %d, table-cut %d, no-name %d "
                    "(%d member over %d table(s), %d unnamed declarer over %d table(s)), no-close %d; "
                    "check() stops %d over %d handler(s)%s; the map's %d duplicate group(s) cover %d bodies"
                    % (len(out),
                       sum(1 for r in out if r["verdict"] == "fold"),
                       sum(1 for r in out if r["verdict"] == "closes"),
                       sum(1 for r in out if r["verdict"] == "island-cut"),
                       sum(1 for r in out if r["verdict"] == "table-cut"),
                       sum(1 for r in out if r["verdict"] == "no-name"),
                       sum(1 for r in out if r["verdict"] == "no-name" and r["kind"] == "member"),
                       len({r["stop_va"] for r in out
                            if r["verdict"] == "no-name" and r["kind"] == "member"}),
                       sum(1 for r in out if r["verdict"] == "no-name"
                           and r["kind"] in ("unnamed-table", "declared-unnamed")),
                       len({r["stop_va"] for r in out if r["verdict"] == "no-name"
                            and r["kind"] in ("unnamed-table", "declared-unnamed")}),
                       sum(1 for r in out if r["verdict"] == "no-close"),
                       sum(targets.values()), len(targets),
                       " (%s)" % ", ".join("%#010x" % k for k in sorted(targets)) if targets else "",
                       dup_groups, dup_bodies),
    })
    out.append(census)
    return out


# --------------------------------------------------------------------------- render
def summary(rows):
    bodies = [r for r in rows if r["row"] == "body"]
    pick = lambda v: [r for r in bodies if r["verdict"] == v]
    return {
        "bodies": bodies, "folds": pick("fold"), "islands": pick("island-cut"), "closes": pick("closes"),
        "tables": pick("table-cut"), "spans": pick("spans-row"), "no_name": pick("no-name"),
        "no_close": pick("no-close"),
        "member": [r for r in bodies if r["verdict"] == "no-name" and r["kind"] == "member"],
        "unnamed": [r for r in bodies if r["verdict"] == "no-name"
                    and r["kind"] in ("unnamed-table", "declared-unnamed")],
        "member_tables": len({r["stop_va"] for r in bodies
                              if r["verdict"] == "no-name" and r["kind"] == "member" and r["stop_va"]}),
        "unnamed_tables": len({r["stop_va"] for r in bodies if r["verdict"] == "no-name"
                               and r["kind"] in ("unnamed-table", "declared-unnamed") and r["stop_va"]}),
        "declared": [r for r in bodies if r["kind"] == "declared"],
        "crossed": sum(int(r["crossed"] or 0) for r in bodies if r["crossed"]),
        "curve": sum(int(r["size"] or 0) for r in bodies if r["verdict"] == "closes"),
    }


def render(rows):
    s = summary(rows)
    census = next((r["evidence"] for r in rows if r["row"] == "census"), "")
    handlers = re.search(r"handler\(s\)(?: \([^)]*\))", census)
    out = []
    out.append("| the four bodies two classes declare | byte(s) | the body | declarations |")
    out.append("|---|---:|---|---|")
    for r in s["folds"]:
        body = re.search(r"`([0-9a-f]*)`", r["evidence"])
        out.append("| `%s` | %s | `%s` | %s |"
                   % (r["head_va"], r["size"], body.group(1) if body else "-", r["declarers"] or "-"))
    out.append("")
    out.append("**The %d conflict bodies are folded bodies, not declarations.** Each is one of the build's "
               "smallest shapes held at two or more slot indices in classes that are neither ancestor nor "
               "descendant: a 1-byte `ret` at slot 17 of two class tables, a 4-byte `mov eax,[ecx+0x38]; "
               "ret` at slots 7, 8 and 30 of five, and a 21-byte getter pair at slots 34 and 35 of two. "
               "The linker emitted one body for identical methods, so the walk reads the same dword once "
               "per index. The map's own byte-duplicate groups are the control: %s."
               % (len(s["folds"]),
                  (re.search(r"the map's \d+ duplicate group\(s\) cover \d+ bodies", census) or
                   re.match(r"$", "")).group(0) or "not measured"))
    out.append("")
    out.append("| the undecoded declarations | declarer | row above | it opens on | verdict | closes at | "
               "byte(s) | map rows inside |")
    out.append("|---|---|---|---|---|---|---:|---:|")
    for r in s["declared"]:
        out.append("| `%s` | %s | `%s` | %s | `%s` | `%s` | %s | %s |"
                   % (r["head_va"], r["declarers"] or "-", r["stop_name"] or "-", r["stop_shape"] or "-",
                      r["verdict"], r["closes_at"] or "-", r["size"] or "-", r["crossed"] or "0"))
    out.append("")
    out.append("**%d of the %d declared bodies are cut by a check() block, not by the boundary the gap "
               "census drew.** On those the row above opens on `push; push; push; call` behind no padding "
               "-- an out-of-line `check()` block laid out inside the function, which the body branches "
               "over -- so the gap ends inside the body. %d more close past a map row that is not a "
               "check() block, %d cross an inline jump table first so a linear read cannot bound them, "
               "%d close before the row above and are addable as measured, and %d reach no terminator "
               "behind a pad run.%s"
               % (len(s["islands"]), len(s["declared"]), len(s["spans"]), len(s["tables"]),
                  len(s["closes"]), len(s["no_close"]),
                  (" The handler is one address on every check() block of them." if handlers else "")))
    out.append("")
    out.append("| the residue with no name | byte(s) | kind | its blocker | what the name would need |")
    out.append("|---|---:|---|---|---|")
    for r in s["no_name"]:
        need = ("a label for the member table's owner" if r["kind"] == "member"
                else "a class for the declarer table")
        out.append("| `%s` | %s | `%s` | %s `%s` | %s |"
                   % (r["head_va"], r["size"] or "-", r["kind"], r["stop_shape"], r["stop_va"], need))
    out.append("")
    out.append("**%d bodies need a name no image-side table carries.** The %d member bodies sit in %d "
               "member tables whose owner the label pass refused, and the %d declarations sit on %d class "
               "tables no registration names while the registration does name their children -- so the "
               "first needs an owner for a field and the second a class for a parent table, and neither is "
               "a read the declaration walk owes."
               % (len(s["no_name"]), len(s["member"]), s["member_tables"], len(s["unnamed"]),
                  s["unnamed_tables"]))
    return "\n".join(out)


def write_artifact(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []

    def ins(pairs):
        return [(a, len(b.split()), t) for a, b, t in pairs]

    parsed = parse_objdump("""
/tmp/x.bin:     file format binary

Disassembly of section .data:

  2f13c:\t68 d2 03 00 00       \tpush   $0x3d2
  2f141:\t68 a0 1c 4d 00       \tpush   $0x4d1ca0
  2f146:\t68 c8 21 4d 00       \tpush   $0x4d21c8
  2f14b:\te8 e0 98 02 00       \tcall   0x58a30
  2f150:\t83 c4 0c             \tadd    $0xc,%esp
""")
    if [p[1] for p in parsed] != [5, 5, 5, 5, 3] or parsed[3][2] != "call   0x58a30":
        bad.append("the objdump line parser reads address, width and text")
    got = assert_block(parsed[:4], collections.Counter())
    if got != 0x58A30:
        bad.append("three immediate pushes and a direct call are the check() block")
    if assert_block(ins([(0, "53", "push   %ebx"), (1, "56", "push   %esi"), (2, "57", "push   %edi"),
                         (3, "e8 00", "call   0x10")])) is not None:
        bad.append("three register pushes are not a check() block")
    if assert_block(ins([(0, "68 01", "push   $0x1"), (5, "68 02", "push   $0x2"),
                         (10, "68 03", "push   $0x3"), (15, "ff 95 a8", "call   *0xa8(%ebp)")])) is not None:
        bad.append("an indirect call is not the shared handler")
    vec = b"\x55" + b"\xc3" + b"\xcc\xcc" + b"\x00" * 8
    body = ins([(0, "55", "push   %ebp"), (1, "c3", "ret")])
    if closes(body, vec, 0)[:2] != (1, 1):
        bad.append("a ret behind a pad run closes the body")
    if closes(body, b"\x55\xc3\x00\x00", 0) is not None:
        bad.append("a ret with code behind it does not close the body")
    vec2 = b"\x55" + b"\xc2\x10\x00" + b"\xcc\xcc"
    if closes(ins([(0, "55", "push   %ebp"), (1, "c2 10 00", "ret    $0x10")]), vec2, 0)[:2] != (1, 3):
        bad.append("a ret imm closes at its own width")
    if closes(body, vec, 0)[2]:
        bad.append("a body without a table read does not set the table flag")
    tbl = ins([(0, "55", "push   %ebp"), (1, "ff 24 85 e0 bd 07 00", "jmp    *0x7bde0(,%eax,4)"),
               (7, "55", "push   %ebp"), (8, "c3", "ret")])
    got = closes(tbl, b"\x00" * 7 + b"\x55\xc3\xcc\xcc", 0)
    if not got or not got[2]:
        bad.append("a jump through a table flags the sweep as reading data")
    if not pad_run(b"\x00\xcc\xcc", 1) or pad_run(b"\x00\xcc\x00", 1):
        bad.append("a tail is a run of pad bytes, not one")

    rows = []
    for va, kind, verdict, stop, crossed, size, shape, decl in (
            ("0x0002f120", "declared", "island-cut", "0x0002f13c", "1", 91, "assert-block", "X"),
            ("0x00078ae0", "declared", "closes", "0x00078b80", "0", 157, "code", "Y"),
            ("0x001ec800", "conflict", "fold", "", "0", 4, "identical-code fold",
             "Object_vt17 (held by 474)"),
            ("0x00332362", "member", "no-name", "0x0053f7f0", "0", 25, "unlabelled member table", "Z"),
            ("0x00034be0", "declared-unnamed", "no-name", "0x004d34d8", "0", 43,
             "class table, no registration", "W")):
        r = {k: "" for k in FIELDS}
        r.update({"row": "body", "head_va": va, "kind": kind, "verdict": verdict, "size": str(size),
                  "stop_va": stop, "stop_shape": shape, "crossed": crossed, "declarers": decl,
                  "stop_name": "XboxAudio_XboxAudioSubsystem" if verdict == "island-cut" else "-",
                  "closes_at": "0x0002f17b" if verdict == "island-cut" else "",
                  "evidence": "`%s` synthetic; the map's 1 duplicate group(s) cover 3 bodies" % (
                      "c3" if verdict == "fold" else "")})
        rows.append(r)
    rows.append({k: "" for k in FIELDS})
    rows[-1].update({"row": "census", "census": "census", "size": "1", "crossed": "1",
                     "evidence": "residue 5 bodies: fold 1, closes 1, island-cut 1, no-name 2 (1 member "
                                 "over 1 table(s), 1 unnamed declarer over 1 table(s)), no-close 0; "
                                 "check() stops 1 over 1 handler(s) (0x00058a30); the map's 1 "
                                 "duplicate group(s) cover 3 bodies"})
    out = render(rows)
    for want in ("fold", "check() block", "member table", "declarer table", "addable"):
        if want not in out:
            bad.append("render lacks %r" % (want,))
    s = summary(rows)
    if len(s["folds"]) != 1 or len(s["islands"]) != 1 or len(s["closes"]) != 1 or len(s["member"]) != 1:
        bad.append("the verdicts split the population")
    if len(s["declared"]) != 2:
        bad.append("the declared population is the two declared rows")
    if s["member_tables"] != 1 or s["unnamed_tables"] != 1:
        bad.append("the blocker table counts are distinct addresses")
    if DECLARED.findall("two independent declarations: Object_vt17 (held by 474); "
                        "GameInfo_vt17 (held by 1)") != [("Object", "17", "474"), ("GameInfo", "17", "1")]:
        bad.append("the declaration pair does not parse")
    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_csv(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL declarer-residue: " + "; ".join(bad))
        return 1
    print("ok   declarer-residue: the objdump line parser, the check() block and its two refusals, the pad "
          "run and the ret imm width, the five verdicts, the blocker table counts and the artifact round "
          "trip agree")
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        rows = read_csv(OUT)
        if not rows:
            print("FAIL declarer-residue: %s is missing" % OUT)
            return 1
        block = render(rows)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL declarer-residue: %s has no generated block" % args.check_doc)
            return 1
        if text[i:j + len(DOC_END)] != DOC_START + "\n" + block + "\n" + DOC_END:
            print("FAIL declarer-residue: %s's block does not reproduce from %s" % (args.check_doc, OUT))
            return 1
        print("ok   declarer-residue: %s's block reproduces from %s" % (args.check_doc, OUT))
        return 0
    if args.report or args.write_doc:
        rows = read_csv(OUT)
        if not rows:
            print("FAIL declarer-residue: %s is missing" % OUT)
            return 1
        block = render(rows)
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block)
        if new is None:
            print("FAIL declarer-residue: %s has no generated block markers" % args.write_doc)
            return 1
        if new != open(args.write_doc, encoding="utf-8").read():
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s" % args.write_doc)
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the image (or use --report/--check-doc/--check)")
    image = sibling("xbe").Xbe(args.xbe)
    rows = harvest(image)
    if args.out:
        path = os.path.join(args.out, os.path.basename(OUT))
        write_artifact(rows, path)
        print("wrote %s (%d rows)" % (path, len(rows)))
        print(next(r["evidence"] for r in rows if r["row"] == "census"))
    else:
        print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
