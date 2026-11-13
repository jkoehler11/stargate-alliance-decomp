#!/usr/bin/env python3
"""Decode a `delegate` property's default: `{object reference, function name}` (PLAN 3.5, U18).

`tools/defaults-emit.py` renders every class-level default it can type, and U18 recorded the values it
cannot: three classes whose `__OnXControllerEvent__Delegate` tag carries a type byte that is not a
delegate type. What a delegate's *value* is had never been measured, because the tag grammar has no
delegate entry and the emitted script text only shows the source form (`OnXControllerEvent=<obj>.<fn>`).

Measured here: a UE2 `FScriptDelegate` default is two compact indices -- a **signed object reference**
(UE2 convention: negative indexes the import table, positive the export table, zero is `Class`) and an
**FName index** of the bound function. All nine decodable values in the dump are the class binding a
handler to *itself*: the object index resolves to the class's own export and the name index to a
function that class declares. The value's width follows the object index's width, which is why the same
property is 2 bytes in one class and 3 in another (`0x17` vs `0x57`: same type nibble, different size
class).

Rows report the shape and the resolution, never the value bytes:

    package, class, property, declared, tag_type, type_byte, size_class, size, indices,
    object_kind, object_self, handler, handler_declared, note

    python3 tools/delegate-defaults.py DUMP/System [--pkg GUI ...]
    python3 tools/delegate-defaults.py DUMP/System --csv inventory/delegate_defaults.csv
    python3 tools/delegate-defaults.py --check          # synthetic self-test, no dump, writes nothing

A row whose `indices` is not 2 is a value this layout does not explain; the tag columns are printed
beside it so the residue keeps its own evidence (U18's three classes land there).
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
pst = load_tool("pkg_script_text", "pkg-script-text.py")
ps = cd.load_prop_stream()

# UELib's tag map: 0-15. `type 7` is where a delegate's tag lands on this build, which the renderer
# reads as `String`; the value is not one.
TYPES = ["Invalid", "Byte", "Int", "Bool", "Float", "Object", "Name", "String", "Class", "Array",
         "Struct", "Vector", "Rotator", "Str", "Map", "FixedArray"]

COLUMNS = ["package", "class", "property", "declared", "tag_type", "type_byte", "size_class", "size",
           "indices", "object_kind", "object_self", "handler", "handler_declared", "note"]


def is_delegate(name, declared):
    """A delegate property: the class's own emitted marker, or the declared type from the inventory."""
    return (name or "").endswith("__Delegate") or (declared or "").startswith("delegate<")


def object_of(pkg, idx):
    """(kind, text) for a signed object index: negatives index imports, positives exports, 0 is Class."""
    if idx < 0:
        i = -idx - 1
        if 0 <= i < len(pkg.imports):
            im = pkg.imports[i]
            return "import", "%s.%s'%s'" % (im["class_package"], im["class"], im["name"])
        return "import?", ""
    if idx > 0:
        i = idx - 1
        if 0 <= i < len(pkg.exports):
            ex = pkg.exports[i]
            return "export", "%s'%s'" % (pkg.class_name_of(ex), pkg.name(ex["name"]))
        return "export?", ""
    return "class", "Class"


def functions_of(pkg, exp, cls, supers):
    """Names the class or any ancestor in the same package declares as functions (its ScriptText).

    Read from the package, never a `$WORK` tree: the extracted tree drifts from the dump, and a missing
    name there would report a bound handler as undeclared. A handler inherited from a parent class is
    declared, so the walk goes up: the delegate names the function, not where it was written.
    """
    out, cur, seen = set(), cls, set()
    by_name = {}
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class":
            by_name[pkg.name(e["name"])] = e
    while cur and cur not in seen and len(seen) < 12:
        seen.add(cur)
        e = by_name.get(cur)
        if e is None:                                    # an ancestor in another package: not in this table
            break
        try:
            text = pst.script_text(pkg, e) or ""
        except Exception:                                # noqa: BLE001 - a class with no text is a negative
            text = ""
        out |= {m.group(1) for m in (pst.FUNC_START.match(line) for line in text.splitlines()) if m}
        cur = supers.get(cur)
    return out


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


