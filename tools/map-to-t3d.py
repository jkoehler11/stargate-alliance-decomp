#!/usr/bin/env python3
"""Write a map's actors out as T3D (PLAN 5.2).

The parsing is done (`docs/ACTOR_PROPERTIES.md`): a `.unr`'s actors are exports, and 62% of exports — every
actor — carry a readable property block. This is the formatting step: one `Begin Actor` … `End Actor` section
per actor, with the properties written the way the editor writes them.

    python3 tools/map-to-t3d.py DUMP/Maps/M02A.unr --out $WORK/extracted/maps
    python3 tools/map-to-t3d.py DUMP/Maps --out $WORK/extracted/maps --stats inventory/t3d_coverage.csv

**An array is its elements** (2026-11-19). An `Array` property's payload is a compact count followed by exactly
that many compact indices when its elements are object references — the rule `tools/action-wiring.py` measured
on `Actions`, now applied to every array. A payload that does not consume itself exactly, or whose indices do
not land in the package's export/import tables, is *not* read as objects: a guess that happens to consume its
bytes would print invented actor names, so it is counted as a refusal instead. Arrays that are not object
arrays are counted with their count and element width (`rest / count`, when it divides) and left as a comment:
an array's element type lives in the class's property declaration, which this reader does not carry.

Output is game-derived content: `$WORK` only, never this repository (`.gitignore` keeps `/work` and `/out` out,
and the docs say the same). What is committed is the tool and the counts it prints.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
VALUES = os.path.normpath(os.path.join(HERE, "..", "inventory", "t3d_values.csv"))
STRUCTS = os.path.normpath(os.path.join(HERE, "..", "inventory", "t3d_structs.csv"))


def load_prop_stream():
    import importlib.util
    path = os.path.join(HERE, "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fmt(value):
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return "%.6g" % value
    if isinstance(value, tuple) and len(value) == 3:
        if all(isinstance(v, float) for v in value):
            return "(X=%.6g,Y=%.6g,Z=%.6g)" % value
        return "(Pitch=%d,Yaw=%d,Roll=%d)" % value
    if isinstance(value, tuple):
        return "(%s)" % ",".join(str(v) for v in value)
    if value is None:
        return '""'
    return '"%s"' % str(value).replace('"', "'")


def resolves(pkg, ref):
    """Does an object reference land in this package's own tables?"""
    if ref == 0:
        return True
    if ref > 0:
        return ref <= len(pkg.exports)
    return -ref <= len(pkg.imports)


def decode_obj_array(raw, pkg, ps):
    """An `Array` payload of object references -> [refs], or None when it does not consume itself.

    Count then that many compact indices, exactly as `action-wiring.py` measured for `Actions`. The extra test
    here is that every index lands in the export/import tables: without it, a struct array whose first byte is a
    plausible count and whose remainder happens to parse would publish invented actors.
    """
    off = 0
    try:
        count, off = ps.read_index(raw, off)
    except IndexError:
        return None
    if count < 0 or count > 8192:
        return None
    refs = []
    for _ in range(count):
        if off >= len(raw):
            return None
        try:
            ref, off = ps.read_index(raw, off)
        except IndexError:
            return None
        if not resolves(pkg, ref):
            return None
        refs.append(ref)
    if off != len(raw):
        return None
    return refs


def element_layout(raw, ps):
    """A non-object array payload -> (count, element width) or None when it does not divide."""
    off = 0
    try:
        count, off = ps.read_index(raw, off)
    except IndexError:
        return None
    rest = len(raw) - off
    if count < 1 or rest <= 0 or rest % count:
        return None
    return count, rest // count


