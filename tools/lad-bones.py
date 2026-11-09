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
* **The pose record's last four floats are a unit quaternion, and the three "opaque" words are its vector part.**
  `FUN_000b1bf0` reads a record as eight consecutive 4-byte scalars -- a tag, then three, then four -- through a
  leaf that is byte-identical for a `u32` and a `float` (`FUN_00037110` and `FUN_00037170` are the same 48 bytes),
  so the 3 + 4 split comes from the data, not from the code. The data is decisive: `|q| = 1` to within `5.5e-7` in
  all 3,200 records, so bytes 16-31 are a rotation. The `-1.0f` that earlier pages read as a terminator, then as a
  pose weight, then as `-cos(theta)` is its `w`; the denormal words at rest (`6.3e-30, -0, 3.6e-30`) are `x, y, z`.
  The stored quaternion always has `w <= 0` -- the canonical form negated, the same rotation -- so the angle is
  `2*atan2(|v|, -w)`, **twice** the `-acos` reading: a whole-degree grid, 77.0 degrees at most, and 522 of the
  3,200 records turning at all.
* **The 19 weight slots are not one-per-channel.** The 21-channel clips carry the same 26-slot key blocks as the
  19-channel packs, so the weight-slot count is a property of the key schema, not of the channel table -- the
  "19 weights, 19 channels" coincidence the slot page recorded does not survive this measurement.

    tools/lad-bones.py FILE.lad [...]                        # one line per file
    tools/lad-bones.py --dir "$SG1_DUMP/.../System/LIPSincData" -o inventory
    tools/lad-bones.py --bones FILE.lad                      # the ordered bone list with clip membership
    tools/lad-bones.py --angles FILE.lad [...]               # each file's rotation alphabet, grid and axes
    tools/lad-bones.py --records FILE.lad                    # every frame's position and quaternion, one file
    tools/lad-bones.py --check                               # dump-free, validates the committed CSV

It measures names, order and the record's seven floats; the clip record's `u32 2` and the track header's
`160.0`/`220.0` are a separate question (U4).
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
           "pose_x", "pose_y", "pose_z", "qx", "qy", "qz", "qw", "axis", "angle_deg", "angle_max",
           "norm_err", "dx", "dy", "dz"]
RECORD = 32
WEIGHT_SLOTS = 19
POINTER = 4                           # three floats: the position
QUATERNION = 16                       # four floats: a unit quaternion, always negated (w <= 0)
NORM_TOL = 1e-5                       # the unit-length error a float32 quaternion can carry
PREFIX = 18                           # names shared, in order, by all eight files
GRID = 1.0                            # the degree grid the rotations are authored on


