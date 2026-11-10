#!/usr/bin/env python3
"""Every `native()` binding name the image carries, and which declarations it can bind (docs/UNKNOWNS.md U1).

A `native` function with `iNative == 0` claims no dispatch slot: the routine that fills `UFunction::Func`
(`0x0003a030`) formats a name with `"int%sexec%s"`, looks it up and stores the result, so the binding is by
*name*. U1's last open question was what those two `%s` hold and whether the lookup can succeed in a shipped
build with no map or PDB.

Both are measurable without a boot, because the names are **in the image**: the `.rdata` string pool carries them
as wide literals, run together and separated by NULs --

    ..intAActorexecKWake..intAActorexecKIsAwake..intUGUIControllerexecGetMapList..

-- so the second `%s` is the function's own name, the first is a class prefix (`A` for an `Actor` descendant, `U`
otherwise) followed by the class name, and `exec` is a literal in the format rather than a mark of an `exec`
function: `Actor.KWake` is not `exec`. This tool extracts that pool, joins it with `inventory/natives.csv`, and
reports which declarations have a name to bind to.

    python3 tools/native-binder.py DUMP/default.xbe -o inventory
    python3 tools/native-binder.py DUMP/default.xbe            # summary only
    python3 tools/native-binder.py --selftest                  # name parsing only, no dump

The XBE is the input; the tool prints names and counts, and writes one CSV of the join.
"""
import argparse
import array
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

NATIVES = os.path.join(ROOT, "inventory", "natives.csv")
CLASSES = os.path.join(ROOT, "inventory", "classes.csv")
# `int` + prefix + class + `exec` + function, all of it word characters. Bounded on both sides below, because the
# pool packs the strings back to back and a greedy match would otherwise swallow the NUL separator's neighbours.
BINDER = re.compile(r"int([AU][A-Za-z0-9_]+?)exec([A-Za-z0-9_]+)")
WORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def binder_names(text):
    """Every binder name in a decoded UTF-16 string, as {name: first character index}."""
    out = {}
    for m in BINDER.finditer(text):
        if m.start() and text[m.start() - 1] in WORD:
            continue
        if m.end() < len(text) and text[m.end()] in WORD:
            continue
        out.setdefault(m.group(0), m.start())
    return out


def split_binder(name):
    """`intAActorexecKWake` -> ('A', 'Actor', 'KWake')."""
    m = BINDER.fullmatch(name)
    if not m:
        raise ValueError("not a binder name: %r" % name)
    prefix, func = m.group(1), m.group(2)
    return prefix[0], prefix[1:], func


