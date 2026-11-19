#!/usr/bin/env python3
"""Read the UnrealScript property block of an instanced object (PLAN 5.2's missing half).

The layout is the one measured from the game's own reader (`docs/NATIVE_PROPERTIES.md` §6) and confirmed
against a public format reference: a property block is a list of `index name` + property value, terminated by
the name `None`, and **only instanced objects have one** — a class object's header is empty.

The value header is one info byte: type in the low nibble, size class in bits 4-6, and for structs the struct
name comes first. Array indices and bool values ride in bit 7.

    python3 tools/prop-stream.py PKG.unr --export 50            # names, types, sizes
    python3 tools/prop-stream.py PKG.unr --export 50 --values   # + decoded simple values
    python3 tools/prop-stream.py PKG.unr --class StaticMeshActor --limit 3

Values are game content: this prints them for inspection, and nothing from it belongs in the repository.

**The value is the locator's second opinion** (2026-11-19). `find_block` used to take the start with the most
properties whose names all resolve, which a parse that began inside a value can satisfy: a struct's payload
read as tags re-encodes as plausibly as a real block. Every value is decoded by its class's own rule
(`decode_value`, the fourteen `SerializeItem` bodies of `docs/NATIVE_PROPERTIES.md` §6) and has to consume
exactly the `Size` its header declares, and a candidate that fails is passed over for one that does not.
"""
import argparse
import importlib.util
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

TYPES = {0: "Invalid", 1: "Byte", 2: "Int", 3: "Bool", 4: "Float", 5: "Object", 6: "Name",
         7: "String", 8: "Class", 9: "Array", 10: "Struct", 11: "Vector", 12: "Rotator",
         13: "Str", 14: "Map", 15: "FixedArray"}
SIZES = {0: 1, 1: 2, 2: 4, 3: 12, 4: 16}

# The prefix the header rule looks at (unchanged since the locator was written), and the wider one the value
# test may fall back to: a `Brush` keeps its poly data in front of the block and starts at 74 on the worst one.
WINDOW = 64
PREFIX_WINDOW = 96


def read_index(buf, off):
    b = buf[off]
    sign, cont, val = b & 0x80, b & 0x40, b & 0x3F
    off += 1
    shift = 6
    while cont and shift < 32:
        b = buf[off]
        off += 1
        cont = b & 0x80
        val |= (b & 0x7F) << shift
        shift += 7
    return (-val if sign else val), off


def read_array_index(buf, off):
    b1 = buf[off]
    off += 1
    if (b1 & 0xC0) == 0xC0:
        v = ((b1 & 0x3F) << 24) | (buf[off] << 16) | (buf[off + 1] << 8) | buf[off + 2]
        return v, off + 3
    if b1 & 0x80:
        return ((b1 & 0x7F) << 8) | buf[off], off + 1
    return b1, off


def read_fstring(buf, start, size):
    """Decode an FString whose value region is `size` bytes at `start` (length prefix counts the NUL)."""
    if start >= len(buf):
        return None
    n = buf[start]
    used = 1
    if n & 0x80:
        if start + 1 >= len(buf):
            return None
        used = 2
    return buf[start + used:start + size].split(b"\0")[0].decode("latin1", "replace")


