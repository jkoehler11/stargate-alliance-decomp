#!/usr/bin/env python3
"""Which classes actually ship a delegate default, and what a delegate tag looks like (PLAN 3.5, U18).

`tools/delegate-defaults.py` reads the delegate values the located blocks carry -- 42 of them, of which 39
tile as `{object reference, function name}`. U18's two menu pages (`StargateMenus.ExtrasPage`,
`StargateMenus.CreditsPage`) do not, and the writer has no third field to offer, so the entry left the
question to a *second route*: a package from the same build that ships the same property with a value short
enough to read directly. This tool measures whether that route exists, and reads the residue's tag against
the corpus instead of against the delegate grammar.

Three populations, one artifact (`kind` column):

* `value` -- every property in a **located** block whose name ends `__Delegate` or whose declared type is
  `delegate<...>`, with its tag byte, size and index tiling. This is the population the second route would
  have to come from, and it is the whole of it: 5,533 declared slots exist (a class inherits its ancestors'
  delegate properties), 42 of them ship a value, and the other 5,052 are inherited-but-unset.
* `tag15` -- every property tag in every located block whose type nibble is 15. UELib's tag map calls type
  15 `FixedArray`; this build writes it **three** times in 16,012 tags, and one of the three is
  `Engine.TriggeredProjector.DrawType`, an enum byte property (`UByteProperty`) that cannot carry the 12
  bytes the tag's size class claims. That row is the control: the type-15 shape is a tag-stream mis-split,
  not a delegate type, and the two menu pages are its only other instances.
* `census` -- the run's totals, read by the doc renderer.

    python3 tools/delegate-value-census.py DUMP/System
    python3 tools/delegate-value-census.py DUMP/System --csv inventory/delegate_value_census.csv
    python3 tools/delegate-value-census.py --check        # synthetic self-test, no dump, writes nothing

Rows never carry a value's bytes, only its shape and resolution.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
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

# UELib's tag map, 0-15. The build's *delegate* tags land on type 7 (which the map calls `String`); the
# value is an object reference then an FName, so the name is the map's, not the property's.
TYPES = ["Invalid", "Byte", "Int", "Bool", "Float", "Object", "Name", "String", "Class", "Array",
         "Struct", "Vector", "Rotator", "Str", "Map", "FixedArray"]
DELEGATE_TYPE = 7

COLUMNS = ["kind", "package", "class", "property", "declared", "property_kind", "tag_type", "type_byte",
           "size", "array_index", "indices", "object_kind", "object_self", "handler", "handler_declared",
           "verdict", "note"]


def is_delegate(name, declared):
    """A delegate property: the class's own emitted marker, or the declared type from the inventory."""
    return (name or "").endswith("__Delegate") or (declared or "").startswith("delegate<")


def tile(buf, start, size, limit=64):
    """Compact indices that tile [start, start+size) exactly, or [] when the region does not tile."""
    off, out = start, []
    end = start + size
    while off < end and len(out) < limit:
        try:
            value, nxt = ps.read_index(buf, off)
        except IndexError:
            return []
        if nxt <= off or nxt > end:
            return []
        out.append(value)
        off = nxt
    return out if off == end else []


def classify(indices, object_kind, object_self, handler, handler_declared):
    """The verdict for a value row, from its tiling and the resolution of its two indices.

    Kept pure so `--check` can drive it: the two menu pages' residues tile as ten indices, and a value that
    tiles as two still has to resolve -- the first index must be a class export and the second a function
    the class declares -- or the pair is not a delegate binding.
    """
    if len(indices) != 2:
        return "not-two-indices"
    if object_kind != "export":
        return "object-is-not-an-export"
    if object_self != "yes":
        return "object-is-not-the-class"
    if handler_declared != "yes":
        return "handler-not-declared"
    return "object-name"


def declared_for(cls, supers, props_meta):
    """property -> (kind, declared type) for the class and its ancestors in this package."""
    out, cur, seen = {}, cls, set()
    while cur and cur not in seen and len(seen) < 16:
        seen.add(cur)
        for prop, meta in props_meta.get(cur, {}).items():
            out.setdefault(prop, meta)
        cur = supers.get(cur)
    return out