def drive_map(path, outdir, ps, verbose=False):
    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    name = os.path.splitext(os.path.basename(path))[0]
    lines = ["// %s — written by tools/map-to-t3d.py" % name, ""]
    actors = props_total = 0
    st = {"arrays": 0, "object_arrays": 0, "object_elements": 0, "struct_arrays": 0,
          "struct_records": 0, "unshaped": 0, "struct_props": 0, "struct_decoded": 0, "struct_raw": 0}
    structs = {}
    for exp in pkg.exports:
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        found = ps.find_block(data, pkg)
        if not found:
            continue
        _count, start, _ = found
        # re-parse from the located start, this time decoding values: find_block works on names and sizes
        # only, so the values have to be read on a second pass over the same bytes.
        props, _end, _reason = ps.read_block(data[start:], pkg, True)
        cls = pkg.class_name_of(exp)
        lines.append("Begin Actor Class=%s Name=%s_%d" % (cls, cls, exp["index"] + 1))
        for p in props:
            value = p.get("value")
            raw = data[start + p["value_offset"]:start + p["value_offset"] + p["size"]]
            if p["type"] == "Array":
                st["arrays"] += 1
                refs = decode_obj_array(raw, pkg, ps)
                if refs is not None:
                    st["object_arrays"] += 1
                    st["object_elements"] += len(refs)
                    for i, ref in enumerate(refs):
                        lines.append("   %s(%d)=%s" % (p["name"], i, ps.resolve_ref(pkg, ref)))
                    continue
                layout = element_layout(raw, ps)
                if layout:
                    st["struct_arrays"] += 1
                    st["struct_records"] += layout[0]
                    lines.append("   // %s: %d element(s) of %d byte(s), element type not in the export"
                                 % (p["name"], layout[0], layout[1]))
                else:
                    st["unshaped"] += 1
                    lines.append("   // %s: %d byte(s), no self-consuming layout" % (p["name"], len(raw)))
                continue
            if p["type"] == "Struct":
                st["struct_props"] += 1
                sn = p.get("struct") or "?"
                row = structs.setdefault(sn, {"n": 0, "sizes": {}, "decoded": 0})
                row["n"] += 1
                row["sizes"][p["size"]] = row["sizes"].get(p["size"], 0) + 1
                if isinstance(value, (tuple, list)):
                    st["struct_decoded"] += 1
                    row["decoded"] += 1
                else:
                    st["struct_raw"] += 1
            lines.append("   %s=%s" % (p["name"], fmt(value)))
        props_total += len(props)
        lines.append("End Actor")
        lines.append("")
        actors += 1
    text = "\n".join(lines)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, name + ".t3d"), "w") as f:
        f.write(text)
    if verbose:
        print("  %s" % os.path.join(outdir, name + ".t3d"))
    return {"map": name, "actors": actors, "bytes": len(text), "props": props_total,
            "unresolved": text.count("ref:"), "values": st, "structs": structs}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class _Pkg:
    """A stand-in package for the synthetic reader test: N exports, no imports."""

    def __init__(self, n):
        self.exports = [{} for _ in range(n)]
        self.imports = []


def check(values, structs, stats):
    """The censuses are arithmetic on each other, and a synthetic payload drives the reader. No dump."""
    problems = []
    ps = load_prop_stream()
    vrows = read_csv(values)
    if len(vrows) != 73:
        problems.append("%s: %d rows, expected 73" % (values, len(vrows)))
    tot = {"arrays": 0, "object_arrays": 0, "arrays_refused": 0, "struct_props": 0,
           "struct_decoded": 0, "object_elements": 0}
    for r in vrows:
        arrays, obj, refused = int(r["arrays"]), int(r["object_arrays"]), int(r["arrays_refused"])
        sp, sd = int(r["struct_props"]), int(r["struct_decoded"])
        if obj + refused != arrays:
            problems.append("%s: %s says %d object + %d refused != %d arrays"
                            % (values, r["map"], obj, refused, arrays))
        if sd > sp:
            problems.append("%s: %s decodes %d of %d structs" % (values, r["map"], sd, sp))
        if int(r["struct_arrays"]) and not int(r["struct_array_records"]):
            problems.append("%s: %s has struct arrays with no records" % (values, r["map"]))
        for k in tot:
            tot[k] += int(r[k])
    srows = read_csv(structs)
    if not srows:
        problems.append("%s: empty" % structs)
    if sum(int(r["properties"]) for r in srows) != tot["struct_props"]:
        problems.append("%s: struct properties %d != per-map %d"
                        % (structs, sum(int(r["properties"]) for r in srows), tot["struct_props"]))
    if sum(int(r["decoded"]) for r in srows) != tot["struct_decoded"]:
        problems.append("%s: decoded %d != per-map %d"
                        % (structs, sum(int(r["decoded"]) for r in srows), tot["struct_decoded"]))
    if any(not r["sizes"] for r in srows):
        problems.append("%s: a struct row carries no size" % structs)
    if stats:
        crows = read_csv(stats)
        if [r["map"] for r in crows] != [r["map"] for r in vrows]:
            problems.append("%s: the map column disagrees with the values census" % stats)
    if decode_obj_array(b"\x02\x01\x02", _Pkg(2), ps) != [1, 2]:
        problems.append("decode_obj_array refuses a well-formed two-element payload")
    if decode_obj_array(b"\x02\x01", _Pkg(2), ps) is not None:
        problems.append("decode_obj_array accepts a payload one element short")
    if decode_obj_array(b"\x01\x09", _Pkg(2), ps) is not None:
        problems.append("decode_obj_array accepts an index past the export table")
    if element_layout(b"\x02" + b"\x00" * 8, ps) != (2, 4):
        problems.append("element_layout does not divide 8 bytes over 2 elements")
    if problems:
        for p in problems:
            print("FAIL %s" % p)
        return 1
    print("ok   t3d-values: %d maps, %d arrays (%d object with %d elements, %d refused), %d struct properties "
          "over %d struct names, %d decoded" % (len(vrows), tot["arrays"], tot["object_arrays"],
                                                tot["object_elements"], tot["arrays_refused"], tot["struct_props"],
                                                len(srows), tot["struct_decoded"]))
    return 0


