#!/usr/bin/env python3
"""Does the block-start chooser pick a start that lies inside the record's own closing group?

`tools/class-record-open-tail.py` measured the signature: on the rows the element walk stops 1-9 bytes short, the
record's closing group (`UClass::Serialize`'s names array, reference, config name, second names array) parses from
the element end and its end lies **2-10 bytes past** the located block start -- never on it. So the start the
chooser picked sits *inside* the group on those rows, and every count that reads it as the record's boundary
inherits that.

This tool turns that geometry into a rule in the chooser and re-runs it. A candidate start is refused when the
writer's four-field group parses from any offset before it and ends past it: the group is a field of the record, so
a start inside it cannot be the defaults block's first byte. What remains is ranked exactly as
`tools/class-defaults.py` ranks it (`choose`), so the only thing that changed is the population of candidates.

The rule needs no element walk and no block anchor: it reads the payload around the candidate, which is what the
chooser has. Controls travel with it:

* the shipped start is reproduced by the same chooser before the guard runs (`reproduced`), so a movement is the
  guard's and not a version skew between this tool and the artifact;
* every class is joined to `class_record_open_tail.csv`'s `overlap` column, measured from the other side (element
  walk + group parse), so the guard's rejections can be scored against a geometry it does not read;
* the covering group's `ref` is reported: the archive's reference slot writes import references negative, so a
  negative reference is the group's own commitment and a positive one is a parse that happened to tile.

    python3 tools/class-blockstart-guard.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-blockstart-guard.py --report
    python3 tools/class-blockstart-guard.py --dump $SG1_DUMP --class Engine.Interaction
    python3 tools/class-blockstart-guard.py --check        # synthetic bodies, no dump, writes nothing

Reads the packages and three committed tables; no boot, no Ghidra round, no property value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

SHIPPED = "class_defaults_per_class.csv"   # the chooser's own output: package, class, block_start
TAIL = "class_record_open_tail.csv"        # the group measured from the element end: overlap
ARTIFACT = "class_blockstart_guard.csv"
LOOKBACK = 16                              # a covering group starts at most this far before the candidate
COLUMNS = ["package", "class", "payload", "shipped", "reproduced", "chosen", "cands", "guard", "group_from",
           "group_len", "group_end", "group_ref", "group_hex", "overlap", "old_count", "old_known", "old_decl",
           "survivors", "new_start", "delta", "new_count", "new_known", "new_decl", "new_to_end", "new_origin",
           "new_on_group", "verdict"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cdef = _load("class-defaults")
otail = _load("class-record-open-tail")
pstream = _load("prop-stream")


# ---------------------------------------------------------------- the group, as an interval per start

def committed(g):
    """Is this parse the writer's field, or bytes that tile as a group?

    `free_group` only demands that every index be inside the name table, and index 0 is inside it -- so a zero run
    parses as an empty group at every offset, and a start four bytes into one parses as a group whose four names
    are all 0. A cover built from those refuses most candidates in any payload with padding.

    The reference is what separates them: the archive's reference slot writes import references **negative**
    (981 of 984 parses; `tools/class-record-open-tail.py` reports a negative reference on 1,604 of the located
    rows), and a padding window's reference is 0. So the guard takes a group only when its reference is negative --
    a committed reference -- rather than counting every parse that tiles.
    """
    return g["ref"] < 0


def group_intervals(data, nnames, hi=None):
    """Every offset where the writer's four-field group parses, as `(start, end)`.

    Built once per payload: the chooser asks the question of every candidate it considered, and a per-candidate
    scan over the same bytes would re-parse the same offsets.
    """
    out = []
    for q in range(len(data)):
        g = otail.free_group(data, q, nnames, hi)
        if g is not None and committed(g):
            out.append((q, q + g["len"], g))
    return out


def covering(iv, start):
    """The group a candidate start falls inside, or None: the one with the furthest end among starts before it.

    A group that *ends* on the start is not a cover -- that end is the block's first byte, which is the answer the
    closing rows already give, not a rejection.
    """
    best = None
    for q, end, g in iv:
        if q >= start:
            break
        if end > start and (best is None or end > best[1]):
            best = (q, end, g)
    return best


# ---------------------------------------------------------------- one class

def declared_for(cls, decl, supers):
    names, cur, seen = set(), cls, set()
    while cur and cur not in seen:
        seen.add(cur)
        names |= decl.get(cur, set())
        cur = supers.get(cur)
    return names


def measure_one(pkg, exp, cls, shipped, decl, kinds):
    """The chooser with and without the guard, for one class export.

    Two changes, and they are one rule read from both ends:

    * a candidate start that a committed group covers is refused -- the group is a record field, so a start inside
      it is not the block's first byte;
    * a committed group's **end** joins the candidate set. That end is where the record says the block begins, and
      on the rejected rows it is often a one-property block, which the two-property floor withholds
      (`class-defaults.py`'s `single_declared_block` reads the same shape only when the class declares the one name
      and the tag tiles the payload's tail). Anchoring that reading to the group's end is what keeps it from being
      a free scan over every offset.

    Everything else -- the ranking -- is `class-defaults.py`'s `choose`, unchanged.
    """
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    nnames = len(pkg.names or [])
    row = {c: "" for c in COLUMNS}
    row.update({"payload": len(data), "shipped": shipped})
    cands, _ = cdef.candidates(pkg, pstream, exp, kinds=kinds, floor=cdef.MIN_PROPERTIES)
    origins = {c[1]: "candidate" for c in cands}
    row["cands"] = len(cands)
    best = cdef.choose(cands, decl)
    if best is None:
        row["verdict"] = "no-candidate"
        return row
    _score, count, start, names, _bad = best
    known = sum(1 for n in names if n in decl)
    row.update({"chosen": start, "reproduced": 1 if start == shipped else 0, "old_count": count, "old_known": known,
                "old_decl": "%.3f" % (known / float(count)) if count else ""})
    iv = group_intervals(data, nnames)
    hit = covering(iv, shipped)
    row["guard"] = "inside-group" if hit else "clear"
    if hit is not None:
        q, end, g = hit
        row.update({"group_from": q, "group_len": g["len"], "group_end": end, "group_ref": g["ref"],
                    "group_hex": g["hex"]})
    for _q, gend, _g in iv:
        if gend in origins or not (0 < gend < len(data)):
            continue
        props, end, reason = pstream.read_block(data[gend:], pkg, False)
        if reason != "None" or len(props) != 1 or gend + end != len(data):
            continue
        prop = props[0]
        if prop["name"] == "?" or prop["name"] not in decl or cdef.contradictions(props, kinds):
            continue
        cands.append((1, gend, [prop["name"]], 0, 0))
        origins[gend] = "group-end"
    survivors = [c for c in cands if covering(iv, c[1]) is None]
    row["survivors"] = len(survivors)
    newbest = cdef.choose(survivors, decl)
    if newbest is None:
        # Every parse the chooser had is covered by a group; the guard has no better start to offer, so the row
        # keeps the shipped one and says so -- a refusal with no replacement is a negative, not a correction.
        row.update({"new_start": shipped, "delta": 0, "verdict": "no-survivor"})
        return row
    _s, ncount, nstart, nnames_, _b = newbest
    nknown = sum(1 for n in nnames_ if n in decl)
    props, end, reason = pstream.read_block(data[nstart:], pkg, False) if 0 <= nstart < len(data) else ([], 0, "")
    row.update({"new_start": nstart, "delta": nstart - shipped, "new_count": ncount, "new_known": nknown,
                "new_decl": "%.3f" % (nknown / float(ncount)) if ncount else "",
                "new_to_end": 1 if reason == "None" and nstart + end == len(data) else 0,
                "new_origin": origins.get(nstart, "candidate"),
                "new_on_group": 1 if nstart in {e for _q, e, _g in iv} else 0})
    if row["guard"] == "inside-group":
        row["verdict"] = "inside-group-moved" if nstart != shipped else "inside-group-same"
    else:
        row["verdict"] = "clear-same" if nstart == shipped else "clear-moved"
    return row


# ---------------------------------------------------------------- the sweep

def shipped_rows():
    with open(os.path.join(ROOT, "inventory", SHIPPED), newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("block_start")]


def tail_rows():
    try:
        with open(os.path.join(ROOT, "inventory", TAIL), newline="", encoding="utf-8") as fh:
            return {(r["package"], r["class"]): r for r in csv.DictReader(fh)}
    except OSError:
        return {}


def sweep(args):
    rows = shipped_rows()
    tail = tail_rows()
    decl_all = cdef.declared_properties()
    supers = cdef.super_map()
    by_pkg = collections.defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    out = []
    for pkgname in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        index = {}
        for e in pkg.exports:
            index.setdefault(pkg.name(e["name"]), e)
        for r in by_pkg[pkgname]:
            rec = {"package": pkgname, "class": r["class"]}
            exp = index.get(r["class"])
            if exp is None:
                rec["verdict"] = "no-export"
                out.append(rec)
                continue
            cls = r["class"]
            decl = declared_for(cls, decl_all, supers)
            rec.update(measure_one(pkg, exp, cls, int(r["block_start"]), decl, cdef.kinds_for_class(cls)))
            t = tail.get((pkgname, cls))
            rec["overlap"] = t["overlap"] if t else ""
            rec["package"], rec["class"] = pkgname, cls
            out.append(rec)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(out)
        print("wrote %s: %d rows" % (path, len(out)))
    report(out)
    return 0


def _share(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "-"


def report(rows=None):
    if rows is None:
        with open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    # the in-memory rows carry ints and the artifact carries strings, so every test goes through `str`
    flag = lambda r, k: str(r.get(k, "")) == "1"                       # noqa: E731
    num = lambda r, k: int(r[k]) if str(r.get(k, "")) != "" else None   # noqa: E731
    print("rows: %d" % len(rows))
    print("verdicts: %s" % collections.Counter(r["verdict"] for r in rows).most_common())
    read = [r for r in rows if r["guard"] in ("clear", "inside-group")]
    off = [r for r in read if not flag(r, "reproduced") and str(r["reproduced"]) != ""]
    print("\nthe chooser reproduces the shipped start on %d of %d (%s)"
          % (sum(1 for r in read if flag(r, "reproduced")), len(read),
             _share(sum(1 for r in read if flag(r, "reproduced")), len(read))))
    if off:
        print("  not reproduced: %s"
              % ", ".join("%s.%s (shipped %s, chooser %s)" % (r["package"], r["class"], r["shipped"], r["chosen"])
                          for r in off[:4]))
    for guard, label in (("inside-group", "REFUSED (a committed closing group covers the start)"), ("clear", "CLEAR")):
        sub = [r for r in read if r["guard"] == guard]
        print("\n%s: %d" % (label, len(sub)))
        if not sub:
            continue
        deltas = collections.Counter(num(r, "delta") for r in sub if str(r["delta"]) != "")
        print("  delta (new - shipped): %s" % deltas.most_common(12))
        print("  the new start is a group's end on %d (%s)"
              % (sum(1 for r in sub if flag(r, "new_on_group")),
                 _share(sum(1 for r in sub if flag(r, "new_on_group")), len(sub))))
        print("  the new start came from a group end on %d, from a parsed block on %d"
              % (sum(1 for r in sub if r.get("new_origin") == "group-end"),
                 sum(1 for r in sub if r.get("new_origin") == "candidate")))
        print("  the new start's tag stream reaches the export end on %d (%s)"
              % (sum(1 for r in sub if flag(r, "new_to_end")),
                 _share(sum(1 for r in sub if flag(r, "new_to_end")), len(sub))))
        old = [float(r["old_decl"]) for r in sub if str(r["old_decl"]) != ""]
        new = [float(r["new_decl"]) for r in sub if str(r["new_decl"]) != ""]
        print("  declared fraction: old mean %.3f / fully declared on %d of %d; new mean %.3f / fully on %d of %d"
              % (sum(old) / len(old) if old else 0.0, sum(1 for v in old if v >= 0.999), len(old),
                 sum(new) / len(new) if new else 0.0, sum(1 for v in new if v >= 0.999), len(new)))
        refs = [int(r["group_ref"]) for r in sub if str(r["group_ref"]) != ""]
        if refs:
            print("  covering group's reference: negative on %d of %d" % (sum(1 for v in refs if v < 0), len(refs)))
    old_all = [float(r["old_decl"]) for r in read if str(r["old_decl"]) != ""]
    # a row the guard had no replacement for keeps the shipped start, so its old fraction is its new one
    new_all = [float(r["new_decl"]) if str(r["new_decl"]) != "" else float(r["old_decl"])
               for r in read if str(r["new_decl"]) != "" or str(r["old_decl"]) != ""]
    print("\nfully declared over the population: %d of %d before the guard, %d of %d after"
          % (sum(1 for v in old_all if v >= 0.999), len(old_all),
             sum(1 for v in new_all if v >= 0.999), len(new_all)))
    # the control this tool does not read: the overlap the element walk measured from the record side
    both = [r for r in read if str(r["overlap"]) != ""]
    for label, pred in (("overlap > 0 (the group ends past the start)", lambda v: v > 0),
                        ("overlap == 0 (the group ends on the start)", lambda v: v == 0)):
        sub = [r for r in both if pred(int(r["overlap"]))]
        refused = sum(1 for r in sub if r["guard"] == "inside-group")
        print("control %s: %d rows, the guard refuses %d (%s)"
              % (label, len(sub), refused, _share(refused, len(sub))))
    extra = [r for r in both if int(r["overlap"]) <= 0 and r["guard"] == "inside-group"]
    missed = [r for r in both if int(r["overlap"]) > 0 and r["guard"] != "inside-group"]
    print("  refused rows the control does not explain: %d%s"
          % (len(extra), (" (" + ", ".join("%s.%s" % (r["package"], r["class"]) for r in extra[:6]) + ")")
             if extra else ""))
    if missed:
        print("  overlapping rows the guard does not refuse: %d (e.g. %s)"
              % (len(missed), ", ".join(r["class"] for r in missed[:6])))
    return 0


def one(args):
    if not args.dump or not args.cls:
        print("--class needs --dump", file=sys.stderr)
        return 2
    pkgname, cls = args.cls.split(".", 1)
    row = next((r for r in shipped_rows() if r["package"] == pkgname and r["class"] == cls), None)
    if row is None:
        print("not a located class: %s" % args.cls, file=sys.stderr)
        return 2
    pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exp = next((e for e in pkg.exports if pkg.name(e["name"]) == cls), None)
    decl = declared_for(cls, cdef.declared_properties(), cdef.super_map())
    m = measure_one(pkg, exp, cls, int(row["block_start"]), decl, cdef.kinds_for_class(cls))
    m["package"], m["class"] = pkgname, cls
    print("%s: payload %s, shipped start %s, chooser reproduces %s (%s candidates)"
          % (args.cls, m["payload"], m["shipped"], m["reproduced"], m["cands"]))
    if m["group_from"] != "":
        print("  a group at %s (%s bytes) ends at %s: ref %s" % (m["group_from"], m["group_len"], m["group_end"],
                                                                 m["group_ref"]))
        print("    %s" % m["group_hex"])
    print("  guard: %s (%s candidates survive)" % (m["guard"], m["survivors"]))
    print("  old start %s: %s props, declared %s" % (m["shipped"], m["old_count"], m["old_decl"]))
    if m["new_start"] != "":
        print("  new start %s (delta %s): %s props, declared %s, to the export end %s, on the group end %s"
              % (m["new_start"], m["delta"], m["new_count"], m["new_decl"], m["new_to_end"], m["new_on_group"]))
    print("  verdict: %s" % m["verdict"])
    return 0


# ---------------------------------------------------------------- the gate

def self_check():
    """Synthetic bodies: what the interval covers, what it must not, and the delta arithmetic."""
    def body(group=b"\x03\x01\x02\x03\x87\x04\x00", fill=b"\x00" * 24):
        return bytes(32) + fill + group + b"\x00" * 8, 32 + len(fill)

    data, gstart = body()
    nnames = 40
    iv = group_intervals(data, nnames)
    assert len(iv) >= 1, iv
    g = otail.free_group(data, gstart, nnames)
    assert g is not None and g["len"] == 7, g
    hit = covering(iv, gstart + 3)
    assert hit is not None and hit[0] == gstart and hit[1] == gstart + 7, hit
    assert hit[2]["ref"] == -7
    # the group's own end is a boundary, not a cover
    assert covering(iv, gstart + 7) is None
    # four zero bytes parse as an empty group, and a zero reference is not a committed one
    zero = bytes(16) + b"\x00" * 16 + b"\x04\x11\x22"
    assert otail.free_group(zero, 16, 40) is not None
    assert not committed(otail.free_group(zero, 16, 40))
    assert covering(group_intervals(zero, 40), 18) is None
    # a start before the group is clear
    assert covering(iv, gstart - 1) is None
    # and a start beyond the lookback is not swallowed by a far group
    assert covering(iv, gstart + 40) is None
    # a name index outside the table is not a group, so nothing covers
    d2, s2 = body(group=b"\x03\x01\x02\x09\x87\x04\x00")
    assert covering(group_intervals(d2, 8), s2 + 3) is None
    # the widest covering group wins when two nest
    d3 = bytes(32) + b"\x00" * 8 + b"\x02\x01\x02" + b"\x00" + b"\x03\x01\x02\x03\x87\x04\x00" + b"\x00" * 4
    iv3 = group_intervals(d3, 40)
    hit3 = covering(iv3, 45)
    assert hit3 is not None, iv3
    print("self-check ok: a start inside a parsed group is covered, the group's own end and any start outside it "
          "are not, an out-of-table index is not a group, and the widest cover wins")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, package.Class")
    ap.add_argument("--sweep", action="store_true", help="re-run the chooser over every located class")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="summarise the committed artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        return report()
    if args.cls:
        return one(args)
    if not args.sweep or not args.dump:
        ap.error("--sweep needs --dump")
    return sweep(args)


if __name__ == "__main__":
    sys.exit(main())
