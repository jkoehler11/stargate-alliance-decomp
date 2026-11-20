#!/usr/bin/env python3
"""Repair the symbol map's body boundaries from reviewed evidence, without a Ghidra round.

`symbols/xbox_functions.csv` is Ghidra's function list, and its *boundaries* are Ghidra's inference, not the
build's. Three defects follow from that and all three are structural, not one-off typos:

* a body Ghidra split at an **assert block** -- the block is entered by fall-through, never by a call, and
  the row it gets carries the assert's `__FILE__` as a name. `0x0002f820` is `Core_UnTemplate`, 518 bytes,
  inside `XboxAudio_XboxAudioSubsystem` (`0x0002f13c`), whose assert is `Index<=ArrayNum` from
  `core\\inc\\UnTemplate` -- an inlined template's bound check, so the name describes a header, not a body;
* a body Ghidra **stopped short**, so a reader that disassembles the row's declared size never sees its last
  instructions (`0x0002feb0` ends at `0x3134d`, inside the `rdtsc` save pair; the closing `mov [ecx],edx`
  and `ret 0x4` are past it);
* a body Ghidra **never created**, because nothing calls it: `0x0002fa30`, 87 bytes, closed by `ret` at
  `0x2fa86`, sits in the gap between two rows and is invisible to every census that reads the map.

`symbols/xbe_overrides.csv` can name a body but cannot move its edge, so a repair that has to move an edge
lives in `symbols/xbe_boundaries.csv`, one reviewed row per repair, and this tool applies it to the map in
its own right -- `--apply` rewrites the csv, `--check` proves the committed csv already carries exactly these
repairs (so an import that undoes one fails CI instead of silently reverting it).

    python3 tools/symbol-boundaries.py --apply       # rewrite symbols/xbox_functions.csv
    python3 tools/symbol-boundaries.py --check       # no dump: does the committed map carry the repairs?
    python3 tools/symbol-boundaries.py --selftest    # synthetic map, every verb and every refusal

The verbs are `resize` (a measured end), `drop` (interior code of the row a `target` names), `add` (a body
the map has no row for) and `rename` (a name the row's own instruction settles -- a jump thunk that spells its
target by address while the target has a name). Each is validated against the map, not trusted: a `drop` must
land strictly inside its target's repaired span, a repair may not create an overlap with a neighbouring row,
an `add` may not land inside any row, and a `rename` must name a row the map has. Every row in
`xbe_overrides.csv` that records a `function_size` must agree with the repaired map, and every `add` must have
one -- that file is what `XbePost.java` builds a function from, so an `add` without it is a row a fresh import
cannot see. The reviewed evidence and the map cannot drift apart, then, in either direction.
"""
import argparse
import csv
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
BOUNDARIES = os.path.join(ROOT, "symbols", "xbe_boundaries.csv")
OVERRIDES = os.path.join(ROOT, "symbols", "xbe_overrides.csv")
HEADER = ["address", "name", "size", "module"]
COLUMNS = ["address", "action", "target", "size", "evidence"]
VERBS = ("resize", "drop", "add", "rename")


class Refusal(Exception):
    pass


def read_map(path):
    rows = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append({"address": int(row["address"], 16), "name": row["name"],
                         "size": int(row["size"]), "module": (row.get("module") or "").strip()})
    return rows


def read_boundaries(path):
    out = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise Refusal("%s: header is missing %s" % (os.path.basename(path), ", ".join(missing)))
        for n, row in enumerate(reader, start=2):
            ev = (row.get("evidence") or "").strip()
            if not ev:
                raise Refusal("line %d: a repair carries the evidence that settles it" % n)
            out.append({"line": n, "address": int(row["address"], 16),
                        "action": (row.get("action") or "").strip(),
                        "target": (row.get("target") or "").strip(),
                        "size": (row.get("size") or "").strip(), "evidence": ev})
    return out


