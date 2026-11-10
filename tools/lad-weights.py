"""What the runtime does with a slot after the sampler: the consumer's zero gate (docs/formats/lipsinc-runtime.md, U4).

`0x000b5f10` (the pose-pair blend) and `0x000b63f0` (the fade state machine) both finish a track the
same way: for each pose record they ask the single-slot accessor `0x000b4710` for that record's curve
value, store it at record `+0x40`, and call the pose builder `0x000b29a0` **only when the value is not
zero** -- `fld [0x4c8848]; fucomip; jnp` over the call. So a slot's number is a blend weight whose zero
means "no contribution", and a negative one is applied like any other.

This tool measures the shipped corpus against that gate at the two times the runtime itself uses: `0`,
the global zero at `0x4c8848` that the pose-pair path samples at, and each track's own end. It counts
which slots would be skipped and reports the signed columns separately. It measures what the data asks
of the consumer; it does not claim what a slot means.

    tools/lad-weights.py --dir "$SG1_DUMP/.../LIPSincData" -o inventory
    tools/lad-weights.py --slots                         # the per-slot table across the corpus
    tools/lad-weights.py --check                         # dump-free, validates the committed CSV
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = "lipsinc_weights.csv"
SLOTS = 26
# Slots 19-25 are the signed ones (docs/formats/lipsinc-lad.md); the pose path treats them like any
# other slot, so they are counted here as "signed" only to keep the two populations apart.
SIGNED = tuple(range(19, 26))
COLUMNS = ["path", "track", "slots", "nonzero", "zero", "end_nonzero", "end_zero",
           "signed_nonzero", "signed_negative", "min_zero", "max_zero"]


def _mod(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SAMPLER = None


def sample(keys, t):
    """The measured evaluator, imported once from `lad-sampler.py` (one implementation of the smoothstep)."""
    global _SAMPLER
    if _SAMPLER is None:
        _SAMPLER = _mod("lad_sampler", "lad-sampler.py")
    return _SAMPLER.sample(keys, t)


def _rel(path):
    parts = os.path.abspath(path).split(os.sep)
    if "LIPSincData" in parts:
        return "/".join(parts[parts.index("LIPSincData") + 1:])
    return os.path.basename(path)


def measure_track(groups):
    """One track against the consumer's gate: what is non-zero at the two times the runtime samples."""
    populated = [g for g in groups if g]
    ends = [g[-1][0] for g in populated]
    end = max(ends) if ends else 0
    nonzero = zero = 0
    end_nonzero = end_zero = 0
    signed_nonzero = signed_negative = 0
    values = []
    for i, g in enumerate(groups):
        if not g:
            continue
        v0 = sample(g, 0)[0]
        vE = sample(g, end)[0]
        values.append(v0)
        if v0 == 0.0:
            zero += 1
        else:
            nonzero += 1
            if i in SIGNED:
                signed_nonzero += 1
                if v0 < 0.0:
                    signed_negative += 1
        if vE == 0.0:
            end_zero += 1
        else:
            end_nonzero += 1
    return {"slots": len(populated), "nonzero": nonzero, "zero": zero,
            "end_nonzero": end_nonzero, "end_zero": end_zero,
            "signed_nonzero": signed_nonzero, "signed_negative": signed_negative,
            "min_zero": min(values) if values else 0.0,
            "max_zero": max(values) if values else 0.0}


def measure(paths):
    lt = _mod("lad_tracks", "lad-tracks.py")
    lk = _mod("lad_keys", "lad-keys.py")
    rows = []
    for path in paths:
        d = open(path, "rb").read()
        try:
            recs = lt.measure(path)["_tracks"]
        except Exception as exc:                       # a file the track reader refuses is reported
            print("%s: refused (%s)" % (path, exc), file=sys.stderr)
            continue
        for rec in recs:
            try:
                groups, _names, _stop = lk.key_block(d, rec["keys_offset"],
                                                     rec["keys_offset"] + rec["block"])
            except ValueError:
                continue
            row = {"path": _rel(path), "track": rec["name"]}
            row.update(measure_track(groups))
            rows.append(row)
    return rows


def selftest():
    """`measure_track` on hand-built key blocks: the gate, the empty slot and the signed columns."""
    k = [(0, 1.0), (10, 2.0)]
    m = measure_track([k] * SLOTS)
    assert m["slots"] == SLOTS and m["nonzero"] == SLOTS and m["zero"] == 0, m
    # A slot whose key at tick 0 is exactly zero is skipped by the gate; an empty slot is not a slot.
    m = measure_track([[(0, 0.0), (10, 1.0)]] + [k] * (SLOTS - 1))
    assert m["zero"] == 1 and m["nonzero"] == SLOTS - 1, m
    m = measure_track([[(5, 3.0)]] + [[]] * (SLOTS - 1))
    assert m["slots"] == 1, m                                  # empty groups are not populated
    assert m["nonzero"] == 1 and m["min_zero"] == 3.0, m       # hold-before answers the first key
    # The signed columns are counted apart, and a negative value is still applied (non-zero).
    signed = [(0, -0.5), (10, 0.5)]
    m = measure_track([k] * 19 + [signed] * 7)
    assert m["signed_nonzero"] == 7 and m["signed_negative"] == 7, m
    assert m["nonzero"] == SLOTS and m["zero"] == 0, m
    m = measure_track([k] * 19 + [[(0, 0.0), (10, 0.0)]] * 7)
    assert m["signed_nonzero"] == 0 and m["zero"] == 7, m
    # The snap makes a sub-2^-51 value exactly zero, so the gate sees it as "no contribution".
    m = measure_track([[(0, 3e-16)]] + [k] * (SLOTS - 1))
    assert m["zero"] == 1, m
    return True


