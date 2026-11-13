#!/usr/bin/env python3
"""Where a class-level `delegate` tag sits, and whether its value is a run of class-record units (U18).

`tools/delegate-defaults.py` swept the 42 class-level delegate values: 39 tile exactly as two compact
indices (`{object reference, function name}`) and three do not -- `GUI.MenuItemOverlay` (110 bytes) and
`StargateMenus.ExtrasPage`/`CreditsPage` (12 bytes each). That tool reports the value's shape and stops
at the tag, so the open question it left is about the *tag's position*: is the tag the first property of
a correctly located block, or does the payload have a second block start that also closes on its last
byte and puts the same property somewhere else?

This tool measures that without guessing at a layout:

- `tag_offset`  -- the byte offset of the property's tag inside the located block (0 = the tag is the
  block's first property, which is the only case where a different block start moves it);
- `records` / `value_consumed` -- how many whole **[compact object reference][dword][dword]** units tile
  the value region. That is the unit `FUN_0003d900` streams (U17: a 12-byte record, 9 bytes on disk when
  the reference is one byte), so a value that is 2 or 3 of them is the class record's own array and not a
  value at all;
- `alt_start` / `alt_gap` / `alt_units` / `alt_first` -- for a tag at offset 0, the earliest later start
  whose block also parses cleanly, holds properties and closes on the payload's last byte, plus the gap
  from the located start measured in whole record units.

Rows are printed as types, widths and offsets; the only names are the class, its property and the
alternate block's first property name, which the inventory CSVs already carry.

    python3 tools/defaults-blockstart.py DUMP/System
    python3 tools/defaults-blockstart.py DUMP/System --csv inventory/delegate_blockstart.csv
    python3 tools/defaults-blockstart.py --check          # synthetic self-test, no dump, writes nothing
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

SUPERS = {}         # class -> superclass, filled once per sweep (cd.super_map)
RECORD = 9          # a streamed record: one compact object reference plus two dwords, 9 bytes at width 1
MAX_ALT = 160       # do not walk an alternative start further than this many bytes past the located one


def load_tool(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cd = load_tool("class_defaults", "class-defaults.py")
de = load_tool("defaults_emit", "defaults-emit.py")
dd = load_tool("delegate_defaults", "delegate-defaults.py")
ps = cd.load_prop_stream()

COLUMNS = ["package", "class", "property", "tag_type", "type_byte", "size", "indices", "records",
           "value_consumed", "block_start", "tag_offset", "block_props", "alt_start", "alt_gap",
           "alt_units", "alt_first", "alt_props", "alt_size", "alt_indices", "alt_object_self",
           "alt_handler", "alt_handler_declared", "note"]


def walk(buf, pkg):
    """[(tag offset, name, type byte, size, value offset)] for the block at the start of `buf`."""
    off, out = 0, []
    while off < len(buf) and len(out) < 4000:
        tag = off
        try:
            name, off = ps.read_index(buf, off)
        except IndexError:
            break
        if name == 0 or pkg.name(name) == "None":
            break
        try:
            entry, off = ps.read_value(buf, off, pkg, False)
        except IndexError:
            break
        out.append((tag, pkg.name(name), entry["type_byte"], entry["size"], entry["value_offset"]))
        if off > len(buf):
            break
    return out


def records(buf, start, size):
    """(whole record units tiling the value region, bytes they consumed)."""
    off, n, end = start, 0, start + size
    while off < end:
        try:
            _v, nxt = ps.read_index(buf, off)
        except IndexError:
            break
        width = nxt - off
        if width <= 0 or off + width + 8 > end:
            break
        off += width + 8
        n += 1
    return n, off - start


def alt_start(data, pkg, start, limit=MAX_ALT):
    """(start, props, first name) for the earliest later start that also parses clean to the payload end."""
    for s in range(start + 1, min(len(data), start + 1 + limit)):
        try:
            props, end, reason = ps.read_block(data[s:], pkg, False)
        except Exception:                               # noqa: BLE001 - a bad candidate is a skip
            continue
        if reason == "None" and props and s + end == len(data):
            return s, len(props), props[0]["name"]
    return None


def row_for(pkg, pkg_name, cls, tag_off, name, info, size, value_off, buf, data, start, nprops,
            n_idx, want_alt):
    n_rec, consumed = records(buf, value_off, size)
    row = {
        "package": pkg_name, "class": cls, "property": name,
        "tag_type": (dd.TYPES[info & 0x0f] if (info & 0x0f) < len(dd.TYPES) else "?"),
        "type_byte": "0x%02x" % info, "size": size, "indices": n_idx, "records": n_rec,
        "value_consumed": consumed, "block_start": start, "tag_offset": tag_off,
        "block_props": nprops, "alt_start": "", "alt_gap": "", "alt_units": "", "alt_first": "",
        "alt_props": "", "alt_size": "", "alt_indices": "", "alt_object_self": "", "alt_handler": "",
        "alt_handler_declared": "", "note": "",
    }
    notes = []
    if n_rec >= 2:
        notes.append("value tiles as record units")
    if size and not n_idx:
        notes.append("no {object, name} tiling")
    row["note"] = "; ".join(notes)
    if want_alt:
        alt = alt_start(data, pkg, start)
        if alt is not None:
            gap = alt[0] - start
            row["alt_start"] = alt[0]
            row["alt_gap"] = gap
            row["alt_units"] = gap // RECORD if gap % RECORD == 0 else ""
            row["alt_first"] = alt[2]
            row["alt_props"] = alt[1]
            abuf = data[alt[0]:]
            for tag_off, name2, info2, size2, voff2 in walk(abuf, pkg):
                if name2 != name:
                    continue
                idx = dd.tile(abuf, voff2, size2)
                row["alt_size"] = size2
                row["alt_indices"] = len(idx)
                if len(idx) == 2:
                    kind, text = dd.object_of(pkg, idx[0])
                    row["alt_object_self"] = "yes" if text.endswith("'%s'" % cls) else "no"
                    try:
                        handler = pkg.name(idx[1])
                    except Exception:                   # noqa: BLE001
                        handler = ""
                    row["alt_handler"] = handler
                    fns = dd.functions_of(pkg, None, cls, SUPERS)
                    row["alt_handler_declared"] = "yes" if handler and handler in fns else "no"
                break
    return row


def sweep(system_dir, pkgs):
    global SUPERS
    supers = cd.super_map()
    SUPERS = supers
    declared_all = cd.declared_properties()
    props_meta = de.property_meta()
    rows = []
    for pkg_name in pkgs:
        path = os.path.join(system_dir, pkg_name + ".u")
        if not os.path.isfile(path):
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            got, _why = de.block_of(pkg, cls, supers, declared_all, want_score=True)
            if got is None:
                continue
            _props, start, _clean = got
            meta_of = de.prop_meta_for(cls, supers, props_meta)
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            buf = data[start:]
            walked = walk(buf, pkg)
            for tag_off, name, info, size, value_off in walked:
                meta = meta_of(name)
                if not dd.is_delegate(name, (meta[1] if meta else "")):
                    continue
                n_idx = len(dd.tile(buf, value_off, size))
                rows.append(row_for(pkg, pkg_name, cls, tag_off, name, info, size, value_off, buf,
                                    data, start, len(walked), n_idx, tag_off == 0))
    return rows


def emit(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLUMNS})


def report(rows):
    print("%-14s %-24s %-32s %6s %4s %4s %5s %5s %6s %5s %s"
          % ("package", "class", "property", "size", "idx", "recs", "used", "tag@", "alt_gap",
             "units", "alt_first"))
    for r in rows:
        print("%-14s %-24s %-32s %6d %4d %4d %5d %5d %6s %5s %s"
              % (r["package"], r["class"][:24], r["property"][:32], r["size"], r["indices"],
                 r["records"], r["value_consumed"], r["tag_offset"], r["alt_gap"], r["alt_units"],
                 r["alt_first"][:26]))
    print()
    first = [r for r in rows if r["tag_offset"] == 0]
    with_alt = [r for r in first if r["alt_start"] != ""]
    units = [r for r in with_alt if r["alt_units"] != ""]
    recs = [r for r in rows if r["records"] >= 2]
    reread = [r for r in with_alt if r["alt_indices"] == 2 and r["alt_object_self"] == "yes"
              and r["alt_handler_declared"] == "yes"]
    print("%d delegate value(s): %d are their block's first property, %d of those have a second clean "
          "block start (%d of them a whole number of record units away); %d value(s) tile as two or more "
          "record units; %d re-read at that start as {object, name} binding the class's own declared "
          "function" % (len(rows), len(first), len(with_alt), len(units), len(recs), len(reread)))


def self_test():
    """Synthetic bodies only: `--check` answers without the dump and writes nothing."""
    class Names:
        NAMES = ["None", "Probe", "Onto", "Ghost"]

        def name(self, i):
            return self.NAMES[i] if 0 <= i < len(self.NAMES) else "?"

    pkg = Names()
    # a one-property block: name 1, Bool tag (0x5b: type 11, size class 1)
    blk = bytes([0x01, 0x5b, 0x00, 0x00])
    got = walk(blk, pkg)
    assert got and got[0][0] == 0 and got[0][1] == "Probe", "the tag offset is the block's first byte"
    # two whole record units: ref 2 + two dwords each
    assert records(bytes([0x02] + [0] * 8 + [0x02] + [0] * 8), 0, 18) == (2, 18), \
        "two 9-byte units tile 18 bytes"
    assert records(bytes([0x02] + [0] * 8 + [0x02, 0, 0]), 0, 12) == (1, 9), \
        "a truncated second unit is not counted"
    assert records(bytes([0x02, 0x0f]), 0, 2) == (0, 0), "a two-byte value holds no record unit"
    # an alternative start: one byte of record, then a block that closes on the payload end
    data = bytes([0x00, 0x03, 0x5b, 0x00, 0x00])
    alt = alt_start(data, pkg, 0)
    assert alt is not None and alt[0] == 1 and alt[2] == "Ghost", "the later clean start is found"
    print("self-test: ok (5 synthetic cases)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dump", nargs="?", help="DUMP/System directory")
    ap.add_argument("--pkg", action="append", default=[], help="restrict to one package (repeatable)")
    ap.add_argument("--csv", help="write the rows here")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump needed")
    args = ap.parse_args()
    if args.check:
        return self_test()
    if not args.dump:
        ap.error("a DUMP/System directory is required unless --check is given")
    pkgs = args.pkg or sorted(f[:-2] for f in os.listdir(args.dump) if f.endswith(".u"))
    rows = sweep(args.dump, pkgs)
    report(rows)
    if args.csv:
        emit(rows, args.csv)
        print("wrote %s" % args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
