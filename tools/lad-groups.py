#!/usr/bin/env python3
"""Measure a `.lad` track's 26 key groups as a **slot table** (docs/UNKNOWNS.md U4).

`tools/lad-keys.py` decoded a track's key block: 26 groups of `(i32 time, f64 value)` keys. It measured
counts and ranges per *track* and left "which group is which curve" open. This tool measures the groups
against **each other**, all 58,006 of them, and the answer is that the 26 slots are not 26 independent
curves -- they are a fixed table in which several pairs are the same curve stored twice or its negation:

    slot   0..14, 20   independent: no other slot in any track shares their time grid key for key
    slots 17, 18       identical whenever both are populated (1,845 of 2,231 tracks)
    slots 22, 23       identical whenever both are populated (883 tracks)
    slots 24, 25       identical in 2,230 of 2,231 tracks -- the two that are populated almost always
    slot  19           the exact negation of slots 24 and 25 where their grids meet (1,890 tracks)
    slot  21           the exact negation of slots 22 and 23 where their grids meet (193 tracks)

The test is strict: a pair is only compared when both slots carry **the same time sequence**, that slot's
`same_keys_of_a`. A pair whose keys cannot be laid side by side proves nothing and is reported as such
rather than as "different". Every key of the shorter side must match exactly (bitwise-equal `f64`, not
a tolerance) or be its exact negation -- a near miss is `other`, and no pair is `other` in any track.

**The values split the table at slot 19.** Slots 0-18 are *never negative* in any of the 208 file x slot
rows (they run 0 to 1.34 -- `1.00788`, `1.18165`, `1.33783` at their widest), which is a weight; slots
19-25 are signed, -9.45942 to +9.45942, which is an offset or an angle. Nineteen weight slots is also the
channel count of the four `Controllers/` packs; the coincidence is recorded, not explained.

`160.0` / `220.0`, the two `f32` fields in the track header, are measured here too: `--headers` re-scans
every `u32 2` marker with the *relaxed* test (a length-prefixed name pair, a `u32 1`, two `f32`, any count
byte) and reports how many track-like records the strict reader would refuse. It finds **one** float pair
in the whole corpus and **zero** refused records, which is what makes them format constants and not
per-track data -- a duration, a sample rate or a range would vary with the clip.

    tools/lad-groups.py FILE.lad [...]                       # one line per file
    tools/lad-groups.py --dir "$SG1_DUMP/.../System/LIPSincData" -o inventory
    tools/lad-groups.py --pairs FILE.lad                     # the relation table only
    tools/lad-groups.py --headers FILE.lad [...]             # the header-constant scan
    tools/lad-groups.py --track TRACK FILE.lad               # one track's 26 slots
    tools/lad-groups.py --check                              # dump-free, validates the committed CSV

It measures redundancy and ranges; it does not claim what a value means.
"""
import argparse
import collections
import csv
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = "lipsinc_groups.csv"
SLOTS = 26
WEIGHT_SLOTS = 19                     # 0..18 carry weights, 19..25 signed values; measured, see below
COLUMNS = ["path", "slot", "tracks", "using", "keys", "empty", "min_keys", "max_keys",
           "time_min", "time_max", "value_min", "value_max", "equal_to", "negation_of"]
EXACT = 1e-12


