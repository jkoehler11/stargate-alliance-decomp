#!/usr/bin/env python3
"""Score a rule change **per body** against a baseline run of the emitter -- never by the differ count.

`inventory/uscript_emit.csv` counts what one run modelled; it cannot say which bodies a new rule moved, and the
differ count cannot either: a body gained *out of a refusal* leaves `emit_differ.csv` flat, and a body that was
refused before and emits-but-differs now moves *into* the differ set without ever having been modelled. So the only
honest score of a rule change is a join of two runs' own artifacts, keyed by body:

    (package, class, state, function, source)

The **source** is part of the key because a class can carry two functions of one name: `PlayerController.
CallServerMove` is two bodies in this corpus (166 and 146 bytes), and a key without it reported one of them lost and
the other gained on every run.

    python3 tools/emit-score.py --base ~/sg1/work/emit-base --new ~/sg1/work/emit-new
    python3 tools/emit-score.py --check          # synthetic, no dump, writes nothing

Each directory is one `uscript-emit.py` run: `<dir>/art/uscript_emit.csv` (or `<dir>/uscript_emit.csv`) and
`<dir>/residue.csv`. A modelled row is a body the run wrote **byte-identically**, so:

* **gained** -- modelled now, not at the baseline: the point of the change;
* **lost** -- modelled at the baseline, not now. Every one is a regression (the baseline only models identical
  bodies), and its **new** reason says which kind: `an emitted body that differs` is a wrong rule, any other reason
  is a new refusal. The tool exits 1 on any loss;
* **newly differing** -- a residue body at both runs whose reason moved *to* `an emitted body that differs`: the
  emitter now reaches it and writes the wrong bytes, which is also a regression (the exit 1 covers it);
* **later refusal** -- a residue body at both runs whose reason moved to a different refusal. Progress, and the only
  category that may be quoted as such without a body count beside it.

An earlier copy of this rule read the *baseline* row's reason for a loss; that is the wrong side of the join: a
modelled body has no baseline residue row at all, so every loss looked like "no baseline row".
"""

import argparse
import collections
import csv
import os
import sys
import tempfile

FIELDS = ("package", "class", "state", "function")
DIFFERS = "an emitted body that differs"


def _key(row):
    return tuple((row.get(f) or "") for f in FIELDS) + (row.get("source") or "",)


