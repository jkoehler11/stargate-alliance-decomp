#!/usr/bin/env python3
"""Emit `defaultproperties` blocks from the packages for the compile loop (PLAN 3.5, 3.6).

`docs/CLASS_DEFAULTS.md` proved the defaults *block* can be located and its property names read. This tool
takes the next step: decode each value and print it as the UnrealScript a compiler would accept, so the
reconstructed source tree has defaults and not just functions.

    python3 tools/defaults-emit.py DUMP/System --sweep -o inventory     # coverage artifact
    python3 tools/defaults-emit.py DUMP/System --emit $WORK/defaults    # one .uc snippet per class
    python3 tools/defaults-emit.py DUMP/System --verify                  # against the shipped text blocks
    python3 tools/defaults-emit.py DUMP/System --class SGOrder --package StargateGame --show

Rendering rules, all of them learned from bytes rather than assumed:

* `Str`/`String` is an FString: a length prefix (1 byte, 2 when the high bit is set) *counts the terminating
  NUL*, and the NUL is only stored when the declared size has room for it. Reading the length byte as the
  first character is what made `Description` print as "\\x0cEmpty Order".
* an object reference renders as `Class'Name'` inside the package and `Class'Package.Name'` across one, which
  is the form the game's own T3D writer uses (`Squad=SQUAD_x'SQUAD_x2'` in `docs/T3D.md`) and the form the
  shipped source shows (`ProjectorMaterial=Texture'SG1_wep_tex.wep_p90_torchlens'`).
* a `Byte` property is an enum: `inventory/class_properties.csv` carries the enum's *type* name, and the
  element list is read out of the class's own shipped script (`enum EPhysics { PHYS_None, ... }`). Without
  both halves the value is written as a number, which is why the CSV counts the two cases apart.
* `Struct(Vector)`, `Struct(Rotator)`, `Struct(Color)` and `Struct(Plane)` are rendered field by field;
  every other struct is left out and counted. Arrays are left out and counted: their value stream is not the
  `count` + tagged-element shape a first guess takes it for (that parse succeeds on 66 of 555 instances).

Values are game data: nothing here prints a value to stdout unless `--show` is given, and `--emit` writes to the
caller's directory (`$WORK`, never this repository).
"""
import argparse
import collections
import csv
import importlib.util
import os

import re
import statistics
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkcsv  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from ue2pkg import Package  # noqa: E402

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_STR = re.compile(r"^[A-Za-z0-9_%.$:+/*-]+$")
ENUM_DECL = re.compile(r"\benum\s+(\w+)\s*\{(.*?)\}", re.S)
RENDERED_STRUCTS = ("Vector", "Rotator", "Color", "Plane")


