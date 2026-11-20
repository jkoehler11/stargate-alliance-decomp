#!/usr/bin/env python3
"""Decode the map's jump thunks: what each one jumps to, the name that follows, and what the target is.

75 rows of `symbols/xbox_functions.csv` are not bodies -- they are 2, 5 or 6 bytes that transfer
control somewhere else, and the map named each one after its target *by address*
(`thunk_FUN_003e67ed`). That name does not survive the next naming pass: once the target has a name
of its own, the row still spells a number, and a reader cannot tell `0x00036cb0` from any other
five-byte stub without disassembling it.

This reads the bytes of every thunk row and settles the row from its own instruction:

* `e9 rel32` -- a near jump, 5 bytes, no fall-through (72 rows);
* `eb rel8`  -- a short jump, 2 bytes (2 rows: a hotpatch slot that jumps onto the next
  instruction, `eb 00`, and one that skips ten bytes);
* `ff 25 disp32` -- an indirect jump through an absolute pointer, 6 bytes: a slot in the image's own
  pointer table (2 rows, one of them the end of a chain).

A thunk can jump to another thunk: `0x0007e4f0` is `jmp 0x0033e788`, which is `jmp 0x00343fae`,
which is `jmp dword ptr [0x004c6cc4]`. The row records the **terminal** hop and how many hops it
took, because that is what a reader wants ("where does this go, in the end") and because a two-level
thunk's name already carries the inner thunk's name.

The rule the artifact records is the one `docs/SYMBOLS.md` states: a thunk whose terminal target has
a name is `thunk_<target>`, and one whose target is still `FUN_*` keeps `thunk_FUN_<target address>`
-- a name is not invented for the target here, only carried across the jump.

**The target's own shape is the second half of the read.** When the terminal target is still
`FUN_*`, "where does this go" is only half an answer: the target's first instruction says what kind
of thing it is, and that is a fact the bytes carry whether or not a name does. `--report` (and the
generated block in `docs/SYMBOLS.md`) lists each unnamed target with the shape it was measured to
have -- a virtual-dispatch trampoline, a `this`-adjustor, a vtable installer, a CRT dispatch stub, a
`call` forwarder, a chain hop, or a plain body. The shape is not a name and the map is not edited on
one; it is what tells the next session which targets are worth naming and which are compiler
furniture that never had a name in the source.

    python3 tools/thunk-targets.py --xbe <dump>/default.xbe -o inventory
    python3 tools/thunk-targets.py --check                    # no dump, no writes
    python3 tools/thunk-targets.py --report                    # the artifact as a table
    python3 tools/thunk-targets.py --write-doc docs/SYMBOLS.md # regenerate the generated block
    python3 tools/thunk-targets.py --check-doc docs/SYMBOLS.md # no dump: the block matches the csv
    python3 tools/thunk-targets.py --emit-boundaries           # the `rename` rows for xbe_boundaries.csv
    python3 tools/thunk-targets.py --selftest                  # synthetic rows, every encoding and refusal

`--check` reconciles the committed csv against the map without the dump: every `thunk_*` map row has
a row here, every row's name is the one its encoding and target imply, and the encodings are the
three above. `--emit-boundaries` prints the rows that *make* the map say so, which are reviewed and
applied by `tools/symbol-boundaries.py` -- this tool measures, it does not edit the map.
"""
import argparse
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
CSV_PATH = os.path.join(ROOT, "inventory", "thunk_targets.csv")
DOC_START = "<!-- generated:thunk-targets -->"
DOC_END = "<!-- /generated:thunk-targets -->"
COLUMNS = ["address", "name", "size", "encoding", "terminal", "target", "slot", "target_name",
           "hops", "target_kind", "target_evidence"]
ENCODINGS = ("jmp-rel32", "jmp-rel8", "jmp-indirect")
THUNK_PREFIX = "thunk_"
# The shapes a terminal target's own bytes can be. `body` is the honest default: nothing about the
# first instruction says the target is furniture, so the row says nothing either.
KINDS = ("body", "chain-jump", "crt-dispatch", "vdispatch", "vcall", "adjustor", "vtbl-install",
         "call-forward")
HELPER = "__cintrindisp1"