def load(directory):
    """`(modelled keys, residue {key: reason})` for one run directory."""
    emit = os.path.join(directory, "art", "uscript_emit.csv")
    if not os.path.exists(emit):
        emit = os.path.join(directory, "uscript_emit.csv")
    modelled = set()
    with open(emit, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("model") == "census":
                continue
            modelled.add(_key(r))
    residue = {}
    path = os.path.join(directory, "residue.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                residue.setdefault(_key(r), r.get("reason") or "")
    return modelled, residue


def score(base, new):
    """`(gained, lost, kept, newly_differing, later_refusals, base_residue, new_residue)`."""
    mb, rb = load(base)
    mn, rn = load(new)
    gained = sorted(mn - mb)
    lost = sorted(mb - mn)
    kept = mb & mn
    newly_differing = sorted(k for k, reason in rn.items()
                             if reason == DIFFERS and rb.get(k) != DIFFERS and k not in mn)
    later = sorted(k for k, reason in rn.items()
                   if k in rb and reason != rb[k] and reason != DIFFERS and k not in mn)
    return gained, lost, kept, newly_differing, later, rb, rn


def render(base, new, gained, lost, kept, newly_differing, later, base_residue, new_residue):
    mb, _ = load(base)
    mn, _ = load(new)
    lines = ["modelled %d -> %d (%+d), gained %d, lost %d, kept %d, newly differing %d, later refusals %d"
             % (len(mb), len(mn), len(mn) - len(mb), len(gained), len(lost), len(kept),
                len(newly_differing), len(later))]
    for key in gained:
        lines.append("  gained %s.%s" % (key[1], key[3]))
    if lost:
        counts = collections.Counter(new_residue.get(k, "(no row)") for k in lost)
        lines.append("lost bodies, by the reason the new run gives:")
        for reason, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append("  %4d  %s" % (n, reason))
        for key in lost:
            lines.append("  LOST %s.%s  now=%s" % (key[1], key[3], new_residue.get(key, "(no row)")))
    for key in newly_differing:
        lines.append("  DIFFERS %s.%s  baseline=%s" % (key[1], key[3], base_residue.get(key, "(modelled)")))
    for key in later:
        lines.append("  later %s.%s  %s -> %s" % (key[1], key[3], base_residue[key], new_residue[key]))
    if lost or newly_differing:
        lines.append("FAIL: %d loss(es) and %d newly differing body(ies)" % (len(lost), len(newly_differing)))
    return "\n".join(lines)


def self_check():
    """Synthetic: a gain, a loss, a body that regresses to `differs`, one that moves to a later refusal, an
    overloaded name, and the render's totals."""
    bad = []
    root = tempfile.mkdtemp()
    try:
        def write(directory, modelled, residue):
            os.makedirs(os.path.join(directory, "art"))
            with open(os.path.join(directory, "art", "uscript_emit.csv"), "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=("package", "class", "state", "function", "source", "model"))
                w.writeheader()
                for package, cls, fn, src in modelled:
                    w.writerow({"package": package, "class": cls, "state": "", "function": fn,
                                "source": src, "model": "call"})
            with open(os.path.join(directory, "residue.csv"), "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=("package", "class", "state", "function", "source", "reason"))
                w.writeheader()
                for (package, cls, fn, src), reason in residue:
                    w.writerow({"package": package, "class": cls, "state": "", "function": fn,
                                "source": src, "reason": reason})

        base = os.path.join(root, "base")
        new = os.path.join(root, "new")
        keep = ("A.u", "Cls", "Keep", "return 1;")
        gain = ("A.u", "Cls", "Gain", "return 2;")
        lose = ("A.u", "Cls", "Lose", "return 3;")
        over1 = ("A.u", "Cls", "Over", "return 4;")
        over2 = ("A.u", "Cls", "Over", "return 5;")
        regress = ("A.u", "Cls", "Regress", "return 6;")
        later = ("A.u", "Cls", "Later", "return 7;")
        write(base, [keep, lose, over1],
              [(gain, "a compound expression, which needs the statement walk"),
               (over2, DIFFERS), (regress, "a compound expression, which needs the statement walk"),
               (later, "a compound expression, which needs the statement walk")])
        write(new, [keep, gain, over1, over2],
              [(lose, "a call the statement walk cannot bound"), (regress, DIFFERS),
               (later, "a call the statement walk cannot bound")])
        gained, lost, kept, newly, late, rb, rn = score(base, new)
        if [k[3] for k in gained] != ["Gain", "Over"]:
            bad.append("gained reads %r" % (gained,))
        if [k[3] for k in lost] != ["Lose"]:
            bad.append("lost reads %r -- the overload at `Over` must stay one body per source" % (lost,))
        if [k[3] for k in newly] != ["Regress"] or [k[3] for k in late] != ["Later"]:
            bad.append("newly differing %r / later %r" % (newly, late))
        if len(kept) != 2:
            bad.append("kept reads %d" % len(kept))
        text = render(base, new, gained, lost, kept, newly, late, rb, rn)
        if "modelled 3 -> 4 (+1), gained 2, lost 1, kept 2, newly differing 1, later refusals 1" not in text:
            bad.append("the render does not carry the totals it was handed")
        if "FAIL: 1 loss(es) and 1 newly differing body(ies)" not in text:
            bad.append("the render does not fail on a loss")
    finally:
        for name in os.listdir(root):
            art = os.path.join(root, name, "art")
            for entry in os.listdir(art):
                os.unlink(os.path.join(art, entry))
            os.rmdir(art)
            os.unlink(os.path.join(root, name, "residue.csv"))
            os.rmdir(os.path.join(root, name))
        os.rmdir(root)
    for line in bad:
        print("FAIL emit-score: %s" % line)
    if not bad:
        print("ok   emit-score: gained, lost, regressed-to-differs, later refusal, an overloaded name, and the render")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--new")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if not (args.base and args.new):
        ap.error("--base and --new are both required (or use --check)")
    gained, lost, kept, newly, late, rb, rn = score(args.base, args.new)
    print(render(args.base, args.new, gained, lost, kept, newly, late, rb, rn))
    return 1 if (lost or newly) else 0


if __name__ == "__main__":
    sys.exit(main())
