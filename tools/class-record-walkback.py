#!/usr/bin/env python3
"""Walk back from the class record's stride-9 run at the record's own widths, and say where its head is.

`tools/class-record-run.py` measured two encodings of a `TArray` head in front of the run -- a compact count
inside 6 bytes (16 of 536, against a 3.6% background) and a raw dword (0 of 536) -- and refused both, then a
9-byte lattice sweep up to 40 units back (34.3% against a ~78% chance rate for the same sweep). The reading that
survived is the writer's own: `FUN_0003d900` streams each record as one object reference plus two dwords and the
reference is a compact index of **1 to 5 bytes**, so the array's earlier elements need not be 9 bytes wide and its
head sits in front of a *variable*-stride lattice that no fixed step reaches.

This tool asks that question, and pairs every answer with a null:

* **the walk.** From the run's first element boundary -- the byte `longest_run` identifies as the record's
  reference -- step back one element at a time, each element occupying `reference (1-5 bytes) + two dwords`. An
  element is only taken when its reference decodes to exactly its own width and names an object the package has
  (a positive index inside the export table, a negative one inside the import table). A step with more than one
  such width is recorded as ambiguous rather than guessed; the tool reports both the *deterministic* depth (how far
  a single-candidate chain runs before the bytes stop agreeing) and every head a branching walk reaches.
* **the head.** A head is accepted only where a compact count **ending at the element boundary the walk reached**
  equals the elements walked back plus the run's own unit count. The count that sits there whatever its value is
  recorded too (`stop_count`), because "a count field is there but it is not this array's" and "there is no count
  field" are different answers.
* **the null.** The same walk, same widths, aimed one byte past the run's first element boundary. A chain of
  variable steps is cheap to fit, so a rule that finds a head there as often as here has no discriminating power.
* **what the run repeats.** The dword is decoded as the compact reference it looks like and resolved through the
  package's import/export tables, so the array's element has a name instead of a hex constant.

    python3 tools/class-record-walkback.py --dump $SG1_DUMP --class AmbientCreatures.Birds
    python3 tools/class-record-walkback.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-walkback.py --report
    python3 tools/class-record-walkback.py --check

Reads the packages and the committed prefix table; no boot, no value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402


def _load(mod):
    spec = importlib.util.spec_from_file_location(mod.replace("-", "_"), os.path.join(HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mrm = _load("class-record-middle")
crf = _load("class-record-fields")

ROOT = os.path.dirname(HERE)
UNIT = mrm.UNIT            # the element's cooked width when its reference is one byte
MIN_RUN = mrm.MIN_RUN
HEAD_GAP = 6               # how far in front of a boundary a count may end and still be called its head
MAX_REF = 5                # the writer's own upper bound for a compact object reference
MAX_STEPS = 32             # elements the walk may take back, i.e. 416 bytes at the widest element
MAX_NODES = 512            # branching cap: head candidates are rare, so this is never a budget question
ARTIFACT = "class_record_walkback.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "middle", "run_start", "run_units",
           "run_const", "const_side", "const_value", "const_obj", "const_class",
           "det_steps", "det_stop", "stop_gap", "stop_len", "stop_count", "near_miss",
           "walk_steps", "walk_heads", "walk_refs", "head_at", "head_gap", "head_len", "head_val",
           "front", "ctl_det", "ctl_steps", "head_ok", "ctl_ok"]


def ref_pos(shift):
    """The byte inside the detected unit that is not part of the repeating dword: the record's reference."""
    return 8 if shift == 0 else (0 if shift == 4 else 4)


def read_ref(buf, p, width):
    """The compact reference occupying `buf[p:p+width]`, or None if it does not decode to exactly that width."""
    if p < 0 or p + width > len(buf):
        return None
    try:
        v, e = crf.read_index(buf, p)
    except IndexError:
        return None
    if e != p + width or v == 0:
        return None
    return v