def read_map(path=MAP):
    """address -> (name, size) for every row of the function map."""
    out = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out[int(row["address"], 16)] = (row["name"], int(row["size"]))
    return out


def decode(data, va):
    """Decode one thunk body. Returns (encoding, target, slot) or None when it is not a thunk."""
    if not data:
        return None
    if data[0] == 0xE9 and len(data) >= 5:
        rel = int.from_bytes(data[1:5], "little", signed=True)
        return ("jmp-rel32", va + 5 + rel, None)
    if data[0] == 0xEB and len(data) >= 2:
        rel = int.from_bytes(data[1:2], "little", signed=True)
        return ("jmp-rel8", va + 2 + rel, None)
    if data[:2] == b"\xff\x25" and len(data) >= 6:
        return ("jmp-indirect", None, int.from_bytes(data[2:6], "little"))
    return None


def encoding_width(encoding):
    return {"jmp-rel32": 5, "jmp-rel8": 2, "jmp-indirect": 6}[encoding]


def classify(target_name):
    """The rule: carry the target's name across the jump, or keep the address when it has none."""
    if target_name and not target_name.startswith("FUN_"):
        return True
    return False


def rule_name(target, target_name):
    if target is not None and classify(target_name):
        return THUNK_PREFIX + target_name
    if target is not None:
        return "%sFUN_%08x" % (THUNK_PREFIX, target)
    return ""


def target_kind(body, rows, va):
    """(kind, evidence) from the terminal target's own instruction. Never a name, only a shape.

    Only what the bytes spell at the target's own edges is used: its first instruction and, for the
    forwarders, the operation immediately before its `ret`. A byte scan for `FF 5x` anywhere in a body
    finds the virtual call a *real* body makes in its middle as readily as a stub's only work, so the
    vcall rule is anchored to the return -- measured 2026-11-20, when the loose scan called a 101-byte
    SEH body with a `ff 53 08` in its argument setup a forwarder.
    """
    if not body:
        return "body", ""
    got = decode(body, va)
    if got is not None:
        if got[0] == "jmp-indirect":
            return "chain-jump", "`jmp dword ptr [0x%08x]`" % got[2]
        return "chain-jump", "`jmp 0x%08x`" % got[1]
    if len(body) >= 10 and body[0] == 0xBA and body[5] == 0xE9:
        table = struct.unpack_from("<I", body, 1)[0]
        tail = va + 10 + struct.unpack_from("<i", body, 6)[0]
        if rows.get(tail, ("", 0))[0] == HELPER:
            return "crt-dispatch", "`mov edx, 0x%08x; jmp %s`" % (table, HELPER)
    if len(body) >= 5 and body[:2] == b"\x8b\x01" and body[2] == 0xFF and body[3] == 0x60:
        return "vdispatch", "`mov eax, [ecx]; jmp [eax+0x%02x]`" % body[4]
    if body[:4] == b"\x8b\x54\x24\x04" and b"\x89\x44\x24\x04\xe9" in body[:16]:
        return "adjustor", "reads `this` from the stack, replaces it, and tail-jumps"
    if body[:2] == b"\xc7\x01" and len(body) == 7 and body[6] == 0xC3:
        return "vtbl-install", "`mov dword ptr [ecx], 0x%08x; ret`" % \
            struct.unpack_from("<I", body, 2)[0]
    # the operation immediately before the return, for a body that is nothing but a call
    tail = body[-3:]
    if tail[0] == 0xC2 and tail[2] == 0x00:
        before = body[-6:-3]
    elif body[-1:] == b"\xc3":
        before = body[-4:-1]
    else:
        before = b""
    if len(before) == 3 and before[0] == 0xFF:
        if 0x50 <= before[1] <= 0x57:
            return "vcall", "`call [reg+0x%02x]`, then return" % before[2]
        if 0x90 <= before[1] <= 0x97:
            return "vcall", "`call [reg+0x%08x]`, then return" % struct.unpack_from("<I", body, -5)[0]
    calls = sum(1 for i in range(len(body) - 4) if body[i] == 0xE8)
    if calls == 1 and len(body) <= 16:
        return "call-forward", "one `call` and no other work, then return"
    return "body", ""