def read_value(buf, off, pkg, want_values):
    """Return (entry dict, new offset) for one property value starting at `off` (the info byte)."""
    info = buf[off]
    off += 1
    t = info & 0x0F
    cls = (info & 0x70) >> 4
    entry = {"type": TYPES.get(t, "?%d" % t), "type_byte": info, "array_index": 0, "struct": ""}
    if t == 10:  # struct: struct name precedes the size class
        idx, off = read_index(buf, off)
        entry["struct"] = pkg.name(idx)
    if cls in SIZES:
        size = SIZES[cls]
    elif cls == 5:
        size = buf[off]; off += 1
    elif cls == 6:
        size = struct.unpack_from("<H", buf, off)[0]; off += 2
    else:
        size = struct.unpack_from("<I", buf, off)[0]; off += 4
    entry["size"] = size
    if info & 0x80 and t != 3:
        entry["array_index"], off = read_array_index(buf, off)
    elif t == 3:
        entry["value"] = bool(info & 0x80)
    start = off
    entry["value_offset"] = start
    off += size
    if want_values:
        # Values inside package property streams are *compact*: a name or object reference can be one or two
        # bytes, and vectors arrive as Struct(Vector) rather than as a Vector type. Reading them by assumed
        # width is what left Location and Squad empty in the first attempt at this.
        if t == 10:
            sn = entry["struct"]
            if sn == "Vector" and size == 12:
                entry["value"] = struct.unpack_from("<3f", buf, start)
            elif sn == "Rotator" and size == 12:
                entry["value"] = struct.unpack_from("<3i", buf, start)
            elif sn == "Color" and size >= 4:
                entry["value"] = tuple(buf[start:start + 4])
            elif sn == "PointRegion" and size >= 13:
                entry["value"] = None
        elif t in (5, 6):
            idx, used = read_index(buf, start)
            entry["index_bytes"] = used - start
            if t == 5:
                entry["ref"] = idx
                entry["value"] = resolve_ref(pkg, idx)
            else:
                entry["value"] = pkg.name(idx)
        elif t == 4 and size == 4:
            entry["value"] = struct.unpack_from("<f", buf, start)[0]
        elif t == 2 and size == 4:
            entry["value"] = struct.unpack_from("<i", buf, start)[0]
        elif t == 2 and size == 2:
            entry["value"] = struct.unpack_from("<h", buf, start)[0]
        elif t == 1:
            entry["value"] = buf[start]
        elif t == 13:
            # Str is an FString: `size` bytes of text, not a name index. Reading it as an index is what made
            # `Description` decode as the name `BeginState` -- and reading the *length prefix* as text is what
            # then made it decode as "\x0cEmpty Order": the prefix counts the terminating NUL, and the NUL is
            # only stored when the declared size has room for it.
            text = read_fstring(buf, start, size)
            entry["value"] = text if text is not None else ""
        elif t == 7 or t == 9:
            entry["value"] = buf[start:start + size].split(b"\0")[0].decode("latin1", "replace")
    return entry, off


def resolve_ref(pkg, ref):
    """Object reference -> `Class'Name'` inside the package, `Class'Package.Name'` when imported, `None`.

    The import branch used to print the class package in the class slot and the object name in the package
    slot (`icon_mission\'Engine\'`), which is the right two names in the wrong order -- the reference resolved,
    so it looked decoded. The class name is the import\'s `Class`, its package the import\'s `ClassPackage`, and
    the object is the import\'s own `ObjectName`.
    """
    if ref == 0:
        return "None"
    try:
        if ref > 0:
            exp = pkg.exports[ref - 1]
            return "%s\'%s\'" % (pkg.class_name_of(exp), pkg.name(exp["name"]))
        imp = pkg.imports[-ref - 1]
        return "%s\'%s.%s\'" % (pkg.name(imp["class"]), pkg.name(imp["class_package"]),
                                 pkg.name(imp["name"]))
    except (IndexError, KeyError):
        return "ref:%d" % ref


def read_block(buf, pkg, want_values=False, limit=2000):
    """Parse the property block at the start of `buf`; returns (properties, end offset, reason)."""
    off, props = 0, []
    while off < len(buf) and len(props) < limit:
        name, off = read_index(buf, off)
        if name == 0 or pkg.name(name) == "None":
            return props, off, "None"
        entry, off = read_value(buf, off, pkg, want_values)
        entry["name"] = pkg.name(name)
        props.append(entry)
        if off > len(buf):
            return props, off, "overrun"
    return props, off, "limit"


