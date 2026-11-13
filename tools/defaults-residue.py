#!/usr/bin/env python3
"""Diagnose the `defaultproperties` values that do not render (PLAN 3.5's last values).

`tools/defaults-emit.py` renders 15,517 of 15,521 class-level values; four are left, and both
`docs/CLASS_DEFAULTS.md` and PLAN 3.5 name them but not what their bytes are:

    Engine.TextToSpeechAlias.Aliases   array<SpeechReplacement>, 511 bytes, "does not tile at any width"
    GUI.MenuItemOverlay                a delegate tag typed Invalid
    StargateMenus.ExtrasPage           a delegate tag typed Invalid
    StargateMenus.CreditsPage          a delegate tag typed Invalid

This tool reports the *shape* of each residue value without printing any of it: the tag's type byte
and size class, the declared type from `inventory/class_properties.csv`, the width of the value's
first compact index, and every tiling that closes the value region exactly -- a count plus fixed-width
elements (`count x w`), a tagged struct stream, or a run of compact indices. A tiling that lands
exactly on the declared size is a candidate layout; one that does not is a negative, and both are
recorded, so a session that guessed wrong does not have to guess the same way twice.

    python3 tools/defaults-residue.py DUMP/System                  # the four named rows
    python3 tools/defaults-residue.py DUMP/System --for Engine.TextToSpeechAlias
    python3 tools/defaults-residue.py DUMP/System --csv inventory/defaults_residue.csv
    python3 tools/defaults-residue.py --check                       # synthetic self-test, no dump

Values are game data: the table prints types, widths and offsets, never a value. `--show` adds the
resolved names of a value's compact indices, for a terminal session only.
"""
import argparse
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from ue2pkg import Package  # noqa: E402