def object_of(pkg, idx):
    """(kind, text) for a signed object index: negatives index imports, positives exports, 0 is Class."""
    if idx < 0:
        i = -idx - 1
        if 0 <= i < len(pkg.imports):
            return "import", ""
        return "import?", ""
    if idx > 0 and idx - 1 < len(pkg.exports):
        return "export", ""
    return ("class", "") if idx == 0 else ("export?", "")


def functions_of(pkg, exp, cls, supers):
    """Function names the class or an ancestor declares in its own ScriptText."""
    pst = load_tool("pst", "pkg-script-text.py")
    out, cur, seen = set(), cls, set()
    by_name = {}
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class":
            by_name[pkg.name(e["name"])] = e
    while cur and cur not in seen and len(seen) < 12:
        seen.add(cur)
        e = by_name.get(cur)
        if e is None:
            break
        try:
            text = pst.script_text(pkg, e) or ""
        except Exception:                                # noqa: BLE001 - a class with no text is a negative
            text = ""
        out |= {m.group(1) for m in (pst.FUNC_START.match(line) for line in text.splitlines()) if m}
        cur = supers.get(cur)
    return out


def value_row(pkg, pkg_name, cls, p, decl, buf, decl_fns):
    idx = tile(buf, p["value_offset"], p["size"])
    info = p["type_byte"]
    row = dict(kind="value", package=pkg_name, cls=cls, property=p["name"],
               declared=decl or p["type"], property_kind="", tag_type=TYPES[info & 0x0f],
               type_byte="0x%02x" % info, size=p["size"], array_index=p.get("array_index") or 0,
               indices=len(idx), object_kind="", object_self="", handler="", handler_declared="", note="")
    if idx:
        kind, _text = object_of(pkg, idx[0])
        row["object_kind"] = kind
        if kind == "export":
            ex = pkg.exports[idx[0] - 1]
            row["object_self"] = "yes" if pkg.name(ex["name"]) == cls else "no"
    if len(idx) >= 2:
        try:
            row["handler"] = pkg.name(idx[1])
        except Exception:                                # noqa: BLE001 - an index outside the name table
            row["handler"] = ""
        row["handler_declared"] = "yes" if row["handler"] and row["handler"] in decl_fns else "no"
    row["verdict"] = classify(idx, row["object_kind"], row["object_self"], row["handler"],
                              row["handler_declared"])
    if row["verdict"] == "not-two-indices":
        row["note"] = "indices tile as %d, and a delegate value is a pair" % len(idx)
    elif row["verdict"] == "object-is-not-an-export":
        row["note"] = "first index is not an object reference into the export table"
    return row


def start_verdict(name, own):
    """`own-first` when the class itself declares the property a later start names, else `inherited-first`."""
    return "own-first" if name in own else "inherited-first"


def residue_starts(pkg, pkg_name, cls, data, start, p, decl, own):
    """Starts inside a residue's value that read a whole block to the payload's end.

    The located block's first tag on these rows is a type-0 or a type-15 tag whose size class claims 12 or 110
    bytes. The corpus says what such a tag is worth: three instances of type 15, one of them on a property that
    cannot hold the value (`tag15` rows). So every start *after* that tag inside its span is tried and kept when
    its block ends on the payload's end and names only properties the class declares -- a real tag inside the
    span is what the block names, and `size` here is the start's offset from the located one.
    """
    out, end_of_payload = [], len(data)
    lo = start + p["value_offset"] + 1
    for s in range(lo, start + p["value_offset"] + p["size"]):
        try:
            props, end, reason = ps.read_block(data[s:], pkg, True)
        except Exception:                                # noqa: BLE001 - a short read at the tail is a skip
            continue
        if reason != "None" or not props or s + end != end_of_payload:
            continue
        if any(q["name"] not in decl and q["name"] != "None" for q in props):
            continue
        first = props[0]
        row = dict(kind="residue-start", package=pkg_name, cls=cls, property=first["name"],
                   declared=(decl.get(first["name"]) or ("", ""))[1],
                   property_kind=(decl.get(first["name"]) or ("", ""))[0], tag_type="",
                   type_byte="0x%02x" % first["type_byte"], size=s - start, array_index="",
                   indices=len(props), object_kind="", object_self="", handler="", handler_declared="",
                   verdict=start_verdict(first["name"], own), note="")
        if first["type"] == "Object":
            value, _nxt = ps.read_index(data[s:], first["value_offset"])
            if 0 < value <= len(pkg.exports):
                ex = pkg.exports[value - 1]
                row["object_kind"] = "export"
                row["handler"] = pkg.name(ex["name"])
                row["note"] = "%s'%s'" % (pkg.class_name_of(ex), row["handler"])
        out.append(row)
    return out