def load_tool(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cd = load_tool("class_defaults", "class-defaults.py")
pst = load_tool("pkg_script_text", "pkg-script-text.py")
ps = cd.load_prop_stream()


# ----------------------------------------------------------------------------------------------- metadata

def property_meta(path=None):
    """class -> property -> (kind, friendly_type), from the committed class_properties.csv."""
    path = path or os.path.join(ROOT, "inventory", "class_properties.csv")
    out = collections.defaultdict(dict)
    if not os.path.isfile(path):
        return out
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out[r["class"]][r["property"]] = (r["kind"], r["friendly_type"])
    return out


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def global_enums(system_dir):
    """enum name -> element list, over every package; (table, conflicts).

    Enum *elements* live only in the shipped script, and the declaring package is not always the package the
    referencing class is compiled into: a `StargateGame` class inheriting `Physics` gets its element names from
    `Engine`. A per-package table resolved 224 of 1,211 byte values; one table over the whole dump resolves the
    cross-package half as well.
    """
    table = {}
    conflicts = 0
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for name, elems in enum_table(pkg, pst.script_text).items():
            if name in table:
                if table[name] != elems:
                    conflicts += 1
                continue
            table[name] = elems
    return table, conflicts


def enum_table(pkg, script_text):
    """enum name -> element names, from the package's own shipped script.

    The element *order* is the byte value, so this is the only place the names can come from: the package
    stores an enum property's type name and a number, never its elements.
    """
    found = {}
    for exp in pkg.exports:
        if pkg.class_name_of(exp) != "Class":
            continue
        text = script_text(pkg, exp)
        if not text:
            continue
        for m in ENUM_DECL.finditer(strip_comments(text)):
            name, body = m.group(1), m.group(2)
            elems = []
            for part in body.split(","):
                part = part.strip()
                if not part:
                    continue
                elems.append(part.split("=")[0].strip())
            if elems and name not in found:
                found[name] = elems
    return found


# ------------------------------------------------------------------------------------------------ render

def read_fstring(buf, start, size):
    """FString at `start` of a tag whose value region is `size` bytes; None if it does not fit."""
    if start >= len(buf):
        return None
    n = buf[start]
    used = 1
    if n & 0x80:
        if start + 1 >= len(buf):
            return None
        n = ((n & 0x7F) << 8) | buf[start + 1]
        used = 2
    raw = buf[start + used:start + size]
    return raw.split(b"\0")[0].decode("latin1", "replace")


def quote_string(value):
    if SAFE_STR.match(value) and value:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % escaped


def ref_name(pkg, ref):
    """Object reference -> (class name, package, object name) or None for None."""
    if ref == 0:
        return None
    if ref > 0:
        try:
            exp = pkg.exports[ref - 1]
        except IndexError:
            return ("?", "", "")
        return (pkg.class_name_of(exp), "", pkg.name(exp["name"]))
    try:
        imp = pkg.imports[-ref - 1]
    except IndexError:
        return ("?", "", "")
    return (pkg.name(imp["class"]), pkg.name(imp["class_package"]), pkg.name(imp["name"]))


PRIMITIVE_ELEMENT = {"float": 4, "int": 4, "byte": 1, "bool": 1}
STRUCT_LAYOUT = {"Vector": ("<3f", "X,Y,Z"), "Rotator": ("<3i", "Pitch,Yaw,Roll"),
                 "Color": ("4B", "R,G,B,A"), "Plane": ("<4f", "X,Y,Z,W")}
# A property's friendly type can carry *several* flags: `array<export editinline ParticleEmitter>` is one array
# of subobject references, not an array of "export editinline" things. Stripping one flag left the rest in the type
# name, which is why these were reported as an unknown element type.
INLINE_FLAGS = re.compile(r"^(?:(?:editinlinenotify|editinline|export|localized|const|private|protected)\s+)+",
                          re.I)


def inner_type(meta):
    """`array<Sound>` -> `Sound`; `array<editinline MatchInfo>` -> `MatchInfo`."""
    if not meta or not meta[1]:
        return None
    m = re.match(r"array<(.*)>$", meta[1].strip())
    if not m:
        return None
    return INLINE_FLAGS.sub("", m.group(1).strip())


def render_ref(pkg, ref, meta):
    got = ref_name(pkg, ref)
    if got is None:
        return "None"
    cls, package, name = got
    if not cls or cls == "?":
        return None
    object_name = "%s.%s" % (package, name) if package else name
    if meta and meta[0] == "UClassProperty":
        return "class'%s'" % object_name
    return "%s'%s'" % (cls, object_name)


def render_fstring(buf, off, end):
    """FString at `off` in an untagged element: (text, new offset), or (None, off) if it overruns."""
    if off >= end:
        return None, off
    n = buf[off]
    used = 1
    if n & 0x80:
        if off + 1 >= end:
            return None, off
        n = ((n & 0x7F) << 8) | buf[off + 1]
        used = 2
    stop = min(off + used + n, end)
    return buf[off + used:stop].split(b"\0")[0].decode("latin1", "replace"), stop


def render_struct_fields(name, values):
    if name == "Vector":
        return "(X=%s,Y=%s,Z=%s)" % tuple(format_float(v) for v in values)
    if name == "Rotator":
        return "(Pitch=%d,Yaw=%d,Roll=%d)" % values
    if name == "Color":
        return "(R=%d,G=%d,B=%d,A=%d)" % values
    if name == "Plane":
        return "(X=%s,Y=%s,Z=%s,W=%s)" % tuple(format_float(v) for v in values)
    return None


def render_element(pkg, buf, off, inner, ctx, end, depth=0):
    """One array element: untagged, so its width comes from the element type, not from a size byte.

    Every read here is bounded by the array's own value region; a short read means the element type guess is
    wrong, which is a refusal rather than a partial element.
    """
    try:
        return _render_element(pkg, buf, off, inner, ctx, end, depth)
    except (IndexError, struct.error):
        return None, off


def _render_element(pkg, buf, off, inner, ctx, end, depth=0):
    if not inner:
        return render_tagged_struct(pkg, buf, off, end, ctx, depth + 1, exact=False)
    low = inner.lower()
    if re.match(r"class<.*>$", inner, re.I):
        # `array<Class<Emitter>>`: the element is an object reference to a class, not to an instance.
        idx, off2 = ps.read_index(buf, off)
        text = render_ref(pkg, idx, ("UClassProperty", "class"))
        return (text, off2) if text else (None, off)
    if low in PRIMITIVE_ELEMENT:
        width = PRIMITIVE_ELEMENT[low]
        if off + width > end:
            return None, off
        if low == "float":
            return format_float(struct.unpack_from("<f", buf, off)[0]), off + width
        if low == "int":
            return str(struct.unpack_from("<i", buf, off)[0]), off + width
        if low == "bool":
            return ("True" if buf[off] else "False"), off + width
        return str(buf[off]), off + width
    if low in ("name", "object"):
        idx, off2 = ps.read_index(buf, off)
        if low == "name":
            # A name element goes through the same quoting rule as a scalar name: `bip01 Spine` is a name
            # with a space in it, and unquoted it would be two tokens to the compiler.
            name = pkg.name(idx)
            return (name if IDENT.match(name) else quote_string(name)), off2
        text = render_ref(pkg, idx, None)
        return (text, off2) if text else (None, off)
    if low in ("string", "str"):
        text, off2 = render_fstring(buf, off, end)
        return (quote_string(text), off2) if text is not None else (None, off)
    if inner in STRUCT_LAYOUT:
        fmt, _names = STRUCT_LAYOUT[inner]
        width = struct.calcsize(fmt)
        if off + width > end:
            return None, off
        return render_struct_fields(inner, struct.unpack_from(fmt, buf, off)), off + width
    if inner in ctx["enums"]:
        elems = ctx["enums"][inner]
        value = buf[off]
        return (elems[value] if value < len(elems) else str(value)), off + 1
    if low in ctx["classes"]:
        idx, off2 = ps.read_index(buf, off)
        text = render_ref(pkg, idx, None)
        return (text, off2) if text else (None, off)
    # A struct whose fields are themselves tagged: UELib reads tags until `None`, not by a fixed width.
    # One *element* of an array ends at its own `None`, not at the array's end (see `render_tagged_struct`).
    return render_tagged_struct(pkg, buf, off, end, ctx, depth + 1, exact=False)


def format_inline(text):
    """An array rendered inside a struct: `(elem,elem)` rather than the top level's `Name(i)=elem` lines."""
    if isinstance(text, list):
        return "(" + ",".join(value for _index, value in text) + ")"
    return text


def render_tagged_struct(pkg, buf, start, end, ctx, depth=0, exact=True):
    """A struct serialised as a nested tagged block (`Field=value` pairs, `None`-terminated).

    UELib's `UDefaultProperty` falls back to this for every struct its atomic serialiser does not claim, and the
    recursion is bounded because a malformed stream can otherwise nest without end.
    """
    if depth > 4 or end > len(buf):
        return None, start
    fields, off = [], start
    while off < end:
        try:
            idx, off = ps.read_index(buf, off)
            if idx == 0 or pkg.name(idx) == "None":
                break
            entry, off = ps.read_value(buf, off, pkg, True)
        except (IndexError, struct.error):
            return None, start
        text, _note = render_value(pkg, entry, None, buf, ctx, depth + 1)
        if text is None:
            return None, start
        # An array inside a struct is written inline and positionally, `Points=((InVal=..),(InVal=..))`:
        # `UDefaultProperty` does exactly this for `WithinStruct` -- element names and indices only appear at
        # the top level of a `defaultproperties` block.
        fields.append("%s=%s" % (pkg.name(idx), format_inline(text)))
    if not fields or (exact and off != end):
        return None, start
    return "(" + ",".join(fields) + ")", off


def render_value(pkg, entry, meta, buf, ctx=None, depth=0):
    """(text, note) for one decoded property; text is None when the type is not rendered."""
    ctx = ctx or {"enums": {}, "classes": set()}
    t = entry["type"]
    if t == "Byte":
        value = entry.get("value")
        if not isinstance(value, int):
            return None, "byte"
        return str(value), "byte"
    if t == "Int":
        return str(entry.get("value")), "int"
    if t == "Float":
        v = entry.get("value")
        if not isinstance(v, float):
            return None, "float"
        return format_float(v), "float"
    if t == "Bool":
        return ("True" if entry.get("value") else "False"), "bool"
    if t == "Name":
        v = entry.get("value")
        if not isinstance(v, str):
            return None, "name"
        return (v if IDENT.match(v) else quote_string(v)), "name"
    if t in ("Str", "String"):
        v = entry.get("value")
        if not isinstance(v, str):
            return None, "str"
        return quote_string(v), "str"
    if t in ("Object", "Class"):
        text = render_ref(pkg, entry.get("ref", 0), meta)
        return (text, "object") if text else (None, "object")
    if t == "Struct":
        sn = entry.get("struct")
        v = entry.get("value")
        if sn in STRUCT_LAYOUT:
            fmt, _names = STRUCT_LAYOUT[sn]
            if not isinstance(v, tuple) and entry.get("value_offset") is not None:
                # prop-stream decodes Vector/Rotator/Color only; the other atomic layouts are unpacked here,
                # and only when the tag's declared size agrees with the layout.
                start = entry["value_offset"]
                if entry["size"] == struct.calcsize(fmt):
                    try:
                        v = struct.unpack_from(fmt, buf, start)
                    except struct.error:
                        v = None
            if isinstance(v, tuple):
                text = render_struct_fields(sn, v)
                if text:
                    return text, sn.lower()
            # A struct whose name is atomic can still be serialised as tags: `Plane` arrives as four tagged
            # floats (29 bytes) in this build, not as 16 raw ones. Fall through rather than refuse.
        if depth >= 3:
            return None, "struct"
        start = entry.get("value_offset")
        if start is None:
            return None, "struct"
        text, _off = render_tagged_struct(pkg, buf, start, start + entry["size"], ctx, depth + 1)
        return (text, "struct-nested") if text else (None, "struct")
    if t == "Array":
        count_off = entry.get("value_offset")
        inner = inner_type(meta)
        # A nested array inside a struct has no metadata here: `inventory/class_properties.csv` covers
        # class-owned properties only, so `InterpCurve.Points` names no inner type. The elements of a struct
        # array are tagged, i.e. self-describing, so the attempt is still a read -- the tiling test below is
        # what makes a wrong guess a refusal.
        if count_off is None:
            return None, "array"
        end = count_off + entry["size"]
        try:
            count, off = ps.read_index(buf, count_off)
        except IndexError:
            return None, "array"
        if count < 0 or count > 4096:
            return None, "array"
        parts = []
        for i in range(count):
            text, off = render_element(pkg, buf, off, inner, ctx, end, depth)
            if text is None:
                return None, "array"
            parts.append((i, text))
        if off != end:
            # The elements have to tile the value region exactly; anything else is a wrong element type, and
            # a wrong element type that happens to stop inside the size is exactly what a guess looks like.
            return None, "array"
        return parts, "array"
    return None, "other"


def format_float(v):
    if v != v or v in (float("inf"), float("-inf")):
        return "0.0"
    text = repr(round(v, 6))
    if "e" in text or "E" in text:
        text = "%.9f" % v
    if text.endswith("."):
        text += "0"
    if "." not in text:
        text += ".0"
    return text


# ------------------------------------------------------------------------------------------------- blocks

def block_for_export(pkg, exp, cls, supers, declared_all, limit=None):
    """Locate one export's property block, scored against the properties `cls` declares.

    `block_of` finds a class by name; a subobject is an export whose own name is not a class, so the class it is
    scored against is its *class* (`pkg.class_name_of`), and the same candidate/choose rule applies.
    """
    decl = set()
    cur, seen = cls, set()
    while cur and cur not in seen:
        seen.add(cur)
        decl |= declared_all.get(cur, set())
        cur = supers.get(cur)
    cands, _data = cd.candidates(pkg, ps, exp, limit=limit)
    best = cd.choose(cands, decl)
    if best is None:
        return None, "no-block"
    _score, _count, start, _names = best
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    try:
        props, end, reason = ps.read_block(data[start:], pkg, True)
    except Exception:                                   # noqa: BLE001 - a short payload is a skip, not a crash
        return None, "unparsed"
    if reason != "None":
        return None, "unparsed"
    return (props, start, best[0][0] == 1.0), None


def block_of(pkg, cls, supers, declared_all, limit=None, want_score=False):
    """(props, start, trailer) for a class's defaults block, or (None, reason)."""
    exp = None
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls:
            exp = e
            break
    if exp is None:
        return None, "no-export"
    decl = set()
    cur, seen = cls, set()
    while cur and cur not in seen:
        seen.add(cur)
        decl |= declared_all.get(cur, set())
        cur = supers.get(cur)
    cands, _data = cd.candidates(pkg, ps, exp, limit=limit)
    best = cd.choose(cands, decl)
    if best is None:
        return None, "no-block"
    _score, _count, start, _names = best
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    try:
        props, end, reason = ps.read_block(data[start:], pkg, True)
    except Exception:                                   # noqa: BLE001 - a short payload is a skip, not a crash
        return None, "unparsed"
    if reason != "None":
        return None, "unparsed"
    clean = best[0][0] == 1.0
    if want_score:
        return (props, start, clean), None
    return (props, start, len(data) - start - end), None


def render_block(pkg, props, meta_of, enums, buf_offset_data):
    """[(name, text|None, note)] for one block, plus the raw buffer for further use."""
    out = []
    for p in props:
        meta = meta_of(p["name"])
        text, note = render_value(pkg, p, meta, buf_offset_data)
        if text is None and note == "byte":
            note = "byte"
        out.append((p["name"], text, note))
    return out


# -------------------------------------------------------------------------------------------------- sweep

COLUMNS = ["package", "classes", "blocks", "clean_blocks", "dirty_properties", "properties", "rendered",
           "arrays_rendered", "arrays_unrendered", "array_elements", "structs_unrendered", "nested_structs",
           "other", "inline_objects", "inline_properties", "inline_rendered",
           "bytes", "bytes_resolved", "floats", "ints", "bools", "names", "strs", "objects", "geometry"]


def empty_row(pkg_name):
    return dict((c, 0) for c in COLUMNS) | {"package": pkg_name}


def prop_meta_for(cls, supers, props_meta):
    """A lookup from property name to (kind, friendly_type) that follows the class's super chain."""
    def meta_of(name):
        cur, seen = cls, set()
        while cur and cur not in seen:
            seen.add(cur)
            if name in props_meta.get(cur, {}):
                return props_meta[cur][name]
            cur = supers.get(cur)
        return None
    return meta_of


def object_refs(pkg, props, buf, meta_of, ctx, container_index):
    """Export indexes this block references that are *subobjects* of the class (their outer is the class).

    An `editinline` property or array points at an export whose Outer is the class being described, and the
    compiler expects such a reference to be declared by a `Begin Object Class=.. Name=..` block in the same
    `defaultproperties`. 271 scalar properties and 998 array elements are this shape.
    """
    out = []

    def add(ref):
        if not ref or ref <= 0:
            return
        try:
            exp = pkg.exports[ref - 1]
        except IndexError:
            return
        if exp["outer"] == container_index:
            out.append(ref)

    for p in props:
        if p["type"] in ("Object", "Class"):
            add(p.get("ref"))
        elif p["type"] == "Array":
            inner = inner_type(meta_of(p["name"]))
            if not inner or inner.lower() not in ctx["classes"] or inner in STRUCT_LAYOUT:
                continue
            start = p.get("value_offset")
            if start is None:
                continue
            try:
                count, off = ps.read_index(buf, start)
                for _i in range(max(0, min(count, 4096))):
                    idx, off = ps.read_index(buf, off)
                    add(idx)
            except IndexError:
                continue
    return out


def render_subobject(pkg, ref, supers, declared_all, props_meta, ctx, tot, seen, depth=0):
    """Render one subobject export as a `Begin Object` block; returns its lines or None."""
    exp = pkg.exports[ref - 1]
    cls = pkg.class_name_of(exp)
    name = pkg.name(exp["name"])
    key = (cls, name)
    if key in seen:
        return None
    seen.add(key)
    if depth > 1:
        return None
    got, _reason = block_for_export(pkg, exp, cls, supers, declared_all)
    tot["inline_objects"] += 1
    lines = ["Begin Object Class=%s Name=%s" % (cls, name)]
    if got is not None:
        props, start, clean = got
        if clean:
            meta_of = prop_meta_for(cls, supers, props_meta)
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            buf = data[start:]
            for prop in props:
                tot["inline_properties"] += 1
                text, note = render_value(pkg, prop, meta_of(prop["name"]), buf, ctx)
                if text is None:
                    lines.append("// unrendered %s (%s)" % (prop["name"], note))
                    continue
                tot["inline_rendered"] += 1
                if isinstance(text, list):
                    for index, value in text:
                        lines.append("%s(%d)=%s" % (prop["name"], index, value))
                    continue
                lines.append("%s=%s" % (prop["name"], text))
    lines.append("End Object")
    return lines


def class_set(path=None):
    """Every class name in the tree, lowercased: an array's inner type may name a class (`array<Sound>`)."""
    path = path or os.path.join(ROOT, "inventory", "classes.csv")
    out = set()
    if os.path.isfile(path):
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                out.add(r["class"].lower())
    out.add("object")
    return out


def render_class(pkg, cls, exp, supers, declared_all, props_meta, ctx, row, notes):
    """Render one class's block into lines; adds to `row`/`notes`.

    Returns (lines, status) where status is "clean" (every property name declared by the class: emit it),
    "dirty" (a candidate block the class does not account for: withhold and count it) or "none".
    """
    got, _reason = block_of(pkg, cls, supers, declared_all, want_score=True)
    if got is None:
        return None, "none"
    props, start, clean = got
    if not clean:
        # A block whose property names the class does not all declare is a parse that fits the bytes rather
        # than a block. 532 of the 2,084 candidates are like this and they hold almost every odd type (an
        # `Invalid` value, a 16-byte `Float`, a `Vector` of 139 bytes), so their contents are not emitted.
        row["dirty_properties"] += len(props)
        return None, "dirty"
    meta_of = prop_meta_for(cls, supers, props_meta)
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    buf = data[start:]
    lines = []
    for p in props:
        row["properties"] += 1
        meta = meta_of(p["name"])
        text, note = render_value(pkg, p, meta, buf, ctx)
        if isinstance(text, str) and note == "byte":
            row["bytes"] += 1
            enum_name = meta[1].split(".")[-1] if meta and "." in (meta[1] or "") else None
            elems = ctx["enums"].get(enum_name) if enum_name else None
            if elems and 0 <= p["value"] < len(elems):
                text = elems[p["value"]]
                row["bytes_resolved"] += 1
        if text is None:
            key = {"array": "arrays_unrendered", "struct": "structs_unrendered"}.get(note, "other")
            row[key] += 1
            notes[note] += 1
            lines.append("// unrendered %s (%s)" % (p["name"], note))
            continue
        row["rendered"] += 1
        if note == "struct-nested":
            row["nested_structs"] += 1
        key = {"Float": "floats", "Int": "ints", "Bool": "bools", "Name": "names", "Str": "strs",
               "String": "strs", "Object": "objects", "Class": "objects"}.get(p["type"])
        if key is None and note in ("vector", "rotator", "color", "plane"):
            key = "geometry"
        if key:
            row[key] += 1
        if note == "array":
            row["arrays_rendered"] += 1
        if isinstance(text, list):
            # An array renders as a line per element (`Months(0)=January`), which is the form the compiler
            # reads back into the array.
            for index, value in text:
                lines.append("%s(%d)=%s" % (p["name"], index, value))
            row["array_elements"] += len(text)
            continue
        if p.get("array_index"):
            lines.append("%s(%d)=%s" % (p["name"], p["array_index"], text))
        else:
            lines.append("%s=%s" % (p["name"], text))

    # `editinline` references point at exports whose outer is this class, and the compiler requires each of
    # those to be declared by a `Begin Object` block before the assignment that names it.
    refs = object_refs(pkg, props, buf, meta_of, ctx, exp["index"] + 1)
    if refs:
        decls, seen = [], set()
        for ref in refs:
            block = render_subobject(pkg, ref, supers, declared_all, props_meta, ctx, row, seen)
            if block:
                decls.extend(block)
        if decls:
            lines = decls + lines
    return lines, "clean"


def sweep(system_dir, outdir, emit_dir=None, show_class=None, merge_dir=None):
    supers = cd.super_map()
    declared_all = cd.declared_properties()
    props_meta = property_meta()
    enums, enum_conflicts = global_enums(system_dir)
    ctx = {"enums": enums, "classes": class_set()}
    rows, tot, notes = [], collections.Counter(), collections.Counter()
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        classes = [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]
        row = empty_row(pkg_name)
        row["classes"] = len(classes)
        emitted = {}
        for exp in classes:
            cls = pkg.name(exp["name"])
            lines, status = render_class(pkg, cls, exp, supers, declared_all, props_meta, ctx, row, notes)
            if status == "clean":
                row["clean_blocks"] += 1
                emitted[cls] = lines
            elif status == "dirty":
                row["blocks"] += 1
        row["blocks"] += row["clean_blocks"]
        rows.append(row)
        for k, v in row.items():
            if k != "package":
                tot[k] += v
        if merge_dir and emitted:
            for cls, lines in sorted(emitted.items()):
                merge_block(os.path.join(merge_dir, pkg_name, cls + ".uc"), lines, tot)
        if emit_dir and emitted:
            path = os.path.join(emit_dir, pkg_name)
            os.makedirs(path, exist_ok=True)
            for cls, lines in sorted(emitted.items()):
                write_block(os.path.join(path, cls + ".defaultproperties.uc"), lines)
        if show_class and (not show_class[0] or pkg_name.lower() == show_class[0].lower()):
            cls = show_class[1]
            if cls in emitted:
                print("// %s.%s" % (pkg_name, cls))
                print("defaultproperties\n{")
                for line in emitted[cls]:
                    print("\t" + line)
                print("}")
                return rows, tot
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "class_defaults_emit.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
            w.writeheader()
            for row in rows:
                w.writerow(row)
        print("wrote %s" % path)
    if merge_dir:
        print("merged %d blocks into %s; %d classes had no extracted source"
              % (tot["merged"], merge_dir, tot["merge_missing"]))
    print("%d classes, %d blocks (%d clean, %d properties withheld from the rest), %d properties: "
          "%d rendered (%.1f%%), %d array elements and %d nested structs included; %d arrays and %d structs "
          "left unrendered, %d other; %d of %d byte values named from an enum"
          % (tot["classes"], tot["blocks"], tot["clean_blocks"], tot["dirty_properties"], tot["properties"],
             tot["rendered"], 100.0 * tot["rendered"] / tot["properties"] if tot["properties"] else 0.0,
             tot["array_elements"], tot["nested_structs"],
             tot["arrays_unrendered"], tot["structs_unrendered"], tot["other"],
             tot["bytes_resolved"], tot["bytes"]))
    print("%d subobject references rendered as Begin Object blocks (%d properties, %d rendered)"
          % (tot["inline_objects"], tot["inline_properties"], tot["inline_rendered"]))
    print("unrendered by reason: %s; %d enum-name conflicts" % (dict(notes.most_common()), enum_conflicts))
    return rows, tot


BLOCK_HEAD = re.compile(r"(?m)^[ \t]*defaultproperties[ \t]*\r?\n\{")


def merge_block(path, lines, tot):
    """Append — or replace — a class's defaults block in the extracted source tree.

    The tree `make extract-script` writes is one `.uc` per class, so the block goes at the end of that file:
    the position the compiler expects and the place the shipped source puts it. An existing block is replaced,
    so re-running the merge reproduces the tree byte for byte — which is why the cut is made at the *last* head
    match rather than by matching the block's closing brace: a rendered string can contain `}`.
    """
    if not os.path.isfile(path):
        tot["merge_missing"] += 1
        return
    text = open(path, "r", encoding="latin1").read()
    heads = list(BLOCK_HEAD.finditer(text))
    if heads:
        text = text[:heads[-1].start()]
    body = "defaultproperties\n{\n" + "".join("\t%s\n" % line for line in lines) + "}\n"
    with open(path, "w", newline="\n", encoding="latin1") as fh:
        fh.write(text.rstrip() + "\n\n" + body)
    tot["merged"] += 1


def write_block(path, lines):
    with open(path, "w", newline="\n") as fh:
        fh.write("defaultproperties\n{\n")
        for line in lines:
            fh.write("\t%s\n" % line)
        fh.write("}\n")


# ------------------------------------------------------------------------------------------------- verify

def shipped_blocks(pkg):
    """class -> {property[: (index)]: value text} from the defaults block the shipped script still carries."""
    out = {}
    for exp in pkg.exports:
        if pkg.class_name_of(exp) != "Class":
            continue
        text = pst.script_text(pkg, exp)
        if not text:
            continue
        m = re.search(r"defaultproperties[ \t]*\r?\n[ \t]*\{", text)
        if not m:
            continue
        rest = text[m.end():]
        end = re.search(r"\r?\n\}", rest)
        body = rest[:end.start()] if end else rest
        props = {}
        for line in body.splitlines():
            line = re.sub(r"\s*//.*$", "", line.strip())
            if not line or "=" not in line:
                continue
            key, val = line.split("=", 1)
            props[key.strip().replace(" ", "")] = val.strip()
        if props:
            out[pkg.name(exp["name"])] = props
    return out


def normalise(value):
    v = value.strip().rstrip(";")
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    low = v.lower()
    if low in ("true", "false"):
        return low
    try:
        return round(float(v), 6)
    except ValueError:
        return v


def verify(system_dir, show=False):
    """Compare emitted defaults against the blocks the shipped script retains.

    This is the only oracle available for defaults *values*, and it is a weak one: the compiler does not
    normally keep the `defaultproperties` section in `ScriptText`, so two classes out of 2,319 still have it.
    The comparison therefore proves the renderer's shape on a handful of properties and says nothing about
    the rest -- which is why the doc quotes the counts rather than a percentage.
    """
    rows = []
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        shipped = shipped_blocks(pkg)
        if not shipped:
            continue
        supers = cd.super_map()
        declared_all = cd.declared_properties()
        props_meta = property_meta()
        enums, _conflicts = global_enums(system_dir)
        for cls, want in sorted(shipped.items()):
            got, reason = block_of(pkg, cls, supers, declared_all)
            emitted = {}
            if got is not None:
                props, start, _trailer = got
                data = pkg.data[exp_offset(pkg, cls):exp_offset(pkg, cls) + exp_size(pkg, cls)]
                buf = data[start:]
                for p in props:
                    meta = None
                    cur, seen = cls, set()
                    while cur and cur not in seen:
                        seen.add(cur)
                        if p["name"] in props_meta.get(cur, {}):
                            meta = props_meta[cur][p["name"]]
                            break
                        cur = supers.get(cur)
                    text, _note = render_value(pkg, p, meta, buf)
                    if text is not None:
                        key = p["name"] + ("(%d)" % p["array_index"] if p.get("array_index") else "")
                        emitted[key] = text
            match = differ = only = 0
            detail = []
            for key, val in want.items():
                if key in emitted:
                    if normalise(val) == normalise(emitted[key]):
                        match += 1
                    else:
                        differ += 1
                        detail.append((key, val, emitted[key]))
                else:
                    only += 1
            rows.append((fname[:-2], cls, len(want), len(emitted), match, differ, only))
            print("%-12s %-20s shipped %3d  emitted %3d  match %3d  differ %d  shipped-only %3d"
                  % (fname[:-2], cls, len(want), len(emitted), match, differ, only))
            if show:
                for key, a, b in detail:
                    print("      %-24s shipped=%r emitted=%r" % (key, a, b))
    print("verified %d shipped blocks: %d matched, %d differ, %d shipped-only"
          % (len(rows), sum(r[4] for r in rows), sum(r[5] for r in rows), sum(r[6] for r in rows)))
    return rows


def exp_offset(pkg, cls):
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls:
            return e["offset"]
    raise KeyError(cls)


def exp_size(pkg, cls):
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls:
            return e["size"]
    raise KeyError(cls)


# --------------------------------------------------------------------------------------------------- main

def explain(system_dir):
    """Why a class is not emitted, measured rather than guessed.

    Three outcomes exist per class: a block that accounts for itself (emitted), a candidate block that does not
    (withheld), and no candidate at all. This mode reports the residue and the evidence for what it is —
    payload sizes, how many names are unaccounted for, and which names they are — without printing property
    values. It exists because three plausible explanations have been tested and rejected already: the 64-byte
    trailer bound (relaxing it to 4,096 recovers 2 classes), the property metadata (`inventory/class_properties.csv`
    covers all 11,493 class-owned property exports, 0 missing), and payload size (clean payloads are also small —
    median 159 bytes against 112 for withheld).
    """
    supers = cd.super_map()
    declared_all = cd.declared_properties()
    clean_sizes, withheld_sizes, none_sizes = [], [], []
    undeclared = collections.Counter()
    bucket = collections.Counter()
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            decl = set()
            cur, seen = cls, set()
            while cur and cur not in seen:
                seen.add(cur)
                decl |= declared_all.get(cur, set())
                cur = supers.get(cur)
            cands, _data = cd.candidates(pkg, ps, exp)
            best = cd.choose(cands, decl)
            if best is None:
                none_sizes.append(exp["size"])
                bucket["no candidate"] += 1
                continue
            if best[0][0] == 1.0:
                clean_sizes.append(exp["size"])
                bucket["emitted"] += 1
                continue
            withheld_sizes.append(exp["size"])
            bucket["withheld"] += 1
            bad = [n for n in best[3] if n not in decl]
            bucket["withheld with 1 unaccounted name" if len(bad) == 1 else "withheld with 2+"] += 1
            for n in bad:
                undeclared[n] += 1

    def describe(values):
        values = sorted(values)
        if not values:
            return "n=0"
        return ("n=%d, median %d bytes, p90 %d, max %d, under 256 bytes %d"
                % (len(values), statistics.median(values), values[int(len(values) * 0.9)], values[-1],
                   sum(1 for v in values if v < 256)))

    print("outcomes: %s" % dict(bucket))
    print("emitted payloads:   %s" % describe(clean_sizes))
    print("withheld payloads:  %s" % describe(withheld_sizes))
    print("no-candidate:       %s" % describe(none_sizes))
    print("most frequent unaccounted names: %s" % undeclared.most_common(12))
    print("total unaccounted name slots: %d" % sum(undeclared.values()))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", help="the dump's System directory (not needed for --check)")
    ap.add_argument("--sweep", action="store_true", help="measure every package")
    ap.add_argument("-o", "--out", help="directory for class_defaults_emit.csv (usually inventory)")
    ap.add_argument("--emit", metavar="DIR", help="write one .uc snippet per class into DIR ($WORK only)")
    ap.add_argument("--merge", metavar="TREE",
                    help="append each block to TREE/<Pkg>/<Class>.uc (the tree `make extract-script` writes)")
    ap.add_argument("--verify", action="store_true", help="compare against the shipped text blocks")
    ap.add_argument("--check", action="store_true", help="recompute and diff against the committed CSV")
    ap.add_argument("--explain", action="store_true",
                    help="classify the classes that are not emitted, with the evidence for why")
    ap.add_argument("--class", dest="cls", help="limit --show to this class")
    ap.add_argument("--package", dest="pkg", help="the class's package (with --class)")
    ap.add_argument("--show", action="store_true", help="print one class's block (game data: explicit)")
    args = ap.parse_args()
    if args.check and not args.path:
        # Dump-free: the artifact cannot be recomputed here, but it can be checked for the shape this tool writes.
        return checkcsv.report("defaults-emit",
                               checkcsv.shape(os.path.join(ROOT, "inventory", "class_defaults_emit.csv"),
                                              COLUMNS, key="package"),
                               "class_defaults_emit.csv is the shape this tool writes (shape only: needs the dump)")

    if args.check:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sweep(args.path, tmp)
            new = open(os.path.join(tmp, "class_defaults_emit.csv"), "rb").read()
            committed = os.path.join(ROOT, "inventory", "class_defaults_emit.csv")
            old = open(committed, "rb").read()
            if new != old:
                for n, (a, b) in enumerate(zip(new.splitlines(), old.splitlines()), start=1):
                    if a != b:
                        sys.exit("inventory/class_defaults_emit.csv drifted at line %d: %r != %r" % (n, a, b))
                sys.exit("inventory/class_defaults_emit.csv drifted (length %d vs %d)" % (len(new), len(old)))
        print("ok   class-defaults-emit: inventory/class_defaults_emit.csv reproduces")
        return 0
    if args.explain:
        return explain(args.path)
    if args.verify:
        verify(args.path, show=args.show)
        return 0
    if args.emit or args.merge:
        if args.emit:
            os.makedirs(args.emit, exist_ok=True)
        sweep(args.path, None, emit_dir=args.emit, merge_dir=args.merge)
        return 0
    if args.show:
        if not args.cls:
            sys.exit("--show needs --class")
        rows, _tot = sweep(args.path, None, show_class=(args.pkg, args.cls))
        return 0
    sweep(args.path, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