def resolve(rows, body_at, va):
    """Decode the thunk at `va` and follow chained thunks to the terminal hop.

    Returns (first_encoding, terminal_encoding, target, slot, hops) or None when `va` is not a jump.
    """
    got = body_at(va)
    if got is None:
        return None
    encoding, target, slot = got
    first = encoding
    hops = 1
    while target is not None and hops < 8 and rows.get(target, ("", 0))[0].startswith(THUNK_PREFIX):
        nxt = body_at(target)
        if nxt is None:
            break
        encoding, target, slot = nxt
        hops += 1
    return first, encoding, target, slot, hops


def measure(xbe_path):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe  # noqa: E402  (dump-side import, so --check can run without it)

    xbe = Xbe(xbe_path)
    rows = read_map()

    def body_at(va):
        """Decode the instruction at `va`; a slot's contents are the target of an indirect jump."""
        got = decode(xbe.read_va(va, 8), va)
        if got is None:
            return None
        encoding, target, slot = got
        if slot is not None:
            try:
                target = int.from_bytes(xbe.read_va(slot, 4), "little")
            except Exception:
                target = None  # an unreadable slot is a fact, not a crash
        return encoding, target, slot

    out = []
    for va in sorted(rows):
        name, size = rows[va]
        if not name.startswith(THUNK_PREFIX):
            continue
        got = resolve(rows, body_at, va)
        if got is None:
            raise SystemExit("%#010x %s: %s bytes that are not a jump" % (va, name, size))
        first, encoding, target, slot, hops = got
        target_name = rows.get(target, ("", 0))[0] if target is not None else ""
        kind, evidence = "body", ""
        if target is not None:
            tsize = rows.get(target, ("", 0))[1] or 32
            kind, evidence = target_kind(xbe.read_va(target, min(max(tsize, 16), 64)) or b"",
                                         rows, target)
        out.append({"address": "0x%08x" % va, "name": name, "size": str(size),
                    "encoding": first, "terminal": encoding,
                    "target": "0x%08x" % target if target is not None else "",
                    "slot": "0x%08x" % slot if slot is not None else "",
                    "target_name": target_name, "hops": str(hops),
                    "target_kind": kind, "target_evidence": evidence})
    return out


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def read_csv(path=CSV_PATH):
    if not os.path.exists(path):
        raise SystemExit("%s: missing (run tools/thunk-targets.py with the dump)" % path)
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if [c for c in COLUMNS] != list(rows[0].keys()):
        raise SystemExit("%s: columns %s != %s" % (path, list(rows[0].keys()), COLUMNS))
    return rows


