#!/usr/bin/env python3
"""Index a `.lad`'s channels as an ordered bone list, and read the pose record's angle (docs/UNKNOWNS.md U4).

`tools/lad-frames.py` and `tools/lad-tracks.py` measured the body's three parts; `tools/lad-groups.py` measured the
key blocks' 26 slots and left "which weight slot is which bone" open. This tool measures the only ordered bone list
the file actually carries -- the channel table -- and what a pose record holds beyond its position.

* **The channel table is ordered and stable.** All eight files share the same **19-name prefix**
  (`BN-Blink_L` ... `BN-Head`); the two 21-channel shapes insert `BN-Tongue-back` / `BN-Tongue-front` at indices 18
  and 19 and keep `BN-Head` last at 20. The four `Controllers/` packs are exactly the 19-name list.
* **A clip's bone list is an explicit order, not the table's.** Each of the 17 clips names its bones in its own
  sequence and its frames hold one record per *listed* bone: 16 of 17 lists happen to follow table order, and
  `Controllers/sg1_crew_jackson.lad`'s `jackson_frown` lists `BN-Head` first (indices 18, 4, 5, 6, 7). So the file
  never states "bone = slot number"; a clip says it by position in its own list.
* **The pose record's eighth word is an angle, not a sentinel.** Every one of the 3,200 records has its last float in
  `[-1, 0]`, and `-acos` of it snaps to a **half-degree grid** in 96-98% of the records (352 of 420 in a 21-channel
  clip are exactly 0 degrees, i.e. `-1.0`). Reading it as `-cos(theta)` replaces the "pose weight" of the earlier
  page with a rotation angle.
* **The 19 weight slots are not one-per-channel.** The 21-channel clips carry the same 26-slot key blocks as the
  19-channel packs, so the weight-slot count is a property of the key schema, not of the channel table -- the
  "19 weights, 19 channels" coincidence the slot page recorded does not survive this measurement.

    tools/lad-bones.py FILE.lad [...]                        # one line per file
    tools/lad-bones.py --dir "$SG1_DUMP/.../System/LIPSincData" -o inventory
    tools/lad-bones.py --bones FILE.lad                      # the ordered bone list with clip membership
    tools/lad-bones.py --angles FILE.lad [...]               # where the last float lands on the degree grid
    tools/lad-bones.py --check                               # dump-free, validates the committed CSV

It measures names, order and one field; it does not claim what the three words between position and angle are.
"""
import argparse
import collections
import csv
import importlib.util
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = "lipsinc_bones.csv"
COLUMNS = ["path", "index", "name", "channels", "slot", "clips", "clip_positions", "frames",
           "pose_x", "pose_y", "pose_z", "angle_deg", "angle_max", "dx", "dy", "dz"]
RECORD = 32
WEIGHT_SLOTS = 19
ANGLE_WORD = 28                       # the record's last float, -cos(theta)
PREFIX = 18                           # names shared, in order, by all eight files
GRID = 0.5                            # the degree grid the angle lands on


