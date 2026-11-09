#!/usr/bin/env python3
"""What the runtime answers at a time one of a track's curves does not cover (docs/UNKNOWNS.md U4).

`ValuesAtTime` (`0x000b24f0`) samples one track's 26-slot vector at one time and calls a per-slot
evaluator for each populated slot. That evaluator is `Engine_LIPSincSupport` (`0x000b2230`, 435 bytes,
named from its own assert path `Game\\GameXBOX\\Engine\\Src\\LIPSincSupport.cpp`) and its body settles
the question this repository kept open -- "hold, clamp or extrapolate" -- in one read:

  * before a slot's **first** key it returns that key's value (`fldl [keys+8]`, one load, no arithmetic):
    a hold, not an extrapolation;
  * after its **last** key it returns the last key's value (`fldl [keys+count*16-8]`): the same hold;
  * with **no** keys it returns 0.0, and with **one** key that value everywhere;
  * between two keys it is a cubic *smoothstep*, `v0 + (v1 - v0) * (3a^2 - 2a^3)` with
    `a = (t - t0) / (t1 - t0)`. The constants are in the body (`3.0` at `0x4ea138`, `6.0` at
    `0x4ea130`) and the polynomial reduces to `v0 + 3(v1-v0)a^2 - 2(v1-v0)a^3`. So a segment is **not**
    a linear lerp: at `a = 1/4` the sample sits 9.375% of the segment's value span *below* the lerp and
    at `a = 3/4` 9.375% above it -- fixed numbers, independent of the keys;
  * `|v| < 2^-51` (`0x4ea0c8 = 4.44e-16`) is snapped to exactly `0.0` **on every exit path**, holds and
    exact key hits included;
  * an unordered `time` asserts `time == (DOUBLE)(m_akKeys((m_akKeys.Num()-1)).Time())` (line 0x161) --
    the only legal "no segment" is the last key's own time -- and a non-increasing key pair asserts
    `time2 > time1` (line 0x16c), so a segment's divisor is never zero.

The track-level gate above it (`ValuesAtTime`) accepts only `[0.0, *(this + 0x1c)]`: a time below zero
or past the track's end logs `[LIPSinc]: ValuesAtTime, m_nEndTime = %i, time = %f` and returns 1
without writing any of the 26 doubles.

Since the corpus has no shared time axis (0 of 2,231 tracks, `lad-groups.py --vector`), the hold path
is the rule rather than an edge case: a slot that stops early keeps its last value for the rest of the
track. This tool implements the sampler exactly as measured and counts that:

    tools/lad-sampler.py FILE.lad [...]                  # one line per file, then the totals
    tools/lad-sampler.py --dir "$SG1_DUMP/.../LIPSincData" -o inventory
    tools/lad-sampler.py --track TRACK FILE.lad          # one track, sampled at its key times
    tools/lad-sampler.py --check                         # dump-free: the sampler on synthetic keys

`held_frac` is the share of the 26 x domain rectangle the hold path answers, `grid_holds` the same thing
measured by actually calling the sampler on a 33-point grid, `covered` the slots whose extent is the
whole domain, `decreasing`/`equal_time` the adjacent key pairs that would trip the two asserts, and
`snap_zero` the key values the 2^-51 snap would turn into zero. It measures what the shipped data does;
it does not claim what a slot means.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = "lipsinc_sampler.csv"
SLOTS = 26
GRID = 32                                  # sample times per track: 0, E/32, ... E
EPS51 = 2.0 ** -51                         # 0x4ea0c8, the snap threshold in the evaluator
COLUMNS = ["path", "track", "slots", "keys", "domain", "covered", "held_frac", "grid_times",
           "grid_holds", "decreasing", "equal_time", "snap_zero"]


def _mod(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sample(keys, t):
    """The measured evaluator: hold outside, smoothstep inside, the 2^-51 snap on every exit path.

    `keys` is one slot's `(time, value)` list as `lad-keys.py` decodes it -- ascending whole ticks and
    float64 values. Returns `(value, path)`; `path` is `empty`, `single`, `hold-before`, `hold-after`,
    `key` or `segment`, so a caller can count which branch answered. The asserts (line 0x161/0x16c) are
    not re-raised here: `decreasing`/`equal_time` in `measure_track` count their inputs instead.
    """
    if not keys:
        return 0.0, "empty"
    t0, v0 = keys[0]
    if t < t0:
        return snap(v0), "hold-before"
    if len(keys) == 1:
        return snap(v0), "single"
    tl, vl = keys[-1]
    if t > tl:
        return snap(vl), "hold-after"
    if t == tl:
        return snap(vl), "key"
    v, path = None, "hold-after"
    for i in range(len(keys) - 1):
        (ta, va), (tb, vb) = keys[i], keys[i + 1]
        if ta <= t < tb:
            if t == ta:
                return snap(va), "key"
            a = (t - ta) / float(tb - ta)
            v = va + (vb - va) * (3.0 * a * a - 2.0 * a * a * a)
            path = "segment"
            break
    if v is None:                          # only reachable through a non-ascending pair (the assert)
        return snap(vl), "hold-after"
    return snap(v), path


def snap(v):
    """The evaluator's last three instructions: `|v| < 2^-51` becomes exactly zero."""
    return 0.0 if v != 0.0 and abs(v) < EPS51 else v