def tag15_row(pkg_name, cls, p, kind):
    info = p["type_byte"]
    return dict(kind="tag15", package=pkg_name, cls=cls, property=p["name"], declared="",
                property_kind=kind, tag_type=TYPES[info & 0x0f], type_byte="0x%02x" % info,
                size=p["size"], array_index=p.get("array_index") or 0, indices="", verdict="", note="")


def sweep(system_dir, pkgs):
    supers = cd.super_map()
    declared_all = cd.declared_properties()
    props_meta = de.property_meta()
    rows, tally = [], collections.Counter()
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
            decl_map = declared_for(cls, supers, props_meta)
            slots = {k: v for k, v in decl_map.items() if is_delegate(k, v[1])}
            tally["classes"] += 1
            tally["declared_slots"] += len(slots)
            got, _why = de.block_of(pkg, cls, supers, declared_all, want_score=True)
            if got is None:
                tally["slots_without_a_block"] += len(slots)
                tally["classes_without_a_block"] += 1 if slots else 0
                continue
            props, start, _clean = got
            tally["blocks"] += 1
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            buf = data[start:]
            own = declared_all.get(cls, set())
            tally["tags"] += len(props)
            seen = set()
            decl_fns = None
            for p in props:
                info = p["type_byte"]
                kind = (decl_map.get(p["name"]) or ("", ""))[0]
                if info & 0x0f == 15:
                    rows.append(tag15_row(pkg_name, cls, p, kind))
                    tally["tag15_tags"] += 1
                if not is_delegate(p["name"], (decl_map.get(p["name"]) or ("", ""))[1]):
                    continue
                seen.add(p["name"])
                if decl_fns is None:
                    decl_fns = functions_of(pkg, exp, cls, supers)
                rows.append(value_row(pkg, pkg_name, cls, p, (decl_map.get(p["name"]) or ("", ""))[1],
                                      buf, decl_fns))
                tally["delegate_tags"] += 1
                if rows[-1]["verdict"] != "object-name":
                    extra = residue_starts(pkg, pkg_name, cls, data, start, p, decl_map, own)
                    rows.extend(extra)
                    tally["residue_starts"] += len(extra)
                    tally["residue_own_first"] += sum(1 for r in extra if r["verdict"] == "own-first")
            tally["slots_shipped"] += len(seen & set(slots))
            tally["slots_inherited_unset"] += len(set(slots) - seen)
    for row in rows:
        if row["kind"] == "value":
            tally["value_" + row["verdict"]] += 1
    for key in ("classes", "blocks", "declared_slots", "slots_shipped", "slots_inherited_unset",
                "slots_without_a_block", "classes_without_a_block", "tags", "delegate_tags",
                "tag15_tags", "value_object-name", "value_not-two-indices", "value_object-is-not-an-export",
                "residue_starts", "residue_own_first"):
        rows.append(dict(kind="census", package="", cls="", property=key, declared="", property_kind="",
                         tag_type="", type_byte="", size=tally[key], array_index="", indices="",
                         verdict="", note=""))
    return rows, tally


