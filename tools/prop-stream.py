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
"""
import argparse
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

TYPES = {0: "Invalid", 1: "Byte", 2: "Int", 3: "Bool", 4: "Float", 5: "Object", 6: "Name",
         7: "String", 8: "Class", 9: "Array", 10: "Struct", 11: "Vector", 12: "Rotator",
         13: "Str", 14: "Map", 15: "FixedArray"}
SIZES = {0: 1, 1: 2, 2: 4, 3: 12, 4: 16}


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


def find_block(data, pkg, min_props=2):
    """Locate the property block: the earliest start whose block terminates exactly at the export's end.

    UE2 objects do not all begin with their property block -- a class can serialise native data first (a
    StaticMeshActor's 12-byte instance prefix, for example). So the start is found by requiring the block to
    end where the export ends and to contain only resolving names, and the candidate with the most properties
    wins: a mis-parse that starts a few bytes late loses properties rather than inventing them.
    """
    best = None
    for start in range(0, min(len(data), 64)):
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
        if best is None or len(props) > best[0]:
            best = (len(props), start, props)
    return best


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