def render_doc(values, structs):
    """The page's generated block: the array census and the struct census, from the two artifacts only."""
    vrows = read_csv(values)
    srows = read_csv(structs)
    tot = {k: sum(int(r[k]) for r in vrows) for k in
           ("arrays", "object_arrays", "object_elements", "arrays_refused", "struct_arrays",
            "struct_array_records", "struct_props", "struct_decoded", "struct_raw")}
    out = []
    out.append("| measure | value |")
    out.append("|---|---:|")
    out.append("| maps | %d |" % len(vrows))
    out.append("| `Array` properties | %d |" % tot["arrays"])
    out.append("| of those, object arrays | **%d** |" % tot["object_arrays"])
    out.append("| object references inside them | **%d** |" % tot["object_elements"])
    out.append("| arrays refused a shape | %d (%d struct arrays holding %d records) |"
               % (tot["arrays_refused"], tot["struct_arrays"], tot["struct_array_records"]))
    out.append("| struct-typed properties | %d |" % tot["struct_props"])
    out.append("| struct properties decoded to numbers | **%d** |" % tot["struct_decoded"])
    out.append("| struct property names | %d |" % len(srows))
    out.append("")
    out.append("| struct | properties | decoded | sizes (bytes:instances) |")
    out.append("|---|---:|---:|---|")
    for r in srows[:12]:
        out.append("| `%s` | %s | %s | %s |" % (r["struct"], r["properties"], r["decoded"],
                                                r["sizes"].replace(";", " / ")))
    return "\n".join(out)