def row_for(pkg, pkg_name, cls, p, meta, buf, decl_fns):
    off = p.get("value_offset") or 0
    size = p.get("size") or 0
    info = p.get("type_byte", 0)
    value = buf[off:off + size]
    idx = tile(buf, off, size)
    row = {
        "package": pkg_name,
        "class": cls,
        "property": p["name"],
        "declared": (meta[1] if meta else "") or p["type"],
        "tag_type": TYPES[(info & 0x0f)] if (info & 0x0f) < len(TYPES) else "?",
        "type_byte": "0x%02x" % info,
        "size_class": (info & 0x70) >> 4,
        "size": size,
        "indices": len(idx),
        "object_kind": "",
        "object_self": "",
        "handler": "",
        "handler_declared": "",
        "note": "",
    }
    if len(idx) == 2:
        kind, text = object_of(pkg, idx[0])
        row["object_kind"] = kind
        row["object_self"] = "yes" if text.endswith("'%s'" % cls) else "no"
        try:
            handler = pkg.name(idx[1])
        except Exception:                               # noqa: BLE001
            handler = ""
        row["handler"] = handler
        row["handler_declared"] = "yes" if handler and handler in decl_fns else "no"
        if not handler or handler == "?":
            row["note"] = "name index outside the name table"
        elif kind != "export":
            row["note"] = "object is not a class export"
    else:
        row["note"] = "does not tile as {object, name}"
    return row


def sweep(system_dir, pkgs):
    supers = cd.super_map()
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
            props, start, _clean = got
            meta_of = de.prop_meta_for(cls, supers, props_meta)
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            buf = data[start:]
            decl_fns = None
            for p in props:
                meta = meta_of(p["name"])
                if not is_delegate(p["name"], (meta[1] if meta else "")):
                    continue
                if decl_fns is None:
                    decl_fns = functions_of(pkg, exp, cls, supers)
                rows.append(row_for(pkg, pkg_name, cls, p, meta, buf, decl_fns))
    return rows


def emit(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLUMNS})


def report(rows):
    head = ("%-14s %-26s %-32s %-9s %6s %4s %4s %-8s %-4s %-26s %s"
            % ("package", "class", "property", "tag", "size", "cls", "idx", "object", "self",
               "handler", "declared"))
    print(head)
    print("-" * len(head))
    for r in rows:
        print("%-14s %-26s %-32s %-9s %6d %4d %4d %-8s %-4s %-26s %s"
              % (r["package"], r["class"][:26], r["property"][:32], r["tag_type"][:9], r["size"],
                 r["size_class"], r["indices"], r["object_kind"][:8], r["object_self"] or "-",
                 r["handler"][:26], r["handler_declared"] or "-"))
    print()
    ok = [r for r in rows if r["indices"] == 2]
    self_ok = [r for r in ok if r["object_self"] == "yes" and r["handler_declared"] == "yes"]
    print("%d delegate value(s): %d decode as {object, name}, %d of those bind a declared function to "
          "the class itself; %d do not decode" % (len(rows), len(ok), len(self_ok), len(rows) - len(ok)))


def self_test():
    """Synthetic bodies only: `--check` answers without the dump and writes nothing."""
    assert is_delegate("__OnXControllerEvent__Delegate", ""), "the emitted marker is a delegate"
    assert is_delegate("OnXControllerEvent", "delegate<OnXControllerEvent>"), \
        "the declared type is a delegate even without the marker"
    assert not is_delegate("Ammo", "int"), "an int is not a delegate"
    two = bytes([0x02, 0x0f])
    assert tile(two, 0, 2) == [2, 15], "two one-byte indices tile two bytes"
    assert tile(bytes([0x6b, 0x01, 0x0f]), 0, 3) == [107, 15], \
        "bit 6 continues the index: a three-byte value is two indices, not three"
    assert tile(bytes([0x02, 0x0f, 0x41]), 0, 3) == [], "a continuation index past the end does not tile"
    assert tile(bytes([0x41]), 0, 1) == [], "a truncated continuation index does not tile"
    print("self-test: ok (6 synthetic cases)")
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