def check(path=CSV_PATH):
    """Reconcile the committed csv with the map and with the naming rule. No dump, no writes."""
    bad = []
    try:
        rows = read_csv(path)
    except SystemExit as exc:
        print("FAIL thunk-targets: %s" % exc)
        return 1
    limited = read_map()
    seen, last = set(), -1
    for r in rows:
        try:
            va = int(r["address"], 16)
            size = int(r["size"])
        except ValueError:
            bad.append("%r: address/size is not a number" % r["address"])
            continue
        if va in seen:
            bad.append("duplicate address %s" % r["address"])
        seen.add(va)
        if va < last:
            bad.append("addresses out of order at %s" % r["address"])
        last = va
        if r["encoding"] not in ENCODINGS:
            bad.append("%s: encoding %r outside %s" % (r["address"], r["encoding"], ENCODINGS))
            continue
        if r["terminal"] not in ENCODINGS:
            bad.append("%s: terminal %r outside %s" % (r["address"], r["terminal"], ENCODINGS))
            continue
        if size != encoding_width(r["encoding"]):
            bad.append("%s: %s body is %d bytes, the encoding is %d"
                       % (r["address"], r["encoding"], size, encoding_width(r["encoding"])))
        if r["target_kind"] not in KINDS:
            bad.append("%s: target_kind %r outside %s" % (r["address"], r["target_kind"], KINDS))
        if r["target_kind"] == "body" and r["target_evidence"]:
            bad.append("%s: a `body` kind carries evidence, which is a contradiction"
                       % r["address"])
        if r["target_kind"] != "body" and not r["target_evidence"]:
            bad.append("%s: kind %r with no evidence" % (r["address"], r["target_kind"]))
        try:
            hops = int(r["hops"])
        except ValueError:
            bad.append("%s: hops %r is not a count" % (r["address"], r["hops"]))
            continue
        if hops < 1 or hops > 7:
            bad.append("%s: hops %d outside 1..7" % (r["address"], hops))
        if hops == 1 and r["terminal"] != r["encoding"]:
            bad.append("%s: one hop, but the terminal encoding is %r and the row's is %r"
                       % (r["address"], r["terminal"], r["encoding"]))
        if r["terminal"] == "jmp-indirect" and not r["slot"]:
            bad.append("%s: an indirect jump needs its pointer slot" % r["address"])
        if r["terminal"] != "jmp-indirect" and r["slot"]:
            bad.append("%s: a %s terminal carries a pointer slot" % (r["address"], r["terminal"]))
        if limited.get(va) is None:
            bad.append("%s: not a row of symbols/xbox_functions.csv" % r["address"])
            continue
        have = limited[va][0]
        if not have.startswith(THUNK_PREFIX):
            bad.append("%s: the map calls it %s, which is not a thunk name" % (r["address"], have))
            continue
        if have != r["name"]:
            bad.append("%s: the map calls it %s, this row says %s" % (r["address"], have, r["name"]))
            continue
        target = int(r["target"], 16) if r["target"] else None
        want = rule_name(target, r["target_name"])
        if want and want != have:
            bad.append("%s: is %s, the rule says %s (target %s)"
                       % (r["address"], have, want, r["target"] or "unresolved"))
        if target is not None and limited.get(target) is not None:
            if limited[target][0] != r["target_name"]:
                bad.append("%s: target %s is %s in the map, this row says %r"
                           % (r["address"], r["target"], limited[target][0], r["target_name"]))
    thunks = {va for va, (name, _s) in limited.items() if name.startswith(THUNK_PREFIX)}
    absent = sorted(thunks - seen)
    if absent:
        bad.append("%d map thunk(s) missing here, first %#010x" % (len(absent), absent[0]))
    doc = os.path.join(ROOT, "docs", "SYMBOLS.md")
    if os.path.exists(doc):
        text = open(doc, encoding="utf-8").read()
        if DOC_START in text and DOC_END in text:
            if text.split(DOC_START, 1)[1].split(DOC_END, 1)[0].strip("\n") != report(rows):
                bad.append("docs/SYMBOLS.md: the generated block differs from the csv "
                           "(run --write-doc docs/SYMBOLS.md)")
    if bad:
        for b in bad[:12]:
            print("FAIL thunk-targets: %s" % b)
        if len(bad) > 12:
            print("FAIL thunk-targets: ... and %d more" % (len(bad) - 12))
        return 1
    enc = {}
    for r in rows:
        enc[r["encoding"]] = enc.get(r["encoding"], 0) + 1
    named = sum(1 for r in rows if r["target_name"] and not r["target_name"].startswith("FUN_"))
    print("ok   thunk-targets: %d thunk(s) (%s), %d named after their target, %d still FUN_* targets"
          % (len(rows), ", ".join("%s %d" % (k, enc[k]) for k in ENCODINGS if k in enc), named,
             len(rows) - named))
    return 0


def report(rows):
    """The unnamed targets, one row each, with the shape their own bytes were measured to have."""
    unnamed = {}
    for r in rows:
        if not r["target"] or not r["target_name"].startswith("FUN_"):
            continue
        got = unnamed.setdefault(r["target"], {"kind": r["target_kind"],
                                               "evidence": r["target_evidence"], "thunks": []})
        got["thunks"].append(r["address"])
        if r["target_kind"] != "body":  # one shape per target; the first non-body read wins
            got["kind"], got["evidence"] = r["target_kind"], r["target_evidence"]
    out = []
    counts = {}
    for t in sorted(unnamed):
        counts[unnamed[t]["kind"]] = counts.get(unnamed[t]["kind"], 0) + 1
    out.append("| terminal target | thunks that reach it | shape the target's first instruction shows |")
    out.append("|---|---:|---|")
    for t in sorted(unnamed):
        g = unnamed[t]
        out.append("| `%s` | %d | %s%s |" % (t, len(g["thunks"]), g["kind"],
                                            (": " + g["evidence"]) if g["evidence"] else ""))
    out.append("")
    out.append("**%d distinct targets** behind the %d unnamed thunk rows: %s."
               % (len(unnamed), sum(len(g["thunks"]) for g in unnamed.values()),
                  ", ".join("%s %d" % (k, counts[k]) for k in KINDS if k in counts)))
    return "\n".join(out)