def write_doc(doc, block, check_only=False):
    start = "<!-- generated:t3d-values -->"
    end = "<!-- /generated:t3d-values -->"
    if not os.path.exists(doc):
        print("FAIL %s: no such page" % doc)
        return 1
    with open(doc, encoding="utf-8") as fh:
        text = fh.read()
    if start not in text or end not in text:
        print("FAIL %s: no %s markers" % (doc, start))
        return 1
    head, rest = text.split(start, 1)
    _old, tail = rest.split(end, 1)
    new = head + start + "\n" + block + "\n" + end + tail
    if check_only:
        if new != text:
            print("FAIL %s: the generated block is stale (run --write-doc)" % doc)
            return 1
        print("ok   %s: the t3d-values block is on the artifacts" % doc)
        return 0
    with open(doc, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("wrote %s" % doc)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="a .unr file or a directory of them")
    ap.add_argument("--out", help="$WORK/extracted/maps (never the repository)")
    ap.add_argument("--limit", type=int, help="first N maps only")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--stats", metavar="CSV",
                    help="write one row per map (map, actors, properties, unresolved refs, bytes); counts only, "
                         "so it may live in inventory/")
    ap.add_argument("--values", metavar="CSV", default=VALUES,
                    help="per-map array/struct census (default: inventory/t3d_values.csv)")
    ap.add_argument("--structs", metavar="CSV", default=STRUCTS,
                    help="per-struct census (default: inventory/t3d_structs.csv)")
    ap.add_argument("--check", action="store_true",
                    help="assert the two censuses agree with each other and drive the reader (no dump)")
    ap.add_argument("--doc", default=os.path.normpath(os.path.join(HERE, "..", "docs", "T3D.md")),
                    help="the page whose generated block this renders (default docs/T3D.md)")
    ap.add_argument("--write-doc", action="store_true", help="render the block into --doc")
    ap.add_argument("--check-doc", action="store_true", help="assert --doc's block equals the artifacts'")
    args = ap.parse_args()

    if args.check_doc:
        return write_doc(args.doc, render_doc(args.values, args.structs), check_only=True)
    if args.write_doc:
        return write_doc(args.doc, render_doc(args.values, args.structs))
    if args.check:
        return check(args.values, args.structs, args.stats)

    if not args.path:
        sys.exit("a .unr file or a directory is required to write T3D")
    if not args.out:
        sys.exit("--out is required to write T3D")
    if "/work/" not in args.out and not args.out.startswith(os.path.expanduser("~/sg1/work")):
        print("note: writing into %s — this is game content, keep it under $WORK" % args.out)

    ps = load_prop_stream()
    paths = ([os.path.join(args.path, f) for f in sorted(os.listdir(args.path)) if f.lower().endswith(".unr")]
             if os.path.isdir(args.path) else [args.path])
    if args.limit:
        paths = paths[: args.limit]

    total = {"actors": 0, "bytes": 0, "props": 0, "unresolved": 0}
    rows, vrows, structs = [], [], {}
    for path in paths:
        got = drive_map(path, args.out, ps, args.verbose)
        for key in total:
            total[key] += got[key]
        rows.append((got["map"], got["actors"], got["props"], got["unresolved"], got["bytes"]))
        v = got["values"]
        vrows.append((got["map"], v["arrays"], v["object_arrays"], v["object_elements"],
                      v["struct_arrays"] + v["unshaped"], v["struct_arrays"], v["struct_records"],
                      v["struct_props"], v["struct_decoded"], v["struct_raw"]))
        for sn, row in got["structs"].items():
            agg = structs.setdefault(sn, {"n": 0, "sizes": {}, "decoded": 0})
            agg["n"] += row["n"]
            agg["decoded"] += row["decoded"]
            for size, n in row["sizes"].items():
                agg["sizes"][size] = agg["sizes"].get(size, 0) + n
        print("%-22s actors=%-6d props=%-6d unresolved=%-4d arrays=%-5d obj=%-5d elems=%-6d struct=%-5d"
              % (got["map"], got["actors"], got["props"], got["unresolved"], v["arrays"], v["object_arrays"],
                 v["object_elements"], v["struct_props"]))
    if args.stats:
        with open(args.stats, "w", encoding="utf-8", newline="") as fh:
            fh.write("map,actors,properties,unresolved_refs,t3d_bytes\n")
            for row in rows:
                fh.write("%s,%d,%d,%d,%d\n" % row)
        print("stats: %s (%d rows)" % (args.stats, len(rows)))
    with open(args.values, "w", encoding="utf-8", newline="") as fh:
        fh.write("map,arrays,object_arrays,object_elements,arrays_refused,struct_arrays,"
                 "struct_array_records,struct_props,struct_decoded,struct_raw\n")
        for r in vrows:
            fh.write("%s,%d,%d,%d,%d,%d,%d,%d,%d,%d\n" % r)
    print("values: %s (%d rows)" % (args.values, len(vrows)))
    with open(args.structs, "w", encoding="utf-8", newline="") as fh:
        fh.write("struct,properties,decoded,sizes\n")
        for sn in sorted(structs, key=lambda k: -structs[k]["n"]):
            row = structs[sn]
            sizes = ";".join("%d:%d" % (s, n) for s, n in sorted(row["sizes"].items(), key=lambda kv: -kv[1]))
            fh.write("%s,%d,%d,%s\n" % (sn, row["n"], row["decoded"], sizes))
    print("structs: %s (%d rows)" % (args.structs, len(structs)))
    print("\nmaps: %d, actors: %d, properties: %d, unresolved refs: %d, t3d bytes: %d, out: %s"
          % (len(paths), total["actors"], total["props"], total["unresolved"], total["bytes"], args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