def read_sizes(path):
    """The reviewed body sizes `xbe_overrides.csv` records, by address."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            size = (row.get("function_size") or "").strip()
            if size:
                out[int(row["address"], 16)] = (int(size), row["name"])
    return out


def render(rows):
    lines = [",".join(HEADER)]
    for r in rows:
        lines.append("0x%08x,%s,%d,%s" % (r["address"], r["name"], r["size"], r["module"]))
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def apply_repairs(rows, boundaries, override_sizes):
    """Return the repaired rows. Raises Refusal on anything the map does not support."""
    by_addr = {r["address"]: r for r in rows}
    seen = set()
    for b in boundaries:
        if b["action"] not in VERBS:
            raise Refusal("line %d: unknown action %r (verbs: %s)" % (b["line"], b["action"], ", ".join(VERBS)))
        if b["address"] in seen:
            raise Refusal("line %d: %#010x is repaired twice" % (b["line"], b["address"]))
        seen.add(b["address"])

    dropped = {}
    added = []
    for b in boundaries:
        a = b["address"]
        if b["action"] == "resize":
            if a not in by_addr:
                raise Refusal("line %d: resize %#010x, but the map has no row there" % (b["line"], a))
            if not b["size"].isdigit() or int(b["size"]) <= 0:
                raise Refusal("line %d: resize %#010x needs a positive size" % (b["line"], a))
            # --apply is idempotent, so a resize the map already carries is not an error: it is what
            # --check compares against, and re-applying after a Ghidra import must be a no-op.
            by_addr[a]["size"] = int(b["size"])
        elif b["action"] == "rename":
            if a not in by_addr:
                raise Refusal("line %d: rename %#010x, but the map has no row there" % (b["line"], a))
            if not b["target"]:
                raise Refusal("line %d: rename %#010x needs the new name in the target column"
                              % (b["line"], a))
            if b["size"]:
                raise Refusal("line %d: rename %#010x carries a size; a rename moves no edge"
                              % (b["line"], a))
            # Idempotent: a rename the map already carries is what --check compares against.
            by_addr[a]["name"] = b["target"]
        elif b["action"] == "drop":
            try:
                target = int(b["target"], 16)
            except ValueError:
                raise Refusal("line %d: drop %#010x needs the target body it is interior to" % (b["line"], a))
            if target not in by_addr:
                raise Refusal("line %d: drop target %#010x is not a map row" % (b["line"], target))
            # A drop the map has already taken is not an error: --apply folds it once and stays idempotent,
            # and the containment test below still has to hold against the target's repaired span.
            if a in by_addr:
                dropped[a] = target
        else:  # add: the target column carries the name, since an added body has no map row to name it
            if a in by_addr:
                # present: either this repair has already been applied, or the map disagreed with it
                have = by_addr[a]
                if have["name"] != (b["target"] or "") or str(have["size"]) != b["size"]:
                    raise Refusal("line %d: the map already has %#010x as %s/%d, not %s/%s"
                                  % (b["line"], a, have["name"], have["size"], b["target"], b["size"]))
                continue
            if not b["size"].isdigit() or int(b["size"]) <= 0:
                raise Refusal("line %d: add %#010x needs a positive size" % (b["line"], a))
            if not b["target"]:
                raise Refusal("line %d: add %#010x needs a name in the target column" % (b["line"], a))
            added.append({"address": a, "name": b["target"], "size": int(b["size"]), "module": ""})

    # Every drop -- taken now or taken by an earlier apply -- must land strictly inside the span the target
    # repair gives it, so a fold can never delete bytes the target does not claim.
    for b in boundaries:
        if b["action"] != "drop":
            continue
        a, target = b["address"], int(b["target"], 16)
        if target in dropped:
            raise Refusal("drop %#010x names %#010x, which is itself dropped" % (a, target))
        t = by_addr[target]
        if not (t["address"] < a < t["address"] + t["size"]):
            raise Refusal("drop %#010x does not land inside %#010x's repaired span (%#010x..%#010x)"
                          % (a, target, t["address"], t["address"] + t["size"]))

    out = [r for r in rows if r["address"] not in dropped] + added
    out.sort(key=lambda r: r["address"])

    # A repair may not run into the row after it. The map already holds rows whose Ghidra body overlaps the
    # next start (15 of them); those are not this file's business, but a row it touches must not join them.
    touched = set(b["address"] for b in boundaries if b["action"] != "rename") | set(dropped.values())
    for i, r in enumerate(out):
        if r["address"] in touched and i + 1 < len(out):
            if r["address"] + r["size"] > out[i + 1]["address"]:
                raise Refusal("%#010x (%s) now runs to %#010x, past the next row at %#010x"
                              % (r["address"], r["name"], r["address"] + r["size"], out[i + 1]["address"]))

    # An added body must not sit inside one that is already there.
    for r in added:
        for o in out:
            if o is r:
                continue
            if o["address"] <= r["address"] < o["address"] + o["size"]:
                raise Refusal("add %#010x lands inside %#010x (%s)" % (r["address"], o["address"], o["name"]))

    # An `add` is a body a fresh import cannot see, so the repair has to be paired with the row that
    # creates it: `XbePost.java` builds a function from `xbe_overrides.csv`, and an `add` without one is
    # a row the exporter reports as missing on every round (U37).
    for b in boundaries:
        if b["action"] != "add":
            continue
        got = override_sizes.get(b["address"])
        if got is None:
            raise Refusal("line %d: add %#010x has no row in xbe_overrides.csv, so a fresh import holds no "
                          "function there (the override row is what creates it)" % (b["line"], b["address"]))
        if got[0] != int(b["size"]) or got[1] != b["target"]:
            raise Refusal("line %d: add %#010x (%s/%s) and xbe_overrides.csv (%s/%s) disagree"
                          % (b["line"], b["address"], b["target"], b["size"], got[1], got[0]))

    # The reviewed evidence and the map agree, or the file that records the size is decoration.
    for addr, (size, name) in sorted(override_sizes.items()):
        if addr not in {r["address"] for r in out}:
            raise Refusal("xbe_overrides.csv records %s (%#010x), which the map does not place" % (name, addr))
        got = [r["size"] for r in out if r["address"] == addr][0]
        if got != size:
            raise Refusal("xbe_overrides.csv records %s (%#010x) as %d bytes, the map says %d"
                          % (name, addr, size, got))
    return out


def report(old, new, boundaries):
    """One line per repair, with what moved, so a reader can audit the apply."""
    before = {r["address"]: r for r in old}
    after = {r["address"]: r for r in new}
    out = []
    for b in boundaries:
        a = b["address"]
        if b["action"] == "resize":
            o, n = before[a], after[a]
            out.append("resize %#010x %-32s %5d -> %5d (%#010x..%#010x)" %
                       (a, o["name"], o["size"], n["size"], n["address"], n["address"] + n["size"]))
        elif b["action"] == "drop":
            t = after[int(b["target"], 16)]
            if a in before:
                out.append("drop   %#010x %-32s %5d bytes folded into %s, now %d" %
                           (a, before[a]["name"], before[a]["size"], t["name"], t["size"]))
            else:
                out.append("drop   %#010x %-32s already folded into %s" % (a, "(no row)", t["name"]))
        elif b["action"] == "rename":
            o, n = before[a], after[a]
            out.append("rename %#010x %-32s -> %s" % (a, o["name"], n["name"]))
        else:
            n = after[a]
            out.append("add    %#010x %-32s %5d bytes (no row before)" % (a, n["name"], n["size"]))
    return out


def load():
    rows = read_map(MAP)
    boundaries = read_boundaries(BOUNDARIES)
    # repaired on copies: `rows` stays the before-state, which the apply report prints
    return rows, boundaries, apply_repairs([dict(r) for r in rows], boundaries, read_sizes(OVERRIDES))


def cmd_apply():
    rows, boundaries, new = load()
    for line in report(rows, new, boundaries):
        print("  " + line)
    with open(MAP, "wb") as fh:
        fh.write(render(new))
    print("wrote symbols/xbox_functions.csv: %d functions, %d bytes of code"
          % (len(new), sum(r["size"] for r in new)))
    return 0


def cmd_check():
    try:
        rows, boundaries, new = load()
    except Refusal as exc:
        print("FAIL symbol-boundaries: %s" % exc)
        return 1
    with open(MAP, "rb") as fh:
        committed = fh.read()
    if committed != render(new):
        # Find the first repair the committed map does not carry, rather than diffing 15k lines.
        have = {r["address"]: r["size"] for r in rows}
        have_names = {r["address"]: r["name"] for r in rows}
        for b in boundaries:
            if b["action"] == "add" and b["address"] not in have:
                print("FAIL symbol-boundaries: the map has no row for %#010x (%s) -- run --apply"
                      % (b["address"], b["action"]))
                return 1
            if b["action"] == "resize" and have.get(b["address"]) != int(b["size"]):
                print("FAIL symbol-boundaries: %#010x is %s bytes, the repair says %s -- run --apply"
                      % (b["address"], have.get(b["address"]), b["size"]))
                return 1
            if b["action"] == "drop" and b["address"] in have:
                print("FAIL symbol-boundaries: %#010x is still a separate row -- run --apply" % b["address"])
                return 1
            if b["action"] == "rename" and have_names.get(b["address"]) != b["target"]:
                print("FAIL symbol-boundaries: %#010x is %s, the rename says %s -- run --apply"
                      % (b["address"], have_names.get(b["address"]), b["target"]))
                return 1
        print("FAIL symbol-boundaries: symbols/xbox_functions.csv is not byte-identical to the repaired map")
        return 1
    print("ok   symbol-boundaries: %d repairs, %d functions, %d bytes of code"
          % (len(boundaries), len(new), sum(r["size"] for r in new)))
    return 0


def cmd_selftest():
    """Every verb, and every refusal, on a synthetic map: no dump, no writes."""
    def row(a, n, s):
        return {"address": a, "name": n, "size": s, "module": ""}

    base = [row(0x1000, "Body_A", 0x100), row(0x1100, "Assert_A", 0x20), row(0x1120, "Body_B", 0x40),
            row(0x2000, "Body_C", 0x10)]

    def b(action, addr, target="", size=""):
        return {"line": 2, "address": addr, "action": action, "target": target, "size": size,
                "evidence": "synthetic"}

    failures = []

    def expect(label, fn, ok):
        try:
            fn()
        except Refusal:
            if ok:
                failures.append(label + ": refused, but is legal")
            return
        if not ok:
            failures.append(label + ": accepted, but is illegal")

    # the four verbs, together: 0x1100 folds into a grown 0x1000, 0x1200 is a body with no row, 0x2000 is
    # renamed, and 0x1120 stays a body of its own -- the repair moves one edge, it does not absorb what follows
    good = [b("resize", 0x1000, size="288"), b("drop", 0x1100, target="0x1000"),
            b("add", 0x1200, target="FUN_00001200", size="24"),
            b("rename", 0x2000, target="thunk_Body_C")]
    got = apply_repairs([dict(r) for r in base], good, {0x1200: (24, "FUN_00001200")})
    want = [(0x1000, "Body_A", 288), (0x1120, "Body_B", 64), (0x1200, "FUN_00001200", 24),
            (0x2000, "thunk_Body_C", 16)]
    if [(r["address"], r["name"], r["size"]) for r in got] != want:
        failures.append("verbs: got %r" % [(r["address"], r["name"], r["size"]) for r in got])

    # a rename the map already carries is what --check compares against, so it is legal to re-apply
    expect("rename already applied", lambda: apply_repairs(
        [dict(r) for r in base], [b("rename", 0x2000, target="thunk_Body_C")], {}), True)
    expect("rename to the same name", lambda: apply_repairs(
        [dict(r) for r in base], [b("rename", 0x2000, target="Body_C")], {}), True)
    expect("rename a row that is not there", lambda: apply_repairs(
        [dict(r) for r in base], [b("rename", 0x1500, target="Gone")], {}), False)
    expect("rename with no name", lambda: apply_repairs(
        [dict(r) for r in base], [b("rename", 0x2000, target="")], {}), False)
    expect("rename that carries a size", lambda: apply_repairs(
        [dict(r) for r in base], [b("rename", 0x2000, target="X", size="16")], {}), False)
    expect("add with no override row", lambda: apply_repairs(
        [dict(r) for r in base], [b("add", 0x1200, target="FUN_00001200", size="24")], {}), False)
    expect("add whose override row disagrees", lambda: apply_repairs(
        [dict(r) for r in base], [b("add", 0x1200, target="FUN_00001200", size="24")],
        {0x1200: (32, "FUN_00001200")}), False)

    expect("drop outside its target", lambda: apply_repairs(
        [dict(r) for r in base], [b("drop", 0x1120, target="0x1000")], {}), False)
    expect("resize past the next row", lambda: apply_repairs(
        [dict(r) for r in base], [b("resize", 0x1000, size="257")], {}), False)
    expect("add where a row already is, with another shape", lambda: apply_repairs(
        [dict(r) for r in base], [b("add", 0x1100, target="FUN_00001100", size="8")], {}), False)
    expect("add already applied", lambda: apply_repairs(
        [dict(r) for r in base], [b("add", 0x1120, target="Body_B", size="64")],
        {0x1120: (64, "Body_B")}), True)
    expect("drop a row that is not there", lambda: apply_repairs(
        [dict(r) for r in base], [b("drop", 0x1500, target="0x1000")], {}), False)
    expect("unknown verb", lambda: apply_repairs(
        [dict(r) for r in base], [b("merge", 0x1100, target="0x1000")], {}), False)
    expect("two repairs of one address", lambda: apply_repairs(
        [dict(r) for r in base], [b("resize", 0x2000, size="32"), b("drop", 0x2000, target="0x1000")], {}), False)
    expect("evidence the size disagrees with", lambda: apply_repairs(
        [dict(r) for r in base], [], {0x1000: (153, "Body_A")}), False)
    expect("a size row the map does not place", lambda: apply_repairs(
        [dict(r) for r in base], [], {0x9000: (16, "Ghost")}), False)

    # the render round-trips through the reader, CRLF and all
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "map.csv")
        with open(path, "wb") as fh:
            fh.write(render(got))
        again = read_map(path)
        if [(r["address"], r["name"], r["size"]) for r in again] != want:
            failures.append("render/read round trip")
        if open(path, "rb").read().count(b"\r\n") != len(want) + 1:
            failures.append("render: line endings")
    finally:
        shutil.rmtree(tmp)

    if failures:
        for f in failures:
            print("FAIL symbol-boundaries: %s" % f)
        return 1
    print("ok   symbol-boundaries: 4 verbs, 14 refusals, idempotent apply, render round trip")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="rewrite symbols/xbox_functions.csv")
    ap.add_argument("--check", action="store_true", help="does the committed map carry the repairs?")
    ap.add_argument("--selftest", action="store_true", help="synthetic map: every verb and refusal, no dump")
    args = ap.parse_args()
    if args.selftest:
        return cmd_selftest()
    if args.apply:
        try:
            return cmd_apply()
        except Refusal as exc:
            print("FAIL symbol-boundaries: %s" % exc)
            return 1
    if args.check:
        return cmd_check()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