def emit(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            r["class"] = r.get("cls", "")
            w.writerow({k: r.get(k, "") for k in COLUMNS})


def report(rows, tally):
    print("declared delegate slots: %d over %d classes (%d ship a value, %d are inherited but unset, "
          "%d have no located block)" % (tally["declared_slots"], tally["classes"], tally["slots_shipped"],
                                        tally["slots_inherited_unset"], tally["slots_without_a_block"]))
    print("blocks: %d\ntags in located blocks: %d (%d delegate, %d of type 15)"
          % (tally["blocks"], tally["tags"], tally["delegate_tags"], tally["tag15_tags"]))
    print()
    head = "%-14s %-26s %-32s %-10s %4s %3s %-8s %-26s %s" % ("package", "class", "property", "tag", "size",
                                                             "idx", "verdict", "object", "handler")
    print(head)
    print("-" * len(head))
    for r in rows:
        if r["kind"] != "value":
            continue
        if r["verdict"] == "object-name" and r["tag_type"] == "String":
            continue
        print("%-14s %-26s %-32s %-10s %4s %3s %-8s %-26s %s"
              % (r["package"], r["cls"][:26], r["property"][:32], r["tag_type"], r["size"], r["indices"],
                 r["verdict"], (r["object_kind"] or "-")[:26], r["handler"]))
    print()
    print("residue starts (a later start inside the value that reads a whole block):")
    for r in rows:
        if r["kind"] != "residue-start":
            continue
        print("   %-14s %-26s +%-4s %-32s %-16s %2s props  %s %s"
              % (r["package"], r["cls"][:26], r["size"], r["property"][:32], r["verdict"], r["indices"],
                 r["handler"], r["note"]))
    print()
    print("type-15 tags (every one in the corpus):")
    for r in rows:
        if r["kind"] != "tag15":
            continue
        print("   %-14s %-26s %-32s %-10s %4s arr=%-3s property kind %s"
              % (r["package"], r["cls"][:26], r["property"][:32], r["tag_type"], r["size"],
                 r["array_index"], r["property_kind"] or "-"))


def self_test():
    """Synthetic bytes and shapes only: `--check` answers without the dump and writes nothing."""
    assert is_delegate("__OnXControllerEvent__Delegate", ""), "the emitted marker is a delegate"
    assert is_delegate("OnXControllerEvent", "delegate<OnXControllerEvent>"), \
        "the declared type is a delegate without the marker"
    assert not is_delegate("DrawType", "enum EDrawType"), "an enum is not a delegate"
    assert tile(bytes([0x02, 0x0f]), 0, 2) == [2, 15], "two one-byte indices tile two bytes"
    assert tile(bytes([0x6b, 0x01, 0x0f]), 0, 3) == [107, 15], "bit 6 continues an index"
    assert tile(bytes([0x02, 0x0f, 0x41]), 0, 3) == [], "a continuation past the end does not tile"
    assert tile(bytes([0x03, 0x07, 0x08, 0x06, 0x90, 0x0a, 0x00, 0x67, 0x03, 0x15, 0x6a, 0x19]), 0, 12) \
        == [3, 7, 8, 6, -16, 10, 0, 231, 21, 1642], "the residue's twelve bytes tile as ten indices"
    assert classify([2, 3], "export", "yes", "InternalOnKeyEvent", "yes") == "object-name"
    assert classify([3, 7, 8, 6, -16, 10, 0, 231, 21, 1642], "export", "no", "", "no") == "not-two-indices"
    assert classify([2, 3], "import", "no", "x", "yes") == "object-is-not-an-export"
    assert classify([2, 3], "export", "no", "x", "yes") == "object-is-not-the-class"
    assert classify([2, 3], "export", "yes", "x", "no") == "handler-not-declared"
    assert start_verdict("TempLB", {"TempLB"}) == "own-first", "a class's own declaration is the own-first start"
    assert start_verdict("WinLeft", {"TempLB"}) == "inherited-first", "an ancestor's property is not"
    print("self-test: ok (13 synthetic cases)")
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
    rows, tally = sweep(args.dump, pkgs)
    report(rows, tally)
    if args.csv:
        emit(rows, args.csv)
        print("wrote %s" % args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