def measure_track(groups):
    """One track against the sampler: domain, hold coverage, the two asserts, and the snap."""
    keys = sum(len(g) for g in groups)
    ends = [g[-1][0] for g in groups if g]
    domain = max(ends) if ends else 0
    covered = sum(1 for g in groups if g and domain > 0 and g[0][0] == 0 and g[-1][0] == domain)
    held = 0.0
    if domain > 0:
        for g in groups:
            if not g:
                held += domain                     # an empty slot answers 0.0 at every time
            else:
                held += max(0, g[0][0]) + max(0, domain - g[-1][0])
    held_frac = held / (SLOTS * domain) if domain else 0.0
    decreasing = equal_time = 0
    for g in groups:
        for (ta, _), (tb, _) in zip(g, g[1:]):
            if tb < ta:
                decreasing += 1
            elif tb == ta:
                equal_time += 1
    snap_zero = sum(1 for g in groups for _, v in g if v != 0.0 and abs(v) < EPS51)
    grid_times = grid_holds = 0
    if domain > 0:
        step = max(1, domain // GRID)
        times = list(range(0, domain + 1, step))
        if times[-1] != domain:
            times.append(domain)
        for t in times:
            for g in groups:
                if g:
                    grid_times += 1
                    if t < g[0][0] or t > g[-1][0]:
                        grid_holds += 1
    return {"slots": sum(1 for g in groups if g), "keys": keys, "domain": domain,
            "covered": covered, "held_frac": held_frac, "grid_times": grid_times,
            "grid_holds": grid_holds, "decreasing": decreasing, "equal_time": equal_time,
            "snap_zero": snap_zero}


def _rel(path):
    """The dump-relative name the rest of the inventory uses (`Controllers/grey.lad`)."""
    parts = os.path.abspath(path).split(os.sep)
    if "LIPSincData" in parts:
        return "/".join(parts[parts.index("LIPSincData") + 1:])
    return os.path.basename(path)


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


def percentile(values, q):
    if not values:
        return 0
    ordered = sorted(values)
    i = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[i]


def selftest():
    """`sample()` on hand-built keys: holds, both endpoints, the quarter points, the snap and the order."""
    k = [(0, 2.0), (10, 6.0)]
    assert sample([], 5) == (0.0, "empty")
    assert sample(k, -1) == (2.0, "hold-before"), sample(k, -1)
    assert sample(k, 20) == (6.0, "hold-after")
    assert sample([(3, 1.5)], 0) == (1.5, "hold-before")        # one key: the value everywhere
    assert sample([(3, 1.5)], 3) == (1.5, "single") and sample([(3, 1.5)], 99) == (1.5, "single")
    assert sample(k, 0) == (2.0, "key") and sample(k, 10) == (6.0, "key")
    assert sample(k, 5) == (4.0, "segment")                # a = 1/2, the lerp's own midpoint
    assert abs(sample(k, 2.5)[0] - 2.625) < 1e-15          # a = 1/4: 3a^2-2a^3 = 0.15625
    assert abs(sample(k, 7.5)[0] - 5.375) < 1e-15          # a = 3/4: 0.84375
    assert abs((sample(k, 2.5)[0] - 2.0) / 4.0 - 0.15625) < 1e-15
    # The snap is on every path, exact key hits included -- this is the part a naive sampler gets wrong.
    assert sample([(0, 3e-16)], 0) == (0.0, "single")
    assert sample([(0, 0.0), (10, 3e-16)], 10) == (0.0, "key")
    assert sample([(0, 0.0), (10, 3e-16)], 99) == (0.0, "hold-after")
    assert sample([(0, 0.0), (10, 3e-12)], 10) == (3e-12, "key")   # above 2^-51, kept
    # A non-ascending pair is the assert at 0x16c, not a segment.
    assert sample([(0, 1.0), (10, 2.0), (5, 3.0)], 7)[1] == "hold-after"
    m = measure_track([[(0, 1.0), (5, 2.0)], [(2, 1.0), (10, 3.0)], [], []])
    assert m["domain"] == 10 and m["covered"] == 0 and m["slots"] == 2, m
    # slot0 holds 5 ticks after its last key, slot1 holds 2 before its first, two slots hold
    # the whole 10 ticks each: (5 + 2 + 10 + 10) / (26 * 10)
    assert abs(m["held_frac"] - 27 / (26 * 10.0)) < 1e-12, m
    assert m["decreasing"] == 0 and m["snap_zero"] == 0
    assert measure_track([[(0, 1.0), (9, 1.0)]] * SLOTS)["covered"] == SLOTS
    return True


def check(path=None):
    """Dump-free contract: the sampler's arithmetic, plus the committed CSV when there is one."""
    selftest()
    path = path or os.path.join(ROOT, "inventory", CSV)
    if not os.path.exists(path):
        print("lad-sampler: sampler self-test ok (no %s to cross-check)" % CSV)
        return 0
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    assert rows, "empty %s" % CSV
    bad = [r for r in rows if not (0.0 <= float(r["held_frac"]) <= 1.0)]
    assert not bad, "held_frac outside [0,1]: %d rows" % len(bad)
    # The two asserts must be untripped by the shipped keys, or the claim in the doc is wrong.
    assert not [r for r in rows if int(r["decreasing"])], "decreasing key pairs in the corpus"
    assert not [r for r in rows if int(r["snap_zero"])], "a key value below 2^-51 in the corpus"
    assert all(int(r["covered"]) <= int(r["slots"]) for r in rows), "covered > slots"
    assert all(int(r["grid_holds"]) <= int(r["grid_times"]) for r in rows), "grid_holds > grid_times"
    print("lad-sampler: %d rows, sampler self-test ok" % len(rows))
    return 0


def write_csv(rows, outdir):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, CSV)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["held_frac"] = "%.4f" % r["held_frac"]
            w.writerow({k: r[k] for k in COLUMNS})
    return path