def wide_text(data):
    """The image as one character per 16-bit unit, so character `i` is the unit at file offset `2*i`.

    Decoding the file as UTF-16LE would be the obvious way and is wrong: a surrogate pair collapses two units into
    one character, so every later index drifts, and `binder_va` (which is `index * 2` mapped to a VA) came out short
    by up to 0xb9a -- far enough that no code or table reference to a name could be found. Lone surrogates are legal
    in a `str` and never match the binder pattern, so no replacement is needed.
    """
    units = array.array("H")
    units.frombytes(data[:len(data) // 2 * 2])
    return "".join(map(chr, units))


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def actor_classes():
    """Classes whose ancestor chain reaches `Actor`, from the committed class graph."""
    try:
        rows = read_csv(CLASSES)
    except FileNotFoundError:
        return set()
    supers = {r["class"]: r.get("super") or "" for r in rows}
    out = set()
    for cls in supers:
        cur, seen = cls, set()
        while cur and cur not in seen:
            if cur == "Actor":
                out.add(cls)
                break
            seen.add(cur)
            cur = supers.get(cur, "")
    return out


def selftest():
    cases = [
        ("intAActorexecKWake", ("A", "Actor", "KWake")),
        ("intUGUIControllerexecGetMapList", ("U", "GUIController", "GetMapList")),
        ("intAStargatePlayerexecSetSightRadius", ("A", "StargatePlayer", "SetSightRadius")),
        ("intUCanvasexecStrLen", ("U", "Canvas", "StrLen")),
    ]
    bad = 0
    for name, want in cases:
        got = split_binder(name)
        if got != want:
            bad += 1
            print("  FAIL %-40s got %r want %r" % (name, got, want))
    # A pool string closed by a NUL on both sides, and one that a greedy scan must not extend into its neighbour.
    text = "\x00intAActorexecKWake\x00intAActorexecKIsAwake\x00"
    names = binder_names(text)
    for name in ("intAActorexecKWake", "intAActorexecKIsAwake"):
        if name not in names:
            bad += 1
            print("  FAIL pool: %s not extracted" % name)
    # A fragment inside a longer word is not a name of its own.
    if binder_names("xxintAActorexecKWakexx"):
        bad += 1
        print("  FAIL pool: matched inside a longer word")
    print("native-binder: %d name rules" % (len(cases) + 3 - bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?", help="the dump's default.xbe")
    ap.add_argument("--selftest", action="store_true", help="name parsing only; needs no dump")
    ap.add_argument("-o", "--out", help="write the join CSV here")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.xbe:
        sys.exit("native-binder: pass DUMP/default.xbe, or --selftest")

    x = Xbe(args.xbe)
    data = open(args.xbe, "rb").read()
    # The names are wide literals in one pool: decode the whole image as UTF-16LE, which leaves ASCII code bytes as
    # CJK-looking text between them and keeps the pool intact.
    text = wide_text(data)
    found = binder_names(text)
    print("image: %d distinct binder names" % len(found))

    by_names = {n: split_binder(n) for n in found}
    decls = read_csv(NATIVES)
    actors = actor_classes()
    rows = []
    no_index = with_index = 0
    missing = []
    for d in decls:
        token = d.get("native_token", "0")
        prefix = None
        for cand in ("A", "U"):
            if "int%s%s%s" % (cand, d["class"], "exec" + d["function"]) in by_names:
                prefix = cand
                break
        name = "int%s%sexec%s" % (prefix, d["class"], d["function"]) if prefix else ""
        va = ""
        if prefix:
            off = found[name] * 2
            try:
                va = "0x%x" % x.offset_to_va(off)
            except Exception:
                va = ""
        # The class graph decides what the prefix *should* be; the pool says what it is.
        want = "A" if d["class"] in actors else "U"
        rows.append({"package": d["package"], "class": d["class"], "function": d["function"],
                     "native_token": token, "binder": name, "prefix": prefix or "",
                     "prefix_expected": want, "binder_va": va})
        if token in ("0", "", "0x0"):
            no_index += 1
            if not prefix:
                missing.append(d)
        else:
            with_index += 1

    declared = sum(1 for r in rows if r["binder"])
    print("declarations: %d (%d with an index, %d without)" % (len(rows), with_index, no_index))
    print("  a binder name in the image: %d (%.1f%%)" % (declared, 100.0 * declared / max(1, len(rows))))
    print("  of the %d without an index: %d have a name, %d do not"
          % (no_index, no_index - len(missing), len(missing)))
    pref = sum(1 for r in rows if r["prefix"] and r["prefix"] == r["prefix_expected"])
    print("  prefix agrees with the class graph: %d of %d named rows" % (pref, declared))
    unnamed = [n for n in by_names if not any(r["binder"] == n for r in rows)]
    print("  names in the image with no declaration in the 18 packages: %d" % len(unnamed))
    for n in unnamed[:5]:
        print("    %s" % n)
    if missing:
        print("  no name in the image (the lookup cannot succeed for these), first 10:")
        for d in missing[:10]:
            print("    %s.%s.%s" % (d["package"], d["class"], d["function"]))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "native_binder_names.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["package", "class", "function", "native_token", "binder",
                                              "prefix", "prefix_expected", "binder_va"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