def in_table(v, nimp, nex):
    """A statement about the package: a reference that names an object the package actually has."""
    return v > 0 and v <= nex or v < 0 and -v <= nimp


def steps_back(buf, s, nimp, nex):
    """Every element the record's own widths allow to end exactly at `s`: reference 1-5 bytes plus two dwords."""
    out = []
    for width in range(1, MAX_REF + 1):
        p = s - 8 - width
        if p < 0:
            break
        v = read_ref(buf, p, width)
        if v is not None and in_table(v, nimp, nex):
            out.append((p, width, v))
    return out


def nearest_count(buf, end, want=None, gaps=HEAD_GAP):
    """The nearest compact index ending within `gaps` bytes before `end`, optionally only if it equals `want`."""
    for gap in range(gaps):
        e = end - gap
        if e <= 0:
            break
        for c in range(e - 1, max(e - 6, -1), -1):
            if c < 0:
                continue
            try:
                v, e2 = crf.read_index(buf, c)
            except IndexError:
                continue
            if e2 != e:
                continue
            if want is None or v == want:
                return {"head_at": c, "head_gap": gap, "head_len": e - c, "head_val": v}
    return None


def deterministic_depth(buf, start, nimp, nex):
    """How far a single-candidate chain runs back from `start`, and how many candidates stopped it."""
    s, k = start, 0
    while k < MAX_STEPS:
        cands = steps_back(buf, s, nimp, nex)
        if len(cands) != 1:
            return k, len(cands)
        s, _, _ = cands[0]
        k += 1
    return k, 1


def walk_back(buf, start, units, nimp, nex):
    """Breadth-first walk back from the run's first element boundary, one variable-width element per step.

    A head must be **adjacent**: an array's count is immediately followed by its first element, so a count that
    ends 1-5 bytes early is recorded as a near miss rather than accepted.
    """
    heads, nodes, near = [], 0, 0
    frontier = [(start, 0, [])]
    while frontier and nodes < MAX_NODES:
        s, k, refs = frontier.pop(0)
        nodes += 1
        if k >= MAX_STEPS:
            continue
        for (p, width, v) in steps_back(buf, s, nimp, nex):
            h = nearest_count(buf, p, units + k + 1, gaps=1)
            if h:
                h["walk_steps"] = k + 1
                h["walk_refs"] = ",".join(str(x) for x in refs + [v])
                heads.append(h)
            elif nearest_count(buf, p, units + k + 1):
                near += 1
            frontier.append((p, k + 1, refs + [v]))
    return heads, near


def resolve(pkg, const):
    """Decode the repeating dword as a compact reference and name the object it points at."""
    out = {"const_side": "", "const_value": "", "const_obj": "", "const_class": ""}
    try:
        v, e = crf.read_index(const, 0)
    except IndexError:
        return out
    if e > len(const) or any(const[e:]):
        out["const_side"] = "multi"          # more than one field inside the dword: not one reference
        return out
    out["const_value"] = v
    if v == 0:
        out["const_side"] = "null"
        return out
    if v > 0:
        out["const_side"] = "export"
        if v <= len(pkg.exports):
            out["const_obj"] = pkg.name(pkg.exports[v - 1]["name"])
        return out
    out["const_side"] = "import"
    if -v <= len(pkg.imports):
        imp = pkg.imports[-v - 1]
        out["const_obj"] = pkg.name(imp["name"])
        out["const_class"] = pkg.name(imp["class"])
    return out