def write_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    if DOC_START not in text or DOC_END not in text:
        raise SystemExit("%s: no %s ... %s block" % (path, DOC_START, DOC_END))
    head, rest = text.split(DOC_START, 1)
    _, tail = rest.split(DOC_END, 1)
    new = head + DOC_START + "\n" + report(rows) + "\n" + DOC_END + tail
    if new != text:
        open(path, "w", encoding="utf-8").write(new)
        return 1
    return 0


def emit_boundaries(rows):
    """The `rename` rows that make the map agree with the rule, for symbols/xbe_boundaries.csv."""
    w = csv.writer(sys.stdout, lineterminator="\n")
    for r in rows:
        target = int(r["target"], 16) if r["target"] else None
        want = rule_name(target, r["target_name"])
        if not want or want == r["name"]:
            continue
        if r["terminal"] == "jmp-indirect":
            how = "`jmp dword ptr [%s]`, which holds %s" % (r["slot"], r["target"])
        else:
            how = "`jmp %s`" % r["target"]
        if r["hops"] != "1":
            how += " after %s hops" % r["hops"]
        w.writerow([r["address"], "rename", want, "",
                    "%s in %s bytes, no fall-through; the map names the target %s, so the row spells "
                    "its address instead of its name" % (how, r["size"], r["target_name"])])