def _mod(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def angle_of(quat):
    """`2*atan2(|v|, -w)` in degrees: the rotation a stored quaternion carries.

    `atan2` keeps the small angles that `acos` loses -- near `w = -1.0` a float's resolution is coarser than the
    angle's, and a zero rotation is stored with denormal words, not with exact zeros.
    """
    v = math.sqrt(quat[0] * quat[0] + quat[1] * quat[1] + quat[2] * quat[2])
    return math.degrees(2.0 * math.atan2(v, -quat[3]))


def norm_err(quat):
    """How far a stored quaternion is from unit length."""
    return abs(math.sqrt(sum(c * c for c in quat)) - 1.0)


def axis_of(quat):
    """The rotation axis as a rounded unit vector, or '' for a zero rotation."""
    v = math.sqrt(quat[0] * quat[0] + quat[1] * quat[1] + quat[2] * quat[2])
    if v < 1e-12:
        return ""
    return ",".join("%.3f" % (c / v) for c in quat[:3])


def quat_for(deg, axis=(1.0, 0.0, 0.0)):
    """A rotation of `deg` about a unit `axis`, in the file's negated convention (`w <= 0`)."""
    half = math.radians(deg) / 2.0
    s = math.sin(half)
    return (axis[0] * s, axis[1] * s, axis[2] * s, -math.cos(half))


def on_grid(deg):
    """True when a rotation angle lands on the whole-degree grid the poses are authored on."""
    return abs(deg - round(deg)) < 0.02


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
        x, y, z = struct.unpack_from("<3f", recs[0], POINTER)
        quat = struct.unpack_from("<4f", recs[0], QUATERNION)
        deg = angle_of(quat)
        angles = [angle_of(struct.unpack_from("<4f", r, QUATERNION)) for r in recs]
        err = max(norm_err(struct.unpack_from("<4f", r, QUATERNION)) for r in recs)
        peak = max(recs, key=lambda r: angle_of(struct.unpack_from("<4f", r, QUATERNION)))
        axis = axis_of(struct.unpack_from("<4f", peak, QUATERNION))
        span = []
        for axis_i in (0, 1, 2):
            vs = [struct.unpack_from("<3f", r, POINTER)[axis_i] for r in recs]
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
            "qx": "%.3g" % quat[0],
            "qy": "%.3g" % quat[1],
            "qz": "%.3g" % quat[2],
            "qw": "%.3g" % quat[3],
            "axis": axis,
            "angle_deg": "%.4f" % deg,
            "angle_max": "%.4f" % max(angles),
            "norm_err": "%.3g" % err,
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
    """The quaternion decode and the grid test on hand-built values, so `--check` tests the reader."""
    bad = []
    for deg in (0.0, 1.0, 10.0, 38.0, 77.0):
        q = quat_for(deg)
        got = angle_of(q)
        if abs(got - deg) > 1e-4:
            bad.append("angle_of(%r) = %r, wanted %.2f" % (q, got, deg))
        if not on_grid(got):
            bad.append("%.2f does not test as on-grid" % deg)
        if norm_err(q) > 1e-6 or q[3] > 0:
            bad.append("%r is not a stored-convention unit quaternion" % (q,))
    if on_grid(0.5):
        bad.append("a half-degree rotation tests as on-grid")
    if abs(angle_of(quat_for(30.0, (0.0, 0.0, 1.0))) - 30.0) > 1e-6:
        bad.append("an axis other than x does not decode")
    if axis_of((0.0, 0.0, 0.0, -1.0)) != "":
        bad.append("a zero rotation reports an axis")
    if on_grid(20.4):
        bad.append("the grid test accepts 20.4 degrees")
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
            if not 0.0 <= float(r["angle_deg"]) <= 180.0:
                bad.append("%s %s: %.2f degrees" % (p, r["name"], float(r["angle_deg"])))
            if float(r["norm_err"]) > NORM_TOL:
                bad.append("%s %s: |q| - 1 = %s" % (p, r["name"], r["norm_err"]))
            if float(r["qw"]) > 0:
                bad.append("%s %s: the stored quaternion has w > 0" % (p, r["name"]))
            if r["axis"] and abs(math.sqrt(sum(float(c) ** 2 for c in r["axis"].split(","))) - 1.0) > 2e-3:
                bad.append("%s %s: axis %r is not a unit vector" % (p, r["name"], r["axis"]))
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
    angles = [float(r["angle_max"]) for r in rows]
    zero = sum(1 for a in angles if a == 0.0)
    grid = sum(1 for a in angles if on_grid(a))
    if grid < 0.9 * len(angles):
        bad.append("only %d of %d bones peak on the whole-degree grid" % (grid, len(angles)))
    errs = [float(r["norm_err"]) for r in rows]
    print("ok   lipsinc-bones: %d files, %d bone rows, %d-name prefix stable, rotations 0..%.1f "
          "(%d still), |q|-1 <= %.1e, %d of %d peaks on the whole-degree grid, %d out-of-order clip lists"
          % (len(files), len(rows), PREFIX, max(angles), zero, max(errs), grid, len(angles),
             len(out_of_order)))
    return 0


def print_bones(path):
    rows = measure(path)
    print("%s: %d channels" % (os.path.basename(path), len(rows)))
    for r in rows:
        print("  %2s %-22s %s pos=(%-8s %-8s %-8s) q=(%-9s %-9s %-9s %-7s) angle=%-7s axis=%-16s %s"
              % (r["index"], r["name"], r["slot"] or "-", r["pose_x"], r["pose_y"], r["pose_z"],
                 r["qx"], r["qy"], r["qz"], r["qw"], r["angle_max"], r["axis"] or "-", r["clips"]))
    return 0


def print_records(path):
    """Every record of one file: the frame's position and quaternion, the angle it carries and its axis."""
    d = open(path, "rb").read()
    names, nframes, per_channel, _ = walk(d)
    print("%s: %d channels, %d frames" % (os.path.basename(path), len(names), nframes))
    for c, name in enumerate(names):
        print("  %2d %-22s" % (c, name))
        for f, rec in enumerate(per_channel[c]):
            pos = struct.unpack_from("<3f", rec, POINTER)
            quat = struct.unpack_from("<4f", rec, QUATERNION)
            print("     f%02d pos=(%8.4f %8.4f %8.4f) q=(%11.4g %11.4g %11.4g %9.6f) angle=%6.2f axis=%s"
                  % (f, pos[0], pos[1], pos[2], quat[0], quat[1], quat[2], quat[3],
                     angle_of(quat), axis_of(quat) or "-"))
    return 0


def print_angles(files):
    """Each file's rotation alphabet: how many records turn, how many land on the degree grid, about what."""
    for path in files:
        d = open(path, "rb").read()
        names, nframes, per_channel, clips = walk(d)
        quats = [struct.unpack_from("<4f", rec, QUATERNION)
                 for recs in per_channel.values() for rec in recs]
        degs = [angle_of(q) for q in quats]
        err = max(norm_err(q) for q in quats)
        grid = sum(1 for x in degs if on_grid(x))
        moving = [x for x in degs if x > 1e-3]
        axes = collections.Counter(axis_of(q) for q in quats if angle_of(q) > 1e-3)
        c = collections.Counter(round(x, 1) for x in moving)
        print("%-34s records=%4d |q|-1<=%.1e moving=%3d grid=%.3f zero=%4d max=%5.1f axes=%s top=%s"
              % (os.path.basename(path), len(quats), err, len(moving), grid / len(degs),
                 sum(1 for x in degs if x <= 1e-3), max(degs), axes.most_common(2), c.most_common(3)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="directory of .lad files (walked recursively)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--bones", action="store_true", help="print one file's ordered bone list")
    ap.add_argument("--angles", action="store_true", help="each file's rotation alphabet, grid and axes")
    ap.add_argument("--records", action="store_true", help="every frame's record for one file")
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
    if args.records:
        if len(files) != 1:
            ap.error("--records takes exactly one file")
        return print_records(files[0])
    if args.angles:
        return print_angles(files)
    rows = collect(files, args.out, args.dir)
    for p, rs in by_file(rows).items():
        clips = sorted({c for r in rs for c in r["clips"].split(";") if c})
        print("%-34s channels=%2d bones=%2d clips=%d rotations=%.1f..%.1f |q|-1<=%.1e"
              % (os.path.basename(p), len(rs), len(rs), len(clips),
                 min(float(r["angle_max"]) for r in rs), max(float(r["angle_max"]) for r in rs),
                 max(float(r["norm_err"]) for r in rs)))
    print("wrote %s" % os.path.join(args.out, CSV))
    return 0


if __name__ == "__main__":
    sys.exit(main())