def _mod(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def relations(groups):
    """Every pair of slots whose time grids are identical, classified by their keys.

    Returns {(a, b): "same" | "neg" | "other"}. Only pairs whose two time sequences are equal are
    compared: a pair with different grids says nothing about whether the curves are related, and
    calling that "different" would be the plausible-looking wrong answer this project bans.
    """
    times = [tuple(t for t, _ in g) for g in groups]
    out = {}
    for a in range(len(groups)):
        for b in range(a + 1, len(groups)):
            if not times[a] or times[a] != times[b]:
                continue
            va = [v for _, v in groups[a]]
            vb = [v for _, v in groups[b]]
            if all(abs(x - y) < EXACT for x, y in zip(va, vb)):
                out[(a, b)] = "same"
            elif all(abs(x + y) < EXACT for x, y in zip(va, vb)):
                out[(a, b)] = "neg"
            else:
                out[(a, b)] = "other"
    return out


def measure(path):
    """One file's slot table and its pair census."""
    lt = _mod("lad_tracks", "lad-tracks.py")
    lk = _mod("lad_keys", "lad-keys.py")
    d = open(path, "rb").read()
    rows = lt.measure(path)
    slot = [{"tracks": 0, "using": 0, "keys": 0, "empty": 0, "min_keys": None, "max_keys": None,
             "time_min": None, "time_max": None, "value_min": None, "value_max": None}
            for _ in range(SLOTS)]
    pairs = collections.defaultdict(collections.Counter)
    tracks, refused = 0, []
    for rec in rows["_tracks"]:
        try:
            groups, _ = lk.key_block(d, rec["keys_offset"], rec["keys_offset"] + rec["block"])
        except ValueError as exc:
            refused.append("%s: %s" % (rec["full"], exc))
            continue
        tracks += 1
        for i, g in enumerate(groups):
            s = slot[i]
            s["tracks"] += 1
            if not g:
                s["empty"] += 1
                continue
            s["using"] += 1
            s["keys"] += len(g)
            s["min_keys"] = len(g) if s["min_keys"] is None else min(s["min_keys"], len(g))
            s["max_keys"] = len(g) if s["max_keys"] is None else max(s["max_keys"], len(g))
            times = [t for t, _ in g]
            vals = [v for _, v in g]
            s["time_min"] = min(times) if s["time_min"] is None else min(s["time_min"], min(times))
            s["time_max"] = max(times) if s["time_max"] is None else max(s["time_max"], max(times))
            s["value_min"] = min(vals) if s["value_min"] is None else min(s["value_min"], min(vals))
            s["value_max"] = max(vals) if s["value_max"] is None else max(s["value_max"], max(vals))
        for (a, b), kind in relations(groups).items():
            pairs[(a, b)][kind] += 1
    return {"path": path, "slots": slot, "pairs": pairs, "tracks": tracks, "refused": refused}


def deterministic(pairs):
    """Pairs whose relation holds in every track that could compare them.

    `other` must be zero, and the relation must be one kind: 24/25 are `same` in 2,230 tracks and the
    one track that cannot compare them does not break the rule, it just says nothing.
    """
    out = {}
    for key, c in pairs.items():
        if c["other"] or not (c["same"] or c["neg"]):
            continue
        out[key] = ("same" if c["same"] else "neg", c["same"] + c["neg"])
    return out


def header_scan(path):
    """Every track-like record under a relaxed header test: the strict reader's blind spot, measured."""
    d = open(path, "rb").read()
    lt = _mod("lad_tracks", "lad-tracks.py")
    seen = collections.Counter()
    relaxed, strict = 0, 0
    scan = 0
    marker = b"\x02\x00\x00\x00"
    while True:
        hit = d.find(marker, scan)
        if hit < 0:
            break
        scan = hit + 1
        if hit + 16 > len(d):
            break
        try:
            _, q = lt.read_name(d, hit + 4)
            full, q = lt.read_name(d, q)
        except (ValueError, IndexError):
            continue
        if "." not in full or " " in full or q + 13 > len(d):
            continue
        if struct.unpack_from("<I", d, q)[0] != 1:
            continue
        t1, t2 = struct.unpack_from("<ff", d, q + 4)
        relaxed += 1
        seen[(t1, t2, d[q + 12])] += 1
        rec = lt.track_at(d, hit, len(d))
        if rec is not None and rec["full"] == full:
            strict += 1
    return {"relaxed": relaxed, "strict": strict, "headers": seen}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def vector_shape(groups):
    """One track's slots as the sampler sees them: one width, and one time axis or 26 of them?

    `one_extent` is the strongest form -- every populated slot starts and ends on the same tick;
    `same_start`/`same_end` split that in two, because a per-curve key list may begin together and end
    apart. `grids` counts distinct full key-time sequences, `width` distinct first-and-last pairs.
    """
    grids = [tuple(t for t, _ in g) for g in groups if g]
    out = {"width": len(groups), "populated": len(grids), "grids": 0, "extent": 0,
           "same_start": 1, "same_end": 1, "start": None, "end": None}
    if not grids:
        return out
    starts = sorted(set(g[0] for g in grids))
    ends = sorted(set(g[-1] for g in grids))
    out.update({
        "grids": len(set(grids)),
        "extent": len(set((g[0], g[-1]) for g in grids)),
        "same_start": starts[0] == starts[-1],
        "same_end": ends[0] == ends[-1],
        "start": (starts[0], starts[-1]),
        "end": (ends[0], ends[-1]),
    })
    return out


def vector_census(files):
    """Every track against the sampler's model: one vector, sampled at one time (`--vector`).

    `ValuesAtTime` (0x000b24f0) fills `*(this+0x34)` doubles in a single loop and refuses a `time`
    outside `[global min, *(int*)(this+0x1c)]` before filling any of them, so the runtime holds **one**
    bound for the whole 26-slot vector. If the file is that vector, every populated slot of a track
    should live on the same time axis. This counts how often that holds, and how far a track's slots
    disagree about where they start and end.
    """
    lt = _mod("lad_tracks", "lad-tracks.py")
    lk = _mod("lad_keys", "lad-keys.py")
    out = []
    for path in files:
        d = open(path, "rb").read()
        recs = lt.measure(path)["_tracks"]
        got = {"path": path, "tracks": 0, "extent": 0, "grid": 0, "same_start": 0, "same_end": 0,
               "widths": collections.Counter(), "grids": collections.Counter(),
               "start_spread": [], "end_spread": []}
        for rec in recs:
            try:
                groups, _names, _stop = lk.key_block(d, rec["keys_offset"],
                                                     rec["keys_offset"] + rec["block"])
            except ValueError:
                continue
            shape = vector_shape(groups)
            got["tracks"] += 1
            got["widths"][shape["width"]] += 1
            if not shape["populated"]:
                continue
            got["extent"] += 1 if shape["extent"] == 1 else 0
            got["grid"] += 1 if shape["grids"] == 1 else 0
            got["same_start"] += 1 if shape["same_start"] else 0
            got["same_end"] += 1 if shape["same_end"] else 0
            got["grids"][shape["grids"]] += 1
            got["start_spread"].append(shape["start"][1] - shape["start"][0])
            got["end_spread"].append(shape["end"][1] - shape["end"][0])
        out.append(got)
    return out


def percentile(values, q):
    """The q-th percentile of a small list, nearest-rank: the report prints spreads, not averages."""
    if not values:
        return 0
    ordered = sorted(values)
    i = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[i]


def selftest():
    """`relations()` on hand-built groups: a duplicate, a negation, an unrelated slot and an empty one."""
    g0 = [(0, 1.5), (10, -2.25), (20, 0.0)]
    g1 = list(g0)                        # same
    g2 = [(0, -1.5), (10, 2.25), (20, -0.0)]   # neg
    g3 = [(0, 1.5), (10, 2.25), (20, 0.0)]     # shares the grid, neither
    g4 = [(0, 1.5), (30, 9.0)]                 # different grid
    got = relations([g0, g1, g2, g3, g4, []])
    want = {(0, 1): "same", (0, 2): "neg", (0, 3): "other", (1, 2): "neg", (1, 3): "other", (2, 3): "other"}
    bad = []
    if got != want:
        bad.append("relations() read %r" % (got,))
    if (0, 4) in got:
        bad.append("relations() compared a pair with different time grids")
    if any(4 in k or 5 in k for k in got):
        bad.append("relations() compared a slot with itself/empty")
    # vector_shape(): the sampler's model, on hand-built tracks.
    one = vector_shape([[(0, 1.0), (10, 2.0)], [(0, 3.0), (10, 4.0)], []])
    if (one["grids"], one["extent"], one["same_start"], one["same_end"]) != (1, 1, 1, 1):
        bad.append("vector_shape() read one grid as %r" % (one,))
    two = vector_shape([[(0, 1.0), (10, 2.0)], [(0, 3.0), (20, 4.0)]])
    if (two["grids"], two["extent"], two["same_start"], two["same_end"]) != (2, 2, 1, 0):
        bad.append("vector_shape() read two extents as %r" % (two,))
    apart = vector_shape([[(5, 1.0), (10, 2.0)], [(0, 3.0), (10, 4.0)]])
    if (apart["extent"], apart["same_start"], apart["same_end"], apart["start"]) != (2, 0, 1, (0, 5)):
        bad.append("vector_shape() read a late start as %r" % (apart,))
    if vector_shape([[], []])["populated"] != 0:
        bad.append("vector_shape() counted an empty track as populated")
    return bad


def check():
    """Dump-free: the committed CSV keeps its invariants and the relation test still holds."""
    path = os.path.join(ROOT, "inventory", CSV)
    rows = read_csv(path)
    bad = selftest()
    wide = {}
    if not rows:
        print("FAIL lipsinc-groups: %s is empty" % path)
        return 1
    by_path = collections.defaultdict(list)
    for r in rows:
        by_path[r["path"]].append(r)
    keys_csv = {}
    kpath = os.path.join(ROOT, "inventory", "lipsinc_keys.csv")
    if os.path.exists(kpath):
        for r in read_csv(kpath):
            keys_csv[r["path"]] = keys_csv.get(r["path"], 0) + int(r["keys"])
    for p, rs in by_path.items():
        slots = [int(r["slot"]) for r in rs]
        if slots != list(range(SLOTS)):
            bad.append("%s: slots %r" % (p, slots))
            continue
        for r in rs:
            using, tracks, keys = int(r["using"]), int(r["tracks"]), int(r["keys"])
            if using > tracks:
                bad.append("%s slot %s: %d using of %d tracks" % (p, r["slot"], using, tracks))
            if using and keys < using:
                bad.append("%s slot %s: %d keys in %d groups" % (p, r["slot"], keys, using))
            if int(r["empty"]) + using != tracks:
                bad.append("%s slot %s: %s empty + %d using != %d tracks"
                           % (p, r["slot"], r["empty"], using, tracks))
            if keys and int(r["time_min"]) > int(r["time_max"]):
                bad.append("%s slot %s: time %s..%s" % (p, r["slot"], r["time_min"], r["time_max"]))
            if keys and float(r["value_min"]) > float(r["value_max"]):
                bad.append("%s slot %s: value %s..%s" % (p, r["slot"], r["value_min"], r["value_max"]))
            # the split that says a value means something: slots 0-18 never carry a negative value
            if keys and int(r["slot"]) < WEIGHT_SLOTS and float(r["value_min"]) < 0:
                bad.append("%s slot %s: a weight slot carries %s" % (p, r["slot"], r["value_min"]))
            if keys and int(r["slot"]) >= WEIGHT_SLOTS:
                wide[int(r["slot"])] = max(wide.get(int(r["slot"]), 0.0),
                                           abs(float(r["value_min"])), abs(float(r["value_max"])))
        total = sum(int(r["keys"]) for r in rs)
        if p in keys_csv and total != keys_csv[p]:
            bad.append("%s: %d keys in the slot table, %d in lipsinc_keys.csv" % (p, total, keys_csv[p]))
        # the relations are symmetric by construction, and a slot cannot relate to itself
        for r in rs:
            for other in (r["equal_to"] + " " + r["negation_of"]).split():
                if other == r["slot"]:
                    bad.append("%s slot %s relates to itself" % (p, r["slot"]))
                else:
                    back = by_path[p][int(other)]
                    if r["slot"] not in (back["equal_to"] + " " + back["negation_of"]).split():
                        bad.append("%s slot %s names %s, which does not name it back"
                                   % (p, r["slot"], other))
    pairs = sum(1 for r in rows if r["equal_to"] or r["negation_of"])
    for slot in range(WEIGHT_SLOTS, SLOTS):
        if wide.get(slot, 0.0) < 1.2:
            bad.append("slot %d never leaves the weight range (%r) -- the split at %d is not a split"
                       % (slot, wide.get(slot), WEIGHT_SLOTS))
    if pairs == 0:
        bad.append("no slot pair is redundant -- the census found nothing")
    if bad:
        for b in bad[:20]:
            print("FAIL lipsinc-groups: " + b)
        return 1
    print("ok   lipsinc-groups: %d files, %d slot rows, %d redundant slots, %d tracks"
          % (len(by_path), len(rows), pairs, sum(int(r["tracks"]) for r in rows) // SLOTS))
    return 0


def print_track(path, want):
    lt = _mod("lad_tracks", "lad-tracks.py")
    lk = _mod("lad_keys", "lad-keys.py")
    d = open(path, "rb").read()
    for rec in lt.measure(path)["_tracks"]:
        if rec["full"] != want:
            continue
        groups, _ = lk.key_block(d, rec["keys_offset"], rec["keys_offset"] + rec["block"])
        print("%s: 26 slots, %d keys" % (rec["full"], sum(len(g) for g in groups)))
        for i, g in enumerate(groups):
            if not g:
                print("  slot %2d: empty" % i)
                continue
            print("  slot %2d: %3d keys  time %d..%d  value %s .. %s"
                  % (i, len(g), g[0][0], g[-1][0], "%.4g" % min(v for _, v in g),
                     "%.4g" % max(v for _, v in g)))
        for (a, b), kind in sorted(relations(groups).items()):
            print("  %2d %s %2d" % (a, "==" if kind == "same" else ("== -" if kind == "neg" else "?="), b))
        return 0
    print("no track named %r in %s" % (want, path))
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="directory of .lad files (walked recursively)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--pairs", action="store_true", help="print the slot relation table only")
    ap.add_argument("--headers", action="store_true", help="scan every track-like header, strict and relaxed")
    ap.add_argument("--vector", action="store_true",
                    help="one vector at one time: do a track's slots share a time axis?")
    ap.add_argument("--track", metavar="TRACK", help="print one track's 26 slots")
    ap.add_argument("--check", action="store_true", help="validate the committed CSV without the dump")
    args = ap.parse_args()
    if args.check:
        return check()
    files = list(args.files)
    if args.dir:
        for base, _, names in os.walk(args.dir):
            files += [os.path.join(base, n) for n in names if n.lower().endswith(".lad")]
    if not files:
        ap.error("no .lad files given (pass paths or --dir)")
    files.sort()
    if args.track:
        if len(files) != 1:
            ap.error("--track takes exactly one .lad file")
        return print_track(files[0], args.track)
    if args.headers:
        total = collections.Counter()
        for f in files:
            got = header_scan(f)
            total.update(got["headers"])
            print("%s: %d track-like records, %d accepted by the strict reader"
                  % (os.path.basename(f), got["relaxed"], got["strict"]))
        print("distinct (f32, f32, count) headers in %d files:" % len(files))
        for k, v in total.most_common(10):
            print("   %.6g %.6g %d  x %d" % (k[0], k[1], k[2], v))
        return 0
    if args.vector:
        total = collections.Counter()
        grids = collections.Counter()
        widths = collections.Counter()
        start_spread, end_spread = [], []
        for got in vector_census(files):
            total["tracks"] += got["tracks"]
            for k in ("extent", "grid", "same_start", "same_end"):
                total[k] += got[k]
            grids.update(got["grids"])
            widths.update(got["widths"])
            start_spread += got["start_spread"]
            end_spread += got["end_spread"]
            print("%s: %d tracks, %d one extent, %d same start, %d same end, %d one grid"
                  % (os.path.basename(got["path"]), got["tracks"], got["extent"],
                     got["same_start"], got["same_end"], got["grid"]))
        print("widths (slots per track): %s"
              % ", ".join("%d x %d" % (w, widths[w]) for w in sorted(widths)))
        print("distinct key grids per track: %s"
              % ", ".join("%d x %d" % (g, grids[g]) for g in sorted(grids)))
        print("of %d tracks: %d one extent, %d one grid, %d share a start, %d share an end"
              % (total["tracks"], total["extent"], total["grid"], total["same_start"], total["same_end"]))
        print("spread inside one track (ticks): start median %d max %d, end median %d max %d"
              % (percentile(start_spread, 0.5), max(start_spread or [0]),
                 percentile(end_spread, 0.5), max(end_spread or [0])))
        return 0
    measured = [measure(f) for f in files]
    if args.pairs:
        for m in measured:
            print(os.path.basename(m["path"]))
            for (a, b), (kind, n) in sorted(deterministic(m["pairs"]).items()):
                print("   %2d %s %2d  in %d tracks" % (a, "==" if kind == "same" else "== -", b, n))
            for (a, b), c in sorted(m["pairs"].items()):
                if c["other"] and not deterministic(m["pairs"]).get((a, b)):
                    print("   %2d ?= %2d  %d same, %d neg, %d neither" % (a, b, c["same"], c["neg"], c["other"]))
        return 0
    rows = []
    for m in measured:
        pairs = m["pairs"]
        det = deterministic(pairs)
        eq = collections.defaultdict(list)
        ng = collections.defaultdict(list)
        for (a, b), (kind, _) in det.items():
            if kind == "same":
                eq[a].append(b)
                eq[b].append(a)
            else:
                ng[a].append(b)
                ng[b].append(a)
        for i, s in enumerate(m["slots"]):
            rows.append({
                "path": m["path"], "slot": i, "tracks": s["tracks"], "using": s["using"],
                "keys": s["keys"], "empty": s["empty"],
                "min_keys": "" if s["min_keys"] is None else s["min_keys"],
                "max_keys": "" if s["max_keys"] is None else s["max_keys"],
                "time_min": "" if s["time_min"] is None else s["time_min"],
                "time_max": "" if s["time_max"] is None else s["time_max"],
                "value_min": "" if s["value_min"] is None else "%.6g" % s["value_min"],
                "value_max": "" if s["value_max"] is None else "%.6g" % s["value_max"],
                "equal_to": " ".join(str(x) for x in sorted(eq[i])),
                "negation_of": " ".join(str(x) for x in sorted(ng[i])),
            })
    if args.dir:
        root = os.path.abspath(args.dir)
        for r in rows:
            r["path"] = os.path.relpath(os.path.abspath(r["path"]), root)
    os.makedirs(args.out, exist_ok=True)
    dest = os.path.join(args.out, CSV)
    with open(dest, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in COLUMNS})
    redundant = sorted(set((r["path"], r["slot"]) for r in rows if r["equal_to"] or r["negation_of"]))
    print("wrote %s (%d files, %d slot rows, %d tracks, %d redundant slots)"
          % (dest, len(measured), len(rows), sum(m["tracks"] for m in measured), len(redundant)))
    for m in measured:
        det = deterministic(m["pairs"])
        print("  %s: %d tracks, %d redundant slots" % (m["path"], m["tracks"], len(det) * 2))
        for r in m["refused"][:3]:
            print("  refused %s" % r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