def selftest():
    failures = []

    def expect(label, data, va, want):
        got = decode(data, va)
        if want is None:
            if got is not None:
                failures.append("%s: decoded %r, expected not-a-jump" % (label, got))
            return
        if got != want:
            failures.append("%s: decoded %r, expected %r" % (label, got, want))

    expect("rel32 forward", bytes([0xE9, 0xFB, 0x3A, 0x00, 0x00]), 0x36CB0,
           ("jmp-rel32", 0x36CB0 + 5 + 0x3AFB, None))
    expect("rel32 backward", bytes([0xE9, 0xAB, 0xFC, 0xFF, 0xFF]), 0x65250,
           ("jmp-rel32", 0x65250 + 5 - 0x355, None))
    expect("rel8 zero", bytes([0xEB, 0x00]), 0x3E7B74, ("jmp-rel8", 0x3E7B76, None))
    expect("rel8 ten", bytes([0xEB, 0x0A]), 0x3E9A2C, ("jmp-rel8", 0x3E9A38, None))
    expect("indirect", bytes([0xFF, 0x25, 0xE8, 0xCA, 0x5C, 0x00]), 0x3978D0,
           ("jmp-indirect", None, 0x5CCAE8))
    expect("a call is not a thunk", bytes([0xE8, 0x01, 0x00, 0x00, 0x00]), 0x1000, None)
    expect("a bare ret is not a thunk", bytes([0xC3]), 0x1000, None)
    expect("truncated rel32", bytes([0xE9, 0x00]), 0x1000, None)

    # the rule, on the two shapes the artifact carries
    cases = [(0x3E67ED, "_wcslen", "thunk__wcslen"),
             (0x3D8DB0, "FUN_003d8db0", "thunk_FUN_003d8db0"),
             (0x3E7B76, "FUN_003e7b76", "thunk_FUN_003e7b76")]
    for target, tname, want in cases:
        got = rule_name(target, tname)
        if got != want:
            failures.append("rule %s/%s: got %r, want %r" % (hex(target), tname, got, want))

    # a chain: 0x1000 -> 0x2000 -> 0x3000, where each hop is a thunk and the last is indirect
    synthetic = {0x1000: ("thunk_FUN_00200000", 5), 0x2000: ("thunk_FUN_00300000", 5),
                 0x3000: ("thunk_FUN_00345fae", 6), 0x345FAE: ("FUN_00345fae", 40)}
    hops = {0x1000: ("jmp-rel32", 0x2000, None), 0x2000: ("jmp-rel32", 0x3000, None),
            0x3000: ("jmp-indirect", 0x345FAE, 0x4C6CC4)}
    got = resolve(synthetic, lambda va: hops.get(va), 0x1000)
    if got != ("jmp-rel32", "jmp-indirect", 0x345FAE, 0x4C6CC4, 3):
        failures.append("chain resolve: got %r" % (got,))
    if rule_name(got[2], "FUN_00345fae") != "thunk_FUN_00345fae":
        failures.append("chain rule: a FUN_* terminal keeps the address")
    if rule_name(got[2], "_wcslen") != "thunk__wcslen":
        failures.append("chain rule: a named terminal carries its name")
    if resolve(synthetic, lambda va: hops.get(va), 0x2000)[4] != 2:
        failures.append("chain resolve: the tail hop count")
    if resolve(synthetic, lambda va: None, 0x1000) is not None:
        failures.append("chain resolve: a body that is not a jump decodes")
    if encoding_width("jmp-rel8") != 2 or encoding_width("jmp-indirect") != 6:
        failures.append("encoding widths")

    # the shapes, on the bytes the image actually carries
    names = {0x3EE17A: ("__cintrindisp1", 100)}
    shapes = [
        ("crt-dispatch", bytes.fromhex("baacd35c00") + bytes.fromhex("e938470000"), 0x3E9A38),
        ("vdispatch", bytes.fromhex("8b01ff6020"), 0x624F0),
        ("vtbl-install", bytes.fromhex("c701a86f5100c3"), 0x1D4D10),
        ("adjustor", bytes.fromhex("8b5424048b422c89442404e980b10400"), 0x398B50),
        ("call-forward", bytes.fromhex("8b44240450e86dfc000059c20400"), 0x3D8DB0),
        ("vcall", bytes.fromhex("ff7424108b44240cff7424108b0850ff5124c21000"), 0x32FC55),
        ("chain-jump", bytes.fromhex("ff25c46c4c00"), 0x343FAE),
        ("body", bytes.fromhex("6aff6818293f0064a10000000050"), 0x1B310),
    ]
    for want, data, va in shapes:
        kind, _ev = target_kind(data, names, va)
        if kind != want:
            failures.append("shape %#x: got %r, want %r" % (va, kind, want))

    if failures:
        for f in failures:
            print("FAIL thunk-targets: %s" % f)
        return 1
    print("ok   thunk-targets: 3 encodings, 5 refusals, 3 rule cases, 7 target shapes")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", help="the image, for the thunk bytes (write mode)")
    ap.add_argument("-o", "--out", default="inventory", help="where thunk_targets.csv goes")
    ap.add_argument("--check", action="store_true", help="reconcile the csv with the map, no dump")
    ap.add_argument("--report", action="store_true", help="print the unnamed targets as a table")
    ap.add_argument("--write-doc", help="regenerate a generated block in this page")
    ap.add_argument("--check-doc", help="no dump: does the page's block match the csv?")
    ap.add_argument("--emit-boundaries", action="store_true",
                    help="print the `rename` rows for symbols/xbe_boundaries.csv")
    ap.add_argument("--selftest", action="store_true", help="decode synthetic bodies, no dump")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check()
    if args.check_doc:
        text = open(args.check_doc, encoding="utf-8").read()
        if DOC_START not in text or DOC_END not in text:
            raise SystemExit("%s: no %s ... %s block" % (args.check_doc, DOC_START, DOC_END))
        got = text.split(DOC_START, 1)[1].split(DOC_END, 1)[0].strip("\n")
        if got != report(read_csv()):
            print("FAIL thunk-targets: %s: the generated block differs from the csv "
                  "(run --write-doc %s)" % (args.check_doc, args.check_doc))
            return 1
        return 0
    if args.report:
        print(report(read_csv()))
        return 0
    if args.emit_boundaries:
        emit_boundaries(read_csv())
        return 0
    if not args.xbe:
        ap.error("--xbe is required to measure (or use --check/--selftest)")
    rows = measure(args.xbe)
    write_csv(os.path.join(args.out, "thunk_targets.csv"), rows)
    if args.write_doc:
        write_doc(args.write_doc, rows)
    named = sum(1 for r in rows if r["target_name"] and not r["target_name"].startswith("FUN_"))
    print("wrote %s/thunk_targets.csv: %d thunks, %d named targets, %d FUN_* targets"
          % (args.out, len(rows), named, len(rows) - named))
    return 0


if __name__ == "__main__":
    sys.exit(main())