def find_block(data, pkg, min_props=2, values=True, native_width=None):
    """Locate the property block: the start whose block terminates exactly at the export's end.

    UE2 objects do not all begin with their property block -- a class can serialise native data first (a
    StaticMeshActor's 12-byte instance prefix, a Brush's 15, a Mover's 12) -- so the start is found by
    requiring the block to end where the export ends and to contain only resolving names.  Among those
    candidates the one with the most properties wins, because a mis-parse that starts a few bytes late
    normally loses properties rather than inventing them.

    That tie-break is not enough on its own: a parse that starts *inside* a value reads the value's own
    bytes as names and sizes and can produce a whole block that still ends at the export's end -- a
    `Mover`'s `PointRegion`/`Vector`/`Scale` struct payload becomes three plausible properties, and one of
    them decodes to a value its class cannot produce.  With `values` the candidates are therefore tested in
    the same order and the first whose every checkable value consumes exactly its declared `Size` wins;
    a block nothing can check (`seen == 0`) is not evidence and falls through to the property count.  When
    no candidate passes, the header's choice stands -- that residue is counted, not guessed away.
    """
    found = candidates(data, pkg, min_props)
    best = max(found, key=lambda c: c[0]) if found else None
    if not values:
        return best
    for count, start, props in sorted(found, key=lambda c: (-c[0], c[1])):
        bad, seen = block_bad(props, data, start, native_width)
        if bad == 0 and seen:
            return count, start, props
    # Nothing inside the header's window decodes: look one window further before accepting a block the
    # value test knows is wrong, and fall back to the header's choice only when nothing there does either.
    wider = candidates(data, pkg, min_props, PREFIX_WINDOW, WINDOW)
    for count, start, props in sorted(wider, key=lambda c: (-c[0], c[1])):
        bad, seen = block_bad(props, data, start, native_width)
        if bad == 0 and seen:
            return count, start, props
    return best


# ---------------------------------------------------------------- the value half

# What the tag's type nibble means for the value.  `CHECKABLE` are the types whose property class has a
# `SerializeItem` body in this build (`inventory/property_serialisers.csv`); `IMPOSSIBLE` are the ones no
# property class reports, so a tag carrying them is a parse and not a property.
CHECKABLE = {1, 2, 3, 4, 5, 6, 8, 10, 13}
IMPOSSIBLE = {0, 7, 11, 12}

# The three structs `0x000795f0` skips the tagged walk for, with the width the corpus measures for each
# (`inventory/value_layouts.csv`: 521 `Color`, 51,963 `Rotator`, 154,254 `Vector` values, one width each).
# `value-layouts --sweep` re-measures it and passes its own table here, so this default is what a caller
# with no census in hand gets.
NATIVE_WIDTH = {"Color": 4, "Rotator": 12, "Vector": 12}

_TAG = None


def tag_walk():
    """`tag-encode`, loaded on first use: it imports this module, so the dependency has to be deferred."""
    global _TAG
    if _TAG is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tag-encode.py")
        spec = importlib.util.spec_from_file_location("tag_encode", path)
        _TAG = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_TAG)
    return _TAG


def decode_value(t, v, struct_name, native_width=None):
    """Does this value consume exactly its declared `Size`?  Returns `(verdict, detail)`.

    `exact`, `inexact`, `open` for a type with no self-describing rule (`Map`, `Array`, `FixedArray`, whose
    `Inner` the tag does not name) and `impossible` for a type no property class in this build reports.
    """
    size = len(v)
    if t in IMPOSSIBLE:
        return "impossible", "no property class"
    if t == 1:
        return ("exact" if size == 1 else "inexact"), "1"
    if t in (2, 4):
        return ("exact" if size == 4 else "inexact"), "4"
    if t == 3:
        return ("exact" if size == 0 else "inexact"), "0"
    if t in (5, 6, 8):
        try:
            _idx, off = read_index(v, 0)
        except IndexError:
            return "inexact", "truncated index"
        return ("exact" if off == size else "inexact"), "%d-byte index" % off
    if t == 13:
        try:
            n, off = read_index(v, 0)
        except IndexError:
            return "inexact", "truncated length"
        want = off + (2 * abs(n) if n < 0 else n)
        return ("exact" if want == size else "inexact"), "%d chars" % abs(n)
    if t == 10:
        width = NATIVE_WIDTH if native_width is None else native_width
        if struct_name in width:
            want = width[struct_name]
            return ("exact" if want == size else "inexact"), "native %d" % want
        te = tag_walk()
        tags, end, reason = te.walk(v)
        if reason != "None" or end != len(v):
            return "inexact", "tagged walk %s at %d" % (reason, end)
        equal, _terms, reasons = te.compare(v, tags)
        if reasons:
            return "inexact", "tagged walk re-encodes %d of %d" % (equal, len(tags) - 1)
        return "exact", "tagged block, %d tag(s)" % (len(tags) - 1)
    return "open", ""