def check(path=None):
    """Dump-free contract: the gate arithmetic, plus the committed CSV when there is one."""
    selftest()
    path = path or os.path.join(ROOT, "inventory", CSV)
    if not os.path.exists(path):
        print("lad-weights: gate self-test ok (no %s to cross-check)" % CSV)
        return 0
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    assert rows, "empty %s" % CSV
    bad = [r for r in rows if int(r["nonzero"]) + int(r["zero"]) != int(r["slots"])]
    assert not bad, "nonzero + zero != slots in %d rows" % len(bad)
    bad = [r for r in rows if int(r["end_nonzero"]) + int(r["end_zero"]) != int(r["slots"])]
    assert not bad, "end_nonzero + end_zero != slots in %d rows" % len(bad)
    bad = [r for r in rows if int(r["signed_negative"]) > int(r["signed_nonzero"])]
    assert not bad, "more negative signed slots than non-zero ones in %d rows" % len(bad)
    bad = [r for r in rows if not (0 <= int(r["slots"]) <= SLOTS)]
    assert not bad, "slots outside 0..%d in %d rows" % (SLOTS, len(bad))
    print("lad-weights: %d rows, gate self-test ok" % len(rows))
    return 0


def write_csv(rows, outdir):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, CSV)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["min_zero"] = "%.6g" % r["min_zero"]
            r["max_zero"] = "%.6g" % r["max_zero"]
            w.writerow({k: r[k] for k in COLUMNS})
    return path


def print_slots(files):
    """The per-slot table across the corpus: the gate at 0 and the value range of each of the 26 slots."""
    lk = _mod("lad_keys", "lad-keys.py")
    lt = _mod("lad_tracks", "lad-tracks.py")
    table = [{"populated": 0, "zero": 0, "positive": 0, "negative": 0, "min": None, "max": None}
             for _ in range(SLOTS)]
    for path in files:
        d = open(path, "rb").read()
        for rec in lt.measure(path)["_tracks"]:
            try:
                groups, _n, _s = lk.key_block(d, rec["keys_offset"],
                                              rec["keys_offset"] + rec["block"])
            except ValueError:
                continue
            for i, g in enumerate(groups):
                if not g:
                    continue
                v = sample(g, 0)[0]
                s = table[i]
                s["populated"] += 1
                if v == 0.0:
                    s["zero"] += 1
                elif v < 0.0:
                    s["negative"] += 1
                else:
                    s["positive"] += 1
                s["min"] = v if s["min"] is None else min(s["min"], v)
                s["max"] = v if s["max"] is None else max(s["max"], v)
    print("%-4s %9s %6s %8s %8s %10s %10s" % ("slot", "populated", "zero", "positive",
                                              "negative", "min at 0", "max at 0"))
    for i, s in enumerate(table):
        print("%-4d %9d %6d %8d %8d %10.4g %10.4g"
              % (i, s["populated"], s["zero"], s["positive"], s["negative"],
                 s["min"] or 0.0, s["max"] or 0.0))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="a LIPSincData directory: every .lad below it")
    ap.add_argument("-o", "--out", help="artifact directory (writes %s)" % CSV)
    ap.add_argument("--slots", action="store_true", help="print the per-slot table across the corpus")
    ap.add_argument("--check", nargs="?", const="", help="dump-free self-test (+ the committed CSV)")
    args = ap.parse_args()
    if args.check is not None:
        return check(args.check or None)
    files = list(args.files)
    if args.dir:
        for base, _dirs, names in os.walk(args.dir):
            files += [os.path.join(base, n) for n in sorted(names) if n.lower().endswith(".lad")]
    if not files:
        ap.error("no .lad files given (or --dir)")
    if args.slots:
        return print_slots(files)
    rows = measure(files)
    if args.out:
        print("wrote", write_csv(rows, args.out))
    slots = sum(r["slots"] for r in rows)
    nonzero = sum(r["nonzero"] for r in rows)
    zero = sum(r["zero"] for r in rows)
    print("%-28s %6s %6s %8s %8s %7s" % ("file", "tracks", "slots", "nonzero", "zero", "gate%"))
    by_file = collections.OrderedDict()
    for r in rows:
        by_file.setdefault(os.path.basename(r["path"]), []).append(r)
    for name, rs in by_file.items():
        s = sum(x["slots"] for x in rs)
        nz = sum(x["nonzero"] for x in rs)
        print("%-28s %6d %6d %8d %8d %6.1f%%" % (name, len(rs), s, nz, s - nz,
                                                100.0 * nz / s if s else 0.0))
    print("total %d tracks, %d populated slots, %d applied (%.1f%%), %d skipped (%.1f%%)"
          % (len(rows), slots, nonzero, 100.0 * nonzero / slots if slots else 0.0,
             zero, 100.0 * zero / slots if slots else 0.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