def print_track(path, want):
    lt = _mod("lad_tracks", "lad-tracks.py")
    lk = _mod("lad_keys", "lad-keys.py")
    d = open(path, "rb").read()
    for rec in lt.measure(path)["_tracks"]:
        if rec["name"] != want:
            continue
        groups, _n, _s = lk.key_block(d, rec["keys_offset"], rec["keys_offset"] + rec["block"])
        m = measure_track(groups)
        print("%s  %s" % (path, want))
        print("  slots %d  keys %d  domain %d  covered %d  held %.1f%%  grid %d/%d"
              % (m["slots"], m["keys"], m["domain"], m["covered"], 100 * m["held_frac"],
                 m["grid_holds"], m["grid_times"]))
        print("  %-4s %-6s %-6s %-6s %-6s %s" % ("slot", "keys", "first", "last", "hold", "at domain/2"))
        for i, g in enumerate(groups):
            if not g:
                print("  %-4d %-6d %-6s %-6s %-6s %s" % (i, 0, "-", "-", "all", "0.0 (empty)"))
                continue
            v, which = sample(g, m["domain"] // 2)
            print("  %-4d %-6d %-6d %-6d %-6d %.6f (%s)"
                  % (i, len(g), g[0][0], g[-1][0], m["domain"] - g[-1][0], v, which))
        return 0
    print("%s: no track named %s" % (path, want), file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="a LIPSincData directory: every .lad below it")
    ap.add_argument("-o", "--out", help="artifact directory (writes %s)" % CSV)
    ap.add_argument("--track", help="print one track's slots instead of the census")
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
    if args.track:
        return print_track(files[0], args.track)
    rows = measure(files)
    if args.out:
        print("wrote", write_csv(rows, args.out))
    by_file = collections.OrderedDict()
    for r in rows:
        by_file.setdefault(os.path.basename(r["path"]), []).append(r)
    print("%-30s %6s %6s %8s %7s %7s %5s %5s %5s" %
          ("file", "tracks", "slots", "domain", "held%", "grid%", "cov", "dec", "snap"))
    for name, rs in by_file.items():
        dom = sum(r["domain"] for r in rs)
        print("%-30s %6d %6d %8d %7.1f %7.1f %5d %5d %5d" %
              (name, len(rs), sum(r["slots"] for r in rs), dom,
               100 * sum(r["held_frac"] * r["domain"] for r in rs) / dom if dom else 0.0,
               100.0 * sum(r["grid_holds"] for r in rs) / max(1, sum(r["grid_times"] for r in rs)),
               sum(r["covered"] for r in rs), sum(r["decreasing"] for r in rs),
               sum(r["snap_zero"] for r in rs)))
    dom = sum(r["domain"] for r in rows)
    print("%-30s %6d %6d %8d %7.1f %7.1f %5d %5d %5d" %
          ("TOTAL", len(rows), sum(r["slots"] for r in rows), dom,
           100 * sum(r["held_frac"] * r["domain"] for r in rows) / dom if dom else 0.0,
           100.0 * sum(r["grid_holds"] for r in rows) / max(1, sum(r["grid_times"] for r in rows)),
           sum(r["covered"] for r in rows), sum(r["decreasing"] for r in rows),
           sum(r["snap_zero"] for r in rows)))
    fr = [r["held_frac"] for r in rows]
    print("held%% median %.1f  p10 %.1f  p90 %.1f  max %.1f" %
          (100 * percentile(fr, 0.5), 100 * percentile(fr, 0.1), 100 * percentile(fr, 0.9),
           100 * max(fr) if fr else 0))
    print("tracks whose every populated slot covers the whole domain: %d of %d" %
          (sum(1 for r in rows if r["slots"] and r["covered"] == r["slots"]), len(rows)))
    print("tracks with no slot covering the domain's first tick: %d" %
          sum(1 for r in rows if r["covered"] == 0))
    print("equal-time adjacent key pairs: %d" % sum(r["equal_time"] for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