def block_bad(props, data, start, native_width=None):
    """How many of this block's checkable values fail their class's rule, and how many were checked.

    `props` is `read_block`'s parse of `data[start:]`, so the value sits at `start + value_offset` and its
    declared width is the tag's `size`; the types with no rule of their own are not counted either way.
    A tag type no class reports is *not* counted here: it says the parse is wrong somewhere, but not which
    start is right, and 26,257 of them sit in blocks no start fixes (the census counts those separately).
    """
    bad = seen = 0
    for p in props:
        t = p["type_byte"] & 0x0F
        if t not in CHECKABLE:
            continue
        seen += 1
        v = data[start + p["value_offset"]:start + p["value_offset"] + p["size"]]
        verdict, _detail = decode_value(t, v, p.get("struct") or "?", native_width)
        if verdict == "inexact":
            bad += 1
    return bad, seen


def candidates(data, pkg, min_props=2, window=WINDOW, start_from=0):
    """Every start in the prefix window whose header parse ends exactly at the export's end: `(count, start, props)`."""
    out = []
    for start in range(start_from, min(len(data), window)):
        try:
            props, end, reason = read_block(data[start:], pkg, False)
        except Exception:
            continue
        if reason != "None" or len(props) < min_props:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + end != len(data):
            continue
        out.append((len(props), start, props))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--export", type=int, help="export index (1-based)")
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--limit", type=int, default=3, help="how many objects to dump in --class mode")
    ap.add_argument("--values", action="store_true")
    ap.add_argument("--scan", action="store_true",
                    help="find the property block per export of --class and summarise")
    args = ap.parse_args()

    pkg = Package(args.package)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()

    if args.scan:
        targets = [e for e in pkg.exports if pkg.class_name_of(e) == args.cls]
        ok = 0
        prefixes = {}
        prop_counts = {}
        for exp in targets:
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            found = find_block(data, pkg)
            if not found:
                continue
            ok += 1
            count, start, props = found
            prefixes[start] = prefixes.get(start, 0) + 1
            for p in props:
                prop_counts[p["name"]] = prop_counts.get(p["name"], 0) + 1
        print("%-22s blocks %d/%d  prefixes %s" % (args.cls, ok, len(targets),
                                                   sorted(prefixes.items(), key=lambda kv: -kv[1])[:3]))
        print("   top properties: %s" % sorted(prop_counts.items(), key=lambda kv: -kv[1])[:10])
        return 0

    if args.export:
        targets = [pkg.exports[args.export - 1]]
    else:
        targets = [e for e in pkg.exports if pkg.class_name_of(e) == args.cls][: args.limit]
    if not targets:
        sys.exit("no matching export")

    ok = 0
    for exp in targets:
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        label = "%s #%d" % (pkg.class_name_of(exp), exp["index"] + 1)
        props, end, reason = read_block(data, pkg, args.values)
        trailing = len(data) - end
        ok += 1 if reason == "None" else 0
        print("%-28s %3d properties, %d trailing bytes (%s)  %s"
              % (label, len(props), trailing, reason,
                 ", ".join(p["name"] for p in props[:6]) + ("..." if len(props) > 6 else "")))
        if args.values:
            for p in props:
                if "value" in p:
                    print("      %-24s %-8s %s" % (p["name"], p["type"], p.get("value")))
    print("blocks terminated cleanly: %d of %d" % (ok, len(targets)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