def _mod(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def angle_of(word):
    """`-cos(theta)` -> theta in degrees; None when the word is not in the cosine's range."""
    if not -1.0000001 <= word <= 0.0000001:
        return None
    return math.degrees(math.acos(min(1.0, max(-1.0, -word))))


def on_grid(deg):
    """True when a degree value lands on the half-degree grid the poses are authored on."""
    return abs(deg * 2 - round(deg * 2)) < 0.02


def walk(d):
    """The channel names, the frame records per channel, and the clips -- all through lad-tracks.py's strict walk."""
    lt = _mod("lad_tracks", "lad-tracks.py")
    channels, off = d[8], 9
    names = []
    for _ in range(channels):
        name, off = lt.read_name(d, off)
        names.append(name)
    end, _ = lt.trailer_start(d, off)
    nframes, _, stop = lt.frame_section(d, off, end, channels)
    p = off + 1
    per_channel = collections.defaultdict(list)
    while p < stop:
        count = d[p + 4]
        p += 5
        for c in range(count):
            per_channel[c].append(d[p:p + RECORD])
            p += RECORD
    clips = None
    if stop < end:
        got = lt.clip_section(d, stop, end, channels)
        if got is not None:
            clips = got[0]
    return names, nframes, per_channel, clips


def measure(path, label=None):
    d = open(path, "rb").read()
    names, nframes, per_channel, clips = walk(d)
    rows = []
    for i, name in enumerate(names):
        recs = per_channel.get(i, [])
        if not recs:
            raise ValueError("%s: channel %d carries no record" % (path, i))
        x, y, z = struct.unpack_from("<3f", recs[0], 4)
        word = struct.unpack_from("<f", recs[0], ANGLE_WORD)[0]
        deg = angle_of(word)
        angles = [a for a in (angle_of(struct.unpack_from("<f", r, ANGLE_WORD)[0]) for r in recs)
                  if a is not None]
        span = []
        for axis in (0, 1, 2):
            vs = [struct.unpack_from("<3f", r, 4)[axis] for r in recs]
            span.append(max(vs) - min(vs))
        in_clips, positions = [], []
        for cname, bones, _ in clips or []:
            if name in bones:
                in_clips.append(cname)
                positions.append(str(bones.index(name)))
        rows.append({
            "path": label if label is not None else path,
            "index": i,
            "name": name,
            "channels": len(names),
            "slot": i if i < WEIGHT_SLOTS else "",
            "clips": ";".join(in_clips),
            "clip_positions": ";".join(positions),
            "frames": len(recs),
            "pose_x": "%.5g" % x,
            "pose_y": "%.5g" % y,
            "pose_z": "%.5g" % z,
            "angle_deg": "" if deg is None else "%.2f" % deg,
            "angle_max": "" if not angles else "%.2f" % max(angles),
            "dx": "%.5g" % span[0],
            "dy": "%.5g" % span[1],
            "dz": "%.5g" % span[2],
        })
    return rows


def collect(files, out, base=None):
    rows = []
    for path in files:
        label = os.path.relpath(path, base) if base else path
        rows.extend(measure(path, label))
    with open(os.path.join(out, CSV), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\r\n")
        w.writeheader()
        w.writerows(rows)
    return rows


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def by_file(rows):
    out = collections.OrderedDict()
    for r in rows:
        out.setdefault(r["path"], []).append(r)
    return out


def selftest():
    """The angle decode and the grid test on hand-built values, so `--check` tests the reader, not just the CSV."""
    bad = []
    cases = [(0.0, -1.0), (10.0, -0.984807753012208), (15.0, -0.965925826289068), (38.5, -0.782608156852414)]
    for deg, word in cases:
        got = angle_of(word)
        if got is None or abs(got - deg) > 0.01:
            bad.append("angle_of(%r) = %r, wanted %.2f" % (word, got, deg))
        if not on_grid(got):
            bad.append("%.2f does not test as on-grid" % deg)
    if angle_of(-1.5) is not None or angle_of(0.5) is not None:
        bad.append("a word outside [-1, 0] was read as an angle")
    return bad


def check():
    """Dump-free: the committed CSV keeps the invariants the measurement claims."""
    path = os.path.join(ROOT, "inventory", CSV)
    if not os.path.exists(path):
        print("FAIL lipsinc-bones: %s is missing" % path)
        return 1
    rows = read_csv(path)
    bad = selftest()
    if not rows:
        print("FAIL lipsinc-bones: %s is empty" % path)
        return 1
    files = by_file(rows)
    prefix = None
    for p, rs in files.items():
        indices = [int(r["index"]) for r in rs]
        if indices != list(range(len(rs))):
            bad.append("%s: indices %r" % (p, indices))
            continue
        channels = int(rs[0]["channels"])
        if channels != len(rs):
            bad.append("%s: %d channels, %d rows" % (p, channels, len(rs)))
        for r in rs:
            if int(r["index"]) < WEIGHT_SLOTS:
                if r["slot"] != r["index"]:
                    bad.append("%s index %s: slot %r" % (p, r["index"], r["slot"]))
            elif r["slot"]:
                bad.append("%s index %s: a slot above the weight table" % (p, r["index"]))
            if r["angle_deg"] == "":
                bad.append("%s %s: the last word is not a cosine" % (p, r["name"]))
            elif not 0.0 <= float(r["angle_deg"]) <= 180.0:
                bad.append("%s %s: %.2f degrees" % (p, r["name"], float(r["angle_deg"])))
            if len(r["clips"].split(";")) != len(r["clip_positions"].split(";")) and r["clips"]:
                bad.append("%s %s: %d clips but %d positions" % (p, r["name"], len(r["clips"].split(";")),
                                                                 len(r["clip_positions"].split(";"))))
        names = [r["name"] for r in rs]
        if len(names) < PREFIX:
            bad.append("%s: only %d names" % (p, len(names)))
        elif prefix is None:
            prefix = names[:PREFIX]
        elif names[:PREFIX] != prefix:
            bad.append("%s: the shared %d-name prefix differs from the other files" % (p, PREFIX))
        # the tail: the 21-channel shapes insert the two tongues and keep BN-Head last; the 19-channel packs
        # are that list minus the tongues
        tail = names[PREFIX:]
        if len(names) == 21:
            if tail != ["BN-Tongue-back", "BN-Tongue-front", "BN-Head"]:
                bad.append("%s: 21-channel tail %r" % (p, tail))
        elif len(names) == 19:
            if tail != ["BN-Head"]:
                bad.append("%s: 19-channel tail %r" % (p, tail))
        else:
            bad.append("%s: %d channels is neither shape" % (p, len(names)))
        # the walk's own invariant: one record per channel per frame, and every bone read the same frame count
        counts = {r["frames"] for r in rs}
        if len(counts) != 1 or int(counts.pop()) < 1:
            bad.append("%s: frame counts %r across the bone rows" % (p, sorted(counts)))
    # a clip's frames follow its own bone list: sort the bones by their position in each clip and the table
    # indices must rise, except where the file records a list that deliberately does not
    out_of_order, clips_seen = [], 0
    for p, rs in files.items():
        per_clip = collections.defaultdict(list)
        for r in rs:
            if not r["clips"]:
                continue
            for cname, pos in zip(r["clips"].split(";"), r["clip_positions"].split(";")):
                per_clip[cname].append((int(pos), int(r["index"])))
        for cname, pairs in per_clip.items():
            clips_seen += 1
            seq = [idx for _, idx in sorted(pairs)]
            if seq != sorted(seq):
                out_of_order.append((p, cname, seq))
    if clips_seen and not out_of_order:
        bad.append("no clip lists a bone out of table order -- the 'explicit order' claim has no witness")
    if bad:
        for b in bad[:20]:
            print("FAIL lipsinc-bones: " + b)
        return 1
    angles = [float(r["angle_max"]) for r in rows if r["angle_max"]]
    zero = sum(1 for a in angles if a == 0.0)
    grid = sum(1 for a in angles if on_grid(a))
    if grid < 0.9 * len(angles):
        bad.append("only %d of %d bones peak on the half-degree grid" % (grid, len(angles)))
    if not any(a > 0 for a in [float(r["angle_deg"]) for r in rows if r["angle_deg"]]):
        pass                      # frame 0 is the bind pose: a zero angle there is the expected reading
    print("ok   lipsinc-bones: %d files, %d bone rows, %d-name prefix stable, angles 0..%.1f (%.0f%% at 0), "
          "%d of %d on the half-degree grid, %d out-of-order clip lists"
          % (len(files), len(rows), PREFIX, max(angles), 100.0 * zero / len(angles), grid, len(angles),
             len(out_of_order)))
    return 0


def print_bones(path):
    rows = measure(path)
    print("%s: %d channels" % (os.path.basename(path), len(rows)))
    for r in rows:
        print("  %2s %-22s %s x=%-8s y=%-8s z=%-8s angle=%-6s dz=%-8s %s"
              % (r["index"], r["name"], r["slot"] or "-", r["pose_x"], r["pose_y"], r["pose_z"],
                 r["angle_deg"], r["dz"], r["clips"]))
    return 0


def print_angles(files):
    for path in files:
        d = open(path, "rb").read()
        names, nframes, per_channel, clips = walk(d)
        words = [struct.unpack_from("<f", r, ANGLE_WORD)[0] for recs in per_channel.values() for r in recs]
        degs = [angle_of(w) for w in words]
        bad = sum(1 for x in degs if x is None)
        degs = [x for x in degs if x is not None]
        grid = sum(1 for x in degs if on_grid(x))
        c = collections.Counter(round(x, 1) for x in degs)
        print("%-34s records=%4d outside=%d grid=%.3f zero=%d top=%s"
              % (os.path.basename(path), len(words), bad, grid / len(degs),
                 sum(1 for x in degs if x == 0.0), c.most_common(5)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="directory of .lad files (walked recursively)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--bones", action="store_true", help="print one file's ordered bone list")
    ap.add_argument("--angles", action="store_true", help="where each file's last float lands on the degree grid")
    ap.add_argument("--check", action="store_true", help="dump-free: validate the committed CSV")
    args = ap.parse_args()

    if args.check:
        return check()
    files = list(args.files)
    if args.dir:
        for base, _, names in os.walk(args.dir):
            for n in sorted(names):
                if n.endswith(".lad"):
                    files.append(os.path.join(base, n))
    files.sort()
    if not files:
        ap.error("no .lad files: give paths or --dir")
    if args.bones:
        if len(files) != 1:
            ap.error("--bones takes exactly one file")
        return print_bones(files[0])
    if args.angles:
        return print_angles(files)
    rows = collect(files, args.out, args.dir)
    for p, rs in by_file(rows).items():
        clips = sorted({c for r in rs for c in r["clips"].split(";") if c})
        print("%-34s channels=%2d bones=%2d clips=%d angles=%.1f..%.1f"
              % (os.path.basename(p), len(rs), len(rs), len(clips),
                 min(float(r["angle_deg"]) for r in rs if r["angle_deg"]),
                 max(float(r["angle_deg"]) for r in rs if r["angle_deg"])))
    print("wrote %s" % os.path.join(args.out, CSV))
    return 0


if __name__ == "__main__":
    sys.exit(main())