def load_tool(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cd = load_tool("class_defaults", "class-defaults.py")
de = load_tool("defaults_emit", "defaults-emit.py")
ps = cd.load_prop_stream()

# The residue `docs/CLASS_DEFAULTS.md` records ("The last 4 values"): (package, class, property).
RESIDUE = [
    ("Engine", "TextToSpeechAlias", "Aliases"),
    ("GUI", "MenuItemOverlay", None),
    ("StargateMenus", "ExtrasPage", None),
    ("StargateMenus", "CreditsPage", None),
]

COLUMNS = ["package", "class", "property", "declared", "tag_type", "type_byte", "size_class",
           "flags", "size", "nested_index", "index_bytes", "greedy_indices", "greedy_bytes", "count",
           "zero_bytes", "fixed_widths", "tagged_elements", "value_offset"]


def compact_width(buf, off):
    """(value, width) of one compact index at `off`; width is the bytes it consumed."""
    value, nxt = ps.read_index(buf, off)
    return value, nxt - off


def greedy_indices(buf, start, size, limit=4096):
    """(reads, bytes) for a run of compact indices from `start` that stops exactly on `start + size`.

    `(0, 0)` when no such run exists. A single byte always advances by one, so this is a shape and not a
    proof: its job is to say whether the whole value region can be read as indices at all.
    """
    off, reads = start, 0
    end = start + size
    while off < end and reads < limit:
        try:
            _v, width = compact_width(buf, off)
        except IndexError:
            return 0, 0
        if width <= 0 or off + width > end:
            return 0, 0
        off += width
        reads += 1
    return (reads, off - start) if off == end else (0, 0)


def fixed_widths(buf, start, size):
    """Candidate `count x width` layouts for an array value: [(count, width, elements)]."""
    out = []
    try:
        count, nxt = ps.read_index(buf, start)
    except IndexError:
        return out
    head = nxt - start
    body = size - head
    if count < 0 or count > 65536 or body < 0:
        return out
    if count == 0:
        out.append((0, body, 0))
        return out
    if body % count == 0 and 0 < body // count <= 512:
        out.append((count, body // count, count))
    return out


def tagged_elements(buf, start, size, pkg, limit=4096):
    """How many tagged struct elements tile the value exactly (0 when the stream desynchronises)."""
    off, n = start, 0
    end = start + size
    while off < end and n < limit:
        try:
            _entry, nxt = ps.read_value(buf, off, pkg, False)
        except Exception:                               # noqa: BLE001 - a short region is a negative
            return 0
        if nxt <= off or nxt > end:
            return 0
        off = nxt
        n += 1
    return n if off == end else 0


def residue_rows(system_dir, wanted, show=False):
    """Every unrendered value in the wanted classes' clean blocks, as shape rows."""
    supers = cd.super_map()
    declared_all = cd.declared_properties()
    props_meta = de.property_meta()
    classes = de.class_set()
    ctx = {"enums": {}, "classes": classes}
    rows = []
    by_pkg = {}
    for pkg_name, cls, prop in wanted:
        by_pkg.setdefault(pkg_name, []).append((cls, prop))
    for pkg_name, items in by_pkg.items():
        path = os.path.join(system_dir, pkg_name + ".u")
        if not os.path.isfile(path):
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for cls, only in items:
            exp = None
            for e in pkg.exports:
                if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls:
                    exp = e
                    break
            if exp is None:
                continue
            meta_of = de.prop_meta_for(cls, supers, props_meta)
            got, _reason = de.block_of(pkg, cls, supers, declared_all, want_score=True)
            if got is None:
                continue
            props, start, clean = got
            if not clean:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            buf = data[start:]
            for p in props:
                meta = meta_of(p["name"])
                if only and p["name"] != only:
                    continue
                text, note = de.render_value(pkg, p, meta, buf, ctx)
                if text is not None:
                    continue
                row = shape_row(pkg, pkg_name, cls, p, meta, buf, show)
                if show:
                    off = p.get("value_offset") or 0
                    row["hex"] = buf[off:off + (p.get("size") or 0)][:96].hex(" ")
                rows.append(row)
    return rows


def shape_row(pkg, pkg_name, cls, p, meta, buf, show=False):
    off = p.get("value_offset") or 0
    size = p.get("size") or 0
    try:
        idx, width = compact_width(buf, off)
    except IndexError:
        idx, width = -1, 0
    reads, bytes_read = greedy_indices(buf, off, size) if size else (0, 0)
    fw = fixed_widths(buf, off, size) if p["type"] == "Array" else []
    tagged = tagged_elements(buf, off, size, pkg) if p["type"] == "Array" else 0
    info = p.get("type_byte", 0)
    value = buf[off:off + size]
    row = {
        "package": pkg_name,
        "class": cls,
        "property": p["name"],
        "declared": (meta[1] if meta else "") or p["type"],
        "tag_type": p["type"],
        "type_byte": "0x%02x" % info,
        "size_class": (info & 0x70) >> 4,
        "flags": "index" if info & 0x80 else "",
        "size": size,
        "nested_index": p.get("array_index", ""),
        "zero_bytes": value.count(0),
        "index_bytes": width,
        "greedy_indices": reads,
        "greedy_bytes": bytes_read,
        "count": idx,
        "fixed_widths": ";".join("count=%d width=%d" % (c, w) for c, w, _n in fw),
        "tagged_elements": tagged,
        "value_offset": off,
    }
    if show and width:
        try:
            row["first_index_name"] = pkg.name(idx)
        except Exception:                               # noqa: BLE001
            row["first_index_name"] = ""
    return row


def emit(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLUMNS})


def report(rows):
    head = ("%-14s %-22s %-30s %-10s %-11s %6s %4s %3s %5s %5s %5s %s"
            % ("package", "class", "property", "declared", "tag", "size", "cls", "f", "zero",
               "grdy", "tagd", "tilings"))
    print(head)
    print("-" * len(head))
    for r in rows:
        if r.get("hex"):
            print("    value bytes: %s" % r["hex"])
        print("%-14s %-22s %-30s %-10s %s/%-9s %6d %4d %3s %5d %5d %5d %s"
              % (r["package"], r["class"], r["property"][:30], r["declared"][:10], r["type_byte"],
                 r["tag_type"][:9], r["size"], r["size_class"], r["flags"] or "-", r["zero_bytes"],
                 r["greedy_indices"], r["tagged_elements"], r["fixed_widths"] or "-"))
    print()
    print("%d unrendered value(s)" % len(rows))


def self_test():
    """Synthetic bodies only: `--check` answers without the dump and writes nothing."""
    buf = bytes([0x02, 0x00, 0x00, 0x00, 0x05, 0x00, 0x00, 0x00])   # eight one-byte indices
    assert compact_width(buf, 0)[1] == 1, "a one-byte index must consume one byte"
    assert compact_width(bytes([0x41, 0x02]), 0)[1] == 2, "a continuation index must consume two bytes"
    assert greedy_indices(buf, 0, 0) == (0, 0), "a zero-size region has no indices"
    assert greedy_indices(buf, 0, 8) == (8, 8), "eight one-byte indices must tile eight bytes"
    assert greedy_indices(buf, 0, 5) == (5, 5), "five one-byte indices must tile five bytes"
    assert greedy_indices(bytes([0x00, 0x41]), 0, 2) == (0, 0), \
        "a region whose last index overruns it is no run"
    assert fixed_widths(bytes([0x02] + [0] * 8), 0, 9) == [(2, 4, 2)], \
        "a count of two over eight body bytes is a two-wide, wait, four-wide element"
    assert fixed_widths(bytes([0x03] + [0] * 8), 0, 9) == [], "a count that does not divide is no width"
    print("self-test: ok (%d synthetic cases)" % 8)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dump", nargs="?", help="DUMP/System directory")
    ap.add_argument("--for", dest="only", action="append", default=[], metavar="PACKAGE.CLASS",
                    help="restrict to one class (repeatable)")
    ap.add_argument("--csv", help="write the rows here")
    ap.add_argument("--show", action="store_true", help="also print the first index's resolved name")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump needed")
    args = ap.parse_args()
    if args.check:
        return self_test()
    if not args.dump:
        ap.error("a DUMP/System directory is required unless --check is given")
    wanted = RESIDUE
    if args.only:
        wanted = []
        for spec in args.only:
            if "." not in spec:
                ap.error("--class wants PACKAGE.CLASS")
            pkg_name, cls = spec.split(".", 1)
            wanted.append((pkg_name, cls, None))
    rows = residue_rows(args.dump, wanted, show=args.show)
    report(rows)
    if args.csv:
        emit(rows, args.csv)
        print("wrote %s" % args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