def measure(pkg, data, prefix_end, block_start):
    """One class: the run, the walk back from it, the count at its head, and the null."""
    row = {c: "" for c in COLUMNS}
    row.update({"payload": len(data), "prefix_end": prefix_end, "block_start": block_start, "head_ok": 0,
                "ctl_ok": 0})
    middle = data[prefix_end:block_start] if block_start >= prefix_end else b""
    row["middle"] = len(middle)
    units, start, shift, const = mrm.longest_run(middle)
    if units < MIN_RUN:
        return row
    row.update({"run_start": start, "run_units": units, "run_const": const.hex()})
    row.update(resolve(pkg, const))
    nimp, nex = len(pkg.imports), len(pkg.exports)
    elt = start + ref_pos(shift)
    if elt + UNIT > len(middle):
        return row
    det, stop = deterministic_depth(middle, elt, nimp, nex)
    row["det_steps"] = det
    row["det_stop"] = stop
    head_pos = elt
    s = elt
    for _ in range(det):
        s, _, _ = steps_back(middle, s, nimp, nex)[0]
        head_pos = s
    near = nearest_count(middle, head_pos)
    if near:
        row.update({"stop_gap": near["head_gap"], "stop_len": near["head_len"], "stop_count": near["head_val"]})
    heads, near = walk_back(middle, elt, units, nimp, nex)
    row["near_miss"] = near
    if heads:
        best = min(heads, key=lambda h: h["walk_steps"])
        row.update({"walk_steps": best["walk_steps"], "walk_heads": len({h["walk_steps"] for h in heads}),
                    "walk_refs": best["walk_refs"], "head_at": best["head_at"], "head_gap": best["head_gap"],
                    "head_len": best["head_len"], "head_val": best["head_val"], "head_ok": 1,
                    "front": best["head_at"]})
    ctl_det, ctl_steps = deterministic_depth(middle, elt + 1, nimp, nex)
    row["ctl_det"] = ctl_det
    row["ctl_steps"] = ctl_steps
    row["ctl_ok"] = int(bool(walk_back(middle, elt + 1, units, nimp, nex)[0]))
    return row


def sweep(args):
    prefixes = crf.read_prefixes()
    rows = []
    for fn in sorted(os.listdir(os.path.join(args.dump, "System"))):
        if not fn.endswith(".u"):
            continue
        pkg = Package(os.path.join(args.dump, "System", fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        name = fn[:-2]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            key = (name, pkg.name(exp["name"]))
            if key not in prefixes:
                continue
            start, block = prefixes[key]
            if block <= 0:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            row = measure(pkg, data, start, block)
            row.update({"package": name, "class": key[1]})
            rows.append(row)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return 0


def _ints(rows, key):
    return [int(r[key]) for r in rows if r[key] not in ("", None)]


def _pct(n, d):
    return 100.0 * n / d if d else 0.0


def report(rows):
    located = [r for r in rows if int(r.get("block_start") or 0) > 0]
    runs = [r for r in located if r["run_units"] not in ("", None)]
    print("classes with a located block start: %d" % len(located))
    print("a stride-%d run of >=%d units in the middle: %d (%.1f%%)"
          % (UNIT, MIN_RUN, len(runs), _pct(len(runs), len(located))))
    if not runs:
        return
    det = _ints(runs, "det_steps")
    print("the walk back from the run at the record's own widths (1-5 byte reference + two dwords):")
    print("  steps taken while exactly one element width fits: median %d, max %d; a single step on %d of %d, "
          "three on %d" % (statistics.median(det), max(det), sum(1 for v in det if v >= 1),
                           len(runs), sum(1 for v in det if v >= 3)))
    print("  what stopped the chain: %s" % collections.Counter(int(r["det_stop"]) for r in runs).most_common(6))
    hit = [r for r in runs if int(r["head_ok"])]
    print("a compact count equal to the elements walked back + the run's units, at the head: %d of %d (%.1f%%)"
          % (len(hit), len(runs), _pct(len(hit), len(runs))))
    print("  null (the same walk aimed one byte past the boundary): %d of %d (%.1f%%)"
          % (sum(int(r["ctl_ok"]) for r in runs), len(runs), _pct(sum(int(r["ctl_ok"]) for r in runs), len(runs))))
    print("  the null's own chain: median %d steps, stopped by %s"
          % (statistics.median(_ints(runs, "ctl_det")) if _ints(runs, "ctl_det") else 0,
             collections.Counter(int(r["ctl_steps"]) for r in runs).most_common(4)))
    if hit:
        print("  the count that matched: %s; its width %s"
              % (collections.Counter(int(r["head_val"]) for r in hit).most_common(6),
                 collections.Counter(int(r["head_len"]) for r in hit).most_common(4)))
        print("  bytes walked back: %s; the window in front of the head: median %d, most common %s"
              % (collections.Counter(int(r["walk_steps"]) for r in hit).most_common(6),
                 statistics.median(_ints(hit, "front")), collections.Counter(_ints(hit, "front")).most_common(5)))
        print("  the references the walk followed: %s"
              % collections.Counter(len(str(r["walk_refs"]).split(",")) for r in hit).most_common(5))
        print("  a head a branching walk reaches but the single-candidate chain does not: %d"
              % sum(1 for r in hit if int(r["walk_steps"]) > int(r["det_steps"])))
    nm = [r for r in runs if r["near_miss"] not in ("", None) and int(r["near_miss"])]
    print("  a count equal to the walked total that ends 1-5 bytes early, refused as not adjacent: %d of %d"
          % (len(nm), len(runs)))
    # is a count there at all, whatever its value?
    # only the chains that ran to the window's front edge without branching say anything about what is in front:
    # a chain stopped by two candidate widths stops where the bytes stop agreeing, not at an array boundary
    counted = [r for r in runs if r["stop_count"] not in ("", None) and int(r["det_stop"]) == 0]
    match = [r for r in counted if int(r["stop_count"]) == int(r["run_units"]) + int(r["det_steps"])]
    print("chains that ran to the front edge without branching: %d of %d; a compact index of any value ends at "
          "their stop, one equal to the elements walked on %d (%.1f%%)"
          % (len(counted), len(runs), len(match), _pct(len(match), len(counted))))
    if counted:
        print("  its value minus (units + steps): %s"
              % collections.Counter(int(r["stop_count"]) - int(r["run_units"]) - int(r["det_steps"])
                                    for r in counted).most_common(6))
        print("  gap to the stop position: %s"
              % collections.Counter(int(r["stop_gap"]) for r in counted).most_common(4))
    print("the dword that repeats across the run, decoded as the reference it looks like:")
    print("  side: %s" % collections.Counter(r["const_side"] for r in runs).most_common(6))
    named = [r for r in runs if r["const_obj"]]
    print("  resolves to an object in its own package: %d of %d (%.1f%%)"
          % (len(named), len(runs), _pct(len(named), len(runs))))
    if named:
        print("  the objects (top 12): %s"
              % collections.Counter("%s:%s" % (r["const_side"], r["const_obj"]) for r in named).most_common(12))
        same = collections.Counter((r["package"], r["const_obj"]) for r in named)
        print("  distinct (package, object) pairs: %d; the commonest holds %d classes"
              % (len(same), same.most_common(1)[0][1]))


def one(args):
    prefixes = crf.read_prefixes()
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    for exp in pkg.exports:
        if pkg.class_name_of(exp) != "Class" or pkg.name(exp["name"]) != args.cls:
            continue
        if (args.package, args.cls) not in prefixes:
            print("no located block start for this class")
            return 2
        start, block = prefixes[(args.package, args.cls)]
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        row = measure(pkg, data, start, block)
        print("%s.%s payload %d prefix %d block start %d middle %d"
              % (args.package, args.cls, len(data), start, block, row["middle"]))
        if row["run_units"] == "":
            print("no stride-%d run of >=%d units" % (UNIT, MIN_RUN))
            return 0
        print("run %s units from %s in the middle; it repeats %s (%s %s%s)"
              % (row["run_units"], row["run_start"], row["run_const"], row["const_side"],
                 row["const_value"], " " + row["const_obj"] if row["const_obj"] else ""))
        print("walk: %s steps with one width, stopped by %s candidates; a count of any value at that stop: %s"
              % (row["det_steps"], row["det_stop"], row["stop_count"] or "-"))
        if int(row["head_ok"]):
            print("HEAD: count %s at %s (gap %s, %s bytes), %s elements walked, references %s"
                  % (row["head_val"], row["head_at"], row["head_gap"], row["head_len"], row["walk_steps"],
                     row["walk_refs"]))
        else:
            print("no head: no compact count equal to units + steps ends at any boundary the walk reaches")
        print("null: %s steps with one width, matching count %s"
              % (row["ctl_det"], "yes" if int(row["ctl_ok"]) else "no"))
        return 0
    print("no such class")
    return 2


def self_check():
    """Synthetic records: a variable-stride array whose head a walk finds, and the same bytes shifted by one."""
    def compact(v):
        v = abs(v)
        out, more = v & 0x3F, v >> 6
        if not more:
            return bytes([out])
        return bytes([0x40 | out]) + compact(more)

    def element(ref, d1, d2):
        return compact(ref) + d1 + d2

    a, b = b"\x11\x22\x33\x44", b"\x55\x66\x77\x88"
    run = b"".join(bytes([ref]) + a + b for ref in (0x01, 0x02, 0x03))
    lead = (element(300, b"\x99\x88\x77\x66", b"\x55\x44\x33\x22")
            + element(7, b"\xaa\xbb\xcc\xdd", b"\x66\x55\x44\x33"))
    prefix = compact(110) + b"\x00" + compact(5)
    body = compact(5) + lead + run
    payload = prefix + body + bytes(12)
    mid_start, block = len(prefix), len(prefix) + len(body)

    units, start, shift, const = mrm.longest_run(payload[mid_start:block])
    assert units == 3 and const == b"\x44\x55\x66\x77", (units, start, shift, const.hex())
    assert ref_pos(shift) == 0 and start == len(compact(5)) + len(lead), (start, shift)

    class P:            # the package the measure() signature wants: two tables and a name reader
        names = [("x",)] * 4096
        imports = [{"name": 0, "class": 0}] * 64
        exports = [{"name": 0}] * 4096

    row = measure(P, payload, mid_start, block)
    assert row["run_units"] == 3 and row["const_side"] in ("multi", "export"), row
    # the first step is unambiguous (the 7), the second is not: the first byte of a two-byte reference also
    # decodes as a one-byte one, which is why the walk has to branch rather than pick a width
    assert row["det_steps"] == 1 and int(row["det_stop"]) >= 2, row
    assert row["head_ok"] == 1 and row["walk_steps"] == 2, row
    assert int(row["head_val"]) == 5 and int(row["head_gap"]) == 0 and int(row["head_len"]) == 1, row
    assert row["walk_refs"] == "7,300", row                 # walked back: the 7 first, then the 300
    assert int(row["ctl_ok"]) == 0, row

    # a count that is not this array's must not be accepted, and the chain must still stop where it did
    wrong = prefix + compact(9) + lead + run + bytes(12)
    row2 = measure(P, wrong, mid_start, block)
    assert row2["head_ok"] == 0 and row2["det_steps"] == 1, row2

    # a reference that names nothing in the package ends the chain instead of being walked over
    wild = prefix + compact(5) + element(9000, a, b) + element(7, a, b"\x01\x02\x03\x04") + run + bytes(12)
    row3 = measure(P, wild, mid_start, block)
    assert row3["head_ok"] == 0, row3
    print("self-check ok: a 2-element variable-stride array takes a count of 5 at its head, refuses 9, refuses a "
          "reference outside the package's tables, and its null finds nothing")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/, Maps/, ...)")
    ap.add_argument("--class", dest="cls", help="one class, with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--sweep", action="store_true", help="measure every located class")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="print the committed artifact's tables")
    ap.add_argument("--check", action="store_true", help="self-test, no dump")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        with open(path, newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.dump:
        ap.error("--dump is required (or --check)")
    if args.cls:
        if "." in args.cls and not args.package:
            args.package, args.cls = args.cls.split(".", 1)
        return one(args)
    if args.sweep:
        return sweep(args)
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
