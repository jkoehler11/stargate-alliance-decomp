#!/usr/bin/env python3
"""Emit `defaultproperties` blocks from the packages for the compile loop (PLAN 3.5, 3.6).

`docs/CLASS_DEFAULTS.md` proved the defaults *block* can be located and its property names read. This tool
takes the next step: decode each value and print it as the UnrealScript a compiler would accept, so the
reconstructed source tree has defaults and not just functions.

    python3 tools/defaults-emit.py DUMP/System --sweep -o inventory     # coverage + per-class artifacts
    python3 tools/defaults-emit.py DUMP/System --emit $WORK/defaults    # one .uc snippet per class
    python3 tools/defaults-emit.py DUMP/System --verify                  # against the shipped text blocks
    python3 tools/defaults-emit.py DUMP/System --reconcile               # the pre-guard chooser's share
    python3 tools/defaults-emit.py DUMP/System --sweep -o inventory --unguarded  # the artifact before the guard
    python3 tools/defaults-emit.py DUMP/System --class SGOrder --package StargateGame --show

Rendering rules, all of them learned from bytes rather than assumed:

* `Str`/`String` is an FString: a length prefix (1 byte, 2 when the high bit is set) *counts the terminating
  NUL*, and the NUL is only stored when the declared size has room for it. Reading the length byte as the
  first character is what made `Description` print as "\\x0cEmpty Order".
* an object reference renders as `Class'Name'` inside the package and `Class'Package.Name'` across one, which
  is the form the game's own T3D writer uses (`Squad=SQUAD_x'SQUAD_x2'` in `docs/T3D.md`) and the form the
  shipped source shows (`ProjectorMaterial=Texture'SG1_wep_tex.wep_p90_torchlens'`). The package of a **class**
  reference is the import's Outer: `class_package` holds the metaclass's package (`Core`) and named a package
  the class is not in, which is what `outer_package` corrects.
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


def outer_package(pkg, ref):
    """The package an imported object lives in: the import's own Outer, not `class_package`.

    `class_package` names the package of the object's **class**, and a class reference's object is a `Class`,
    which lives in `Core`. So every cross-package class reference rendered as `class'Core.SE_...'` -- a package
    the class is not in, and a path a compiler would reject. The import's Outer is the package; a name is taken
    only when that outer object really is one (`Package`), so a nested object answers None. Measured
    2026-11-11: this corrects 83 of the 315 class references the weapon tree sets, and `tools/weapon-links.py`
    is the tool that scores it.
    """
    if ref >= 0:
        return None
    try:
        imp = pkg.imports[-ref - 1]
    except IndexError:
        return None
    outer = struct.unpack("<i", struct.pack("<I", imp["package"]))[0]
    if outer == 0:
        return None
    if outer < 0:
        i, table = -outer - 1, pkg.imports
    else:
        i, table = outer - 1, pkg.exports
    if not 0 <= i < len(table):
        return None
    if outer < 0:
        if pkg.name(table[i]["class"]) != "Package":
            return None
    elif pkg.class_name_of(table[i]) != "Package":
        return None
    return pkg.name(table[i]["name"])


def render_ref(pkg, ref, meta):
    got = ref_name(pkg, ref)
    if got is None:
        return "None"
    cls, package, name = got
    if not cls or cls == "?":
        return None
    object_name = "%s.%s" % (package, name) if package else name
    if meta and meta[0] == "UClassProperty":
        owner = outer_package(pkg, ref) or package
        object_name = "%s.%s" % (owner, name) if owner else name
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


def render_string_array(buf, count_off, end):
    """An inner array with no declaration: `count`, then that many FStrings, tiling the region exactly.

    `inventory/class_properties.csv` covers class-owned properties, so an array that is a *struct member*
    (`SpeechReplacement.MatchWords`) has no inner type here at all. A string array is the one shape such an
    array can be read as without one, and the tiling test is what separates a read from a guess: the count's
    elements must end on the region's last byte, as they do in all sixteen `TextToSpeechAlias.Aliases`
    elements (values are game data and are never printed by this function).
    """
    try:
        count, off = ps.read_index(buf, count_off)
    except IndexError:
        return None, count_off
    if count < 0 or count > 4096:
        return None, count_off
    parts = []
    for i in range(count):
        text, off = render_fstring(buf, off, end)
        if text is None:
            return None, count_off
        parts.append((i, quote_string(text)))
    if off != end:
        return None, count_off
    return parts, off


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
        tiled = True
        for i in range(count):
            text, off = render_element(pkg, buf, off, inner, ctx, end, depth)
            if text is None:
                tiled = False
                break
            parts.append((i, text))
        # The elements have to tile the value region exactly; anything else is a wrong element type, and a
        # wrong element type that happens to stop inside the size is exactly what a guess looks like.
        if tiled and off == end:
            return parts, "array"
        # An array that is a struct member has no inner type here, so the element walk starts it as a tagged
        # struct and the first field -- itself an array -- refuses. The string tiling below is tried only
        # after that refusal, so no block that renders today can change, and it is exact or it is nothing.
        if inner is None:
            alt, _aoff = render_string_array(buf, count_off, end)
            if alt is not None:
                return alt, "array"
        return None, "array"
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

def choose_scored(cands, decl, own=None):
    """`cd.choose` under the named-property floor: (best, None), or (None, reason).

    A candidate whose parse names none of the class's declared properties is not a block. Every one of the
    **196** rows of that shape in `class_defaults_per_class.csv` was walked at every offset of its payload and
    not one offset names a declared property, so what fits the bytes is the tag grammar's permissiveness plus
    the payload-end witness, not a located block (`tools/class-defaults-silent.py`). `tools/class-defaults.py`
    withholds the start there; the renderer has to withhold it from the same classes, or the two artifacts
    count different blocks over the same packages.
    """
    best = cd.choose(cands, decl, own)
    if best is None:
        return None, "no-block"
    if not any(n in decl for n in best[3]):
        return None, "no-declared-name"
    return best, None


def class_declared(cls, supers, declared_all):
    """The names `cls` declares, its ancestors' included (the closure the chooser scores against)."""
    decl, cur, seen = set(), cls, set()
    while cur and cur not in seen:
        seen.add(cur)
        decl |= declared_all.get(cur, set())
        cur = supers.get(cur)
    return decl


def pick_both(pkg, cls, exp, supers, declared_all, limit=None):
    """One candidate scan, both choosers: the guard's pick and the unguarded one.

    `cd.guarded()` refuses a candidate start the record's closing group *covers* -- a record field's bytes
    cannot be the defaults block's first byte -- and offers the group's end (a one-property block the
    two-property floor withholds) plus, past an empty group end, the offsets in `GROUP_TAIL`. The unguarded
    pick is the same `choose_scored()` on the untouched list, which is what this tool located with until the
    guard was adopted; keeping both picks from one scan is what makes the trade checkable per class without
    walking the packages twice.
    """
    decl = class_declared(cls, supers, declared_all)
    own = declared_all.get(cls, set())
    kinds = cd.kinds_for_class(cls)
    cands, data = cd.candidates(pkg, ps, exp, limit=limit, kinds=kinds)
    g_cands, where = cd.guarded(list(cands), data, pkg, decl, kinds, len(pkg.imports or []))
    best_g, reason_g = choose_scored(g_cands, decl, own)
    best_u, reason_u = choose_scored(cands, decl, own)
    return {"data": data, "where": where, "g": best_g, "g_reason": reason_g,
            "u": best_u, "u_reason": reason_u, "g_cands": g_cands, "u_cands": cands}


def block_for_export(pkg, exp, cls, supers, declared_all, limit=None):
    """Locate one export's property block, scored against the properties `cls` declares.

    `block_of` finds a class by name; a subobject is an export whose own name is not a class, so the class it is
    scored against is its *class* (`pkg.class_name_of`), and the same candidate/choose rule applies. The guard
    is a rule about a **`UClass` record's** closing group, and a subobject's payload is a component record, so
    this path stays unguarded: applying the class-record grammar to it would be a guess.
    """
    decl = class_declared(cls, supers, declared_all)
    cands, _data = cd.candidates(pkg, ps, exp, limit=limit, kinds=cd.kinds_for_class(cls))
    best, reason = choose_scored(cands, decl, declared_all.get(cls, set()))
    if best is None:
        return None, reason
    _score, _count, start, _names, bad = best
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    try:
        props, end, reason = ps.read_block(data[start:], pkg, True)
    except Exception:                                   # noqa: BLE001 - a short payload is a skip, not a crash
        return None, "unparsed"
    if reason != "None":
        return None, "unparsed"
    return (props, start, best[0][0] == 1.0 and bad == 0), None


def block_of(pkg, cls, supers, declared_all, limit=None, want_score=False, guard=True):
    """(props, start, trailer) for a class's defaults block, or (None, reason).

    **The guard is taken, and the trade it was held back for is now measured on both sides.** Until this
    change the chooser was `cd.choose()` alone, on the reading that the guard's 313 starts across the corpus
    are one-tag blocks the two-property floor refuses a verdict on -- a policy call, not a defect. The
    measurement that settles it is what the unguarded path writes: on 194 of the 227 moved classes its start
    sits *inside* the record's closing group, so the block it parses is the record's own bytes, and the
    classes whose payload has no other start were withheld for naming no declared property. A start inside a
    committed record field is not a defaults block under any floor. What the guard writes instead is the one
    tag the class declares, ending on the payload's last byte (`inventory/defaults_locator_guard.csv`).
    `guard=False` reproduces the old artifact for the comparison; `--unguarded` is that switch.
    """
    exp = None
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls:
            exp = e
            break
    if exp is None:
        return None, "no-export"
    picks = pick_both(pkg, cls, exp, supers, declared_all, limit=limit)
    best, reason = (picks["g"], picks["g_reason"]) if guard else (picks["u"], picks["u_reason"])
    if best is None:
        return None, reason
    _score, _count, start, _names, bad = best
    data = picks["data"]
    try:
        props, end, reason = ps.read_block(data[start:], pkg, True)
    except Exception:                                   # noqa: BLE001 - a short payload is a skip, not a crash
        return None, "unparsed"
    if reason != "None":
        return None, "unparsed"
    clean = best[0][0] == 1.0 and bad == 0
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


def render_subobject(pkg, ref, supers, declared_all, props_meta, ctx, tot, seen, depth=0,
                     container_index=None, queue=None):
    """Render one subobject export as a `Begin Object` block; returns its lines or None.

    `queue`, when given, receives the exports *this* block points at with the same Outer as the class
    (`container_index`) at `depth + 1`. Without it a page's panel is emitted and the buttons the panel's own
    `MyComponents` array names are dropped: the widget is a subobject of the *page* (its Outer is the page
    class), so it is neither in the panel's file nor in the class-level ref list, and every reader downstream
    sees a binding with no block behind it.
    """
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
            if queue is not None and container_index is not None and depth == 0:
                inner = object_refs(pkg, props, buf, meta_of, ctx, container_index)
                have = set(seen) | {(pkg.class_name_of(pkg.exports[r - 1]),
                                     pkg.name(pkg.exports[r - 1]["name"])) for r in queue if r[1] == depth + 1}
                for r in inner:
                    iexp = pkg.exports[r - 1]
                    if (pkg.class_name_of(iexp), pkg.name(iexp["name"])) in have:
                        continue
                    queue.append((r, depth + 1))
                    have.add((pkg.class_name_of(iexp), pkg.name(iexp["name"])))
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


def render_class(pkg, cls, exp, supers, declared_all, props_meta, ctx, row, notes, guard=True):
    """Render one class's block into lines; adds to `row`/`notes`, returns (lines, status, record).

    Returns status "clean" (every property name declared by the class: emit it), "dirty" (a candidate block
    the class does not account for: withhold and count it), "no-declared-name" (the chosen parse names none
    of the class's properties, so there is no block: withhold it the same way `tools/class-defaults.py` does)
    or "none". `record` is the row `class_defaults_emit_classes.csv` carries: status, start, the start's
    origin in the guard's candidate set, the tag count, whether the block is a single tag, and where the
    unguarded chooser put its start -- both picks come from one scan (`pick_both`).
    """
    picks = pick_both(pkg, cls, exp, supers, declared_all)
    best, reason = (picks["g"], picks["g_reason"]) if guard else (picks["u"], picks["u_reason"])
    u_start = picks["u"][2] if picks["u"] else ""
    if best is None:
        status = "no-declared-name" if reason == "no-declared-name" else "none"
        return None, status, [status, "", "", 0, 0, u_start]
    _score, count, start, _names, bad = best
    clean = _score[0] == 1.0 and bad == 0
    record = ["clean" if clean else "dirty", start, picks["where"].get(start, "candidate"), count,
              1 if count == 1 else 0, u_start]
    if not clean:
        # A block whose property names the class does not all declare is a parse that fits the bytes rather
        # than a block. 532 of the 2,084 candidates are like this and they hold almost every odd type (an
        # `Invalid` value, a 16-byte `Float`, a `Vector` of 139 bytes), so their contents are not emitted.
        row["dirty_properties"] += count
        return None, "dirty", record
    data = picks["data"]
    buf = data[start:]
    try:
        props, _end, why = ps.read_block(buf, pkg, True)
    except Exception:                                   # noqa: BLE001 - a short payload is a skip
        return None, "none", record
    if why != "None":
        return None, "none", record
    meta_of = prop_meta_for(cls, supers, props_meta)
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
    # those to be declared by a `Begin Object` block before the assignment that names it. A subobject's own
    # block can name more of them (a page's panel names its buttons through `MyComponents`), so the walk is a
    # queue over both levels rather than one pass over the class-level list.
    refs = object_refs(pkg, props, buf, meta_of, ctx, exp["index"] + 1)
    if refs:
        decls, seen, queue = [], set(), [(r, 0) for r in refs]
        while queue:
            ref, depth = queue.pop(0)
            block = render_subobject(pkg, ref, supers, declared_all, props_meta, ctx, row, seen,
                                     depth=depth, container_index=exp["index"] + 1, queue=queue)
            if block:
                decls.extend(block)
        if decls:
            lines = decls + lines
    return lines, "clean", record


CLASS_COLUMNS = ["package", "class", "status", "start", "origin", "properties", "one_tag",
                 "unguarded_start"]


def sweep(system_dir, outdir, emit_dir=None, show_class=None, merge_dir=None, guard=True):
    supers = cd.super_map()
    declared_all = cd.declared_properties()
    props_meta = property_meta()
    enums, enum_conflicts = global_enums(system_dir)
    ctx = {"enums": enums, "classes": class_set()}
    rows, tot, notes, choices = [], collections.Counter(), collections.Counter(), []
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
            lines, status, record = render_class(pkg, cls, exp, supers, declared_all, props_meta, ctx,
                                                 row, notes, guard=guard)
            tot["status_" + status] += 1
            choices.append([pkg_name, cls] + record)
            if status == "clean":
                row["clean_blocks"] += 1
                emitted[cls] = lines
            elif status == "dirty":
                row["blocks"] += 1
            elif status == "no-declared-name":
                tot["no_declared_name"] += 1
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
        path = os.path.join(outdir, "class_defaults_emit_classes.csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(CLASS_COLUMNS)
            w.writerows(choices)
        print("wrote %s (%d classes)" % (path, len(choices)))
    if merge_dir:
        print("merged %d blocks into %s; %d classes had no extracted source"
              % (tot["merged"], merge_dir, tot["merge_missing"]))
    print("%d classes, %d blocks (%d clean, %d withheld for naming no declared property, %d properties "
          "withheld from the rest), %d properties: "
          "%d rendered (%.1f%%), %d array elements and %d nested structs included; %d arrays and %d structs "
          "left unrendered, %d other; %d of %d byte values named from an enum"
          % (tot["classes"], tot["blocks"], tot["clean_blocks"], tot["no_declared_name"], tot["dirty_properties"],
             tot["properties"],
             tot["rendered"], 100.0 * tot["rendered"] / tot["properties"] if tot["properties"] else 0.0,
             tot["array_elements"], tot["nested_structs"],
             tot["arrays_unrendered"], tot["structs_unrendered"], tot["other"],
             tot["bytes_resolved"], tot["bytes"]))
    print("%d subobject references rendered as Begin Object blocks (%d properties, %d rendered)"
          % (tot["inline_objects"], tot["inline_properties"], tot["inline_rendered"]))
    origins = collections.Counter(c[4] for c in choices if c[4])
    one_tag = sum(1 for c in choices if c[6] == 1)
    moved = sum(1 for c in choices if c[7] != "" and c[3] != c[7])
    print("start origin: %s; %d one-tag block(s); %d start(s) moved off the unguarded chooser"
          % (", ".join("%s %d" % (k, v) for k, v in origins.most_common()), one_tag, moved))
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
    """class -> ({property[: (index)]: value text}, inside_a_block_comment) from the shipped script.

    The second half matters: a `/* ... */` block that holds a class's body leaves the whole buffer looking
    like source, and both blocks this build ships are inside one (`docs/PACKAGE_SCRIPT_TEXT.md`, *The text
    carries a commented-out revision of some classes*). A block that is commented out was not compiled, so
    it is a revision of the class and not this build's defaults -- which is why the status is printed beside
    every row rather than assumed.
    """
    out = {}
    for exp in pkg.exports:
        if pkg.class_name_of(exp) != "Class":
            continue
        text = pst.script_text(pkg, exp)
        if not text:
            continue
        m = re.search(r"_?defaultproperties[ \t]*\r?\n[ \t]*\{", text)
        if not m:
            continue
        # the comment state at the block's own offset: last `/*` after last `*/` means it is inside one
        head = text[:m.start()]
        in_comment = head.rfind("/*") > head.rfind("*/")
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
            out[pkg.name(exp["name"])] = (props, in_comment)
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
        for cls, (want, in_comment) in sorted(shipped.items()):
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
            rows.append((fname[:-2], cls, len(want), len(emitted), match, differ, only, in_comment))
            print("%-12s %-20s shipped %3d  emitted %3d  match %3d  differ %d  shipped-only %3d  in-comment=%s"
                  % (fname[:-2], cls, len(want), len(emitted), match, differ, only,
                     "yes" if in_comment else "no"))
            if show:
                for key, a, b in detail:
                    print("      %-24s shipped=%r emitted=%r" % (key, a, b))
    print("verified %d shipped blocks: %d matched, %d differ, %d shipped-only, %d inside a block comment"
          % (len(rows), sum(r[4] for r in rows), sum(r[5] for r in rows), sum(r[6] for r in rows),
             sum(1 for r in rows if r[7])))
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

def describe_parse(pkg, best, data, decl, kinds):
    """(start, tags, declared, undeclared, one_byte, trailer, first_name, first_kind, first_type,
    declared_name, declared_kind, declared_type, declared_ok) for one parse.

    `declared_ok` is 0 where the tag that names a declared property carries a type its declared kind forbids --
    the `contradictions()` test -- and 1 where the kind is unconstrained (`UDelegateProperty`) or the type
    agrees. It is empty when no tag names a declared property, because there is no kind to test against.
    """
    empty = ("", 0, 0, 0, 0, "", "", "", "", "", "", "", "")
    if best is None:
        return empty
    _score, count, start, _names, _bad = best
    try:
        props, end, why = ps.read_block(data[start:], pkg, True)
    except Exception:                                   # noqa: BLE001 - a short payload is a skip, not a crash
        return (start, count, 0, 0, 0, "", "", "", "", "", "", "", "")
    if why != "None":
        return (start, count, 0, 0, 0, "", "", "", "", "", "", "", "")
    declared = [p for p in props if p["name"] in decl]
    undecl = [p for p in props if p["name"] not in decl]
    one = [p for p in undecl if p.get("size") == 1]
    first = props[0]
    dname = dkind = dtype = dok = ""
    if declared:
        dname, dtype = declared[0]["name"], declared[0]["type"]
        dkind = kinds.get(dname) or ""
        allowed = cd.TYPE_AGREEMENT.get(kinds.get(dname))
        dok = 1 if allowed is None or dtype in allowed else 0
    return (start, len(props), len(declared), len(undecl), len(one), len(data) - start - end,
            first["name"], kinds.get(first["name"]) or "", first["type"], dname, dkind, dtype, dok)


def explain(system_dir, outdir=None):
    """Why a class is not emitted, measured rather than guessed -- one row per withheld class.

    Three outcomes exist per class: a block that accounts for itself (emitted), a candidate block that does not
    (withheld), and no candidate at all. The earlier version of this mode printed a summary and described the
    payload sizes only, and it read the *unguarded* candidate list -- so its buckets did not reconcile with the
    ledger it explains. This one walks `pick_both`'s guarded set, the same pick `render_class` takes, and writes
    a row per withheld class: the reason, which parse the row describes, and the tag shape that settles it.

    `parse` says whose reading the shape comes from: `chosen` (the pick the ledger withheld), `candidate` (the
    floor refused every candidate, so the row describes the best parse the guard's set holds), or `none`.
    """
    supers = cd.super_map()
    declared_all = cd.declared_properties()
    rows, bucket, shapes = [], collections.Counter(), collections.Counter()
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            decl = class_declared(cls, supers, declared_all)
            own = declared_all.get(cls, set())
            kinds = cd.kinds_for_class(cls)
            picks = pick_both(pkg, cls, exp, supers, declared_all)
            best, reason = picks["g"], picks["g_reason"]
            if best is not None:
                status = "clean" if (best[0][0] == 1.0 and best[4] == 0) else "dirty"
            else:
                status = "no-declared-name" if reason == "no-declared-name" else "none"
            if status == "clean":
                bucket["clean"] += 1
                continue
            if best is not None:
                parse = "chosen"
                desc = describe_parse(pkg, best, picks["data"], decl, kinds)
            else:
                alt = cd.choose(picks["g_cands"], decl, own)
                parse = "candidate"
                if alt is None:
                    alt = cd.choose(picks["u_cands"], decl, own)
                    parse = "unguarded"
                if alt is None:
                    parse = "none"
                desc = describe_parse(pkg, alt, picks["data"], decl, kinds)
            why = {"dirty": "partial", "no-declared-name": "names-no-declared-property"}.get(status,
                                                                                            "no-candidate")
            bucket[(status, why)] += 1
            shapes[(why, parse, desc[1], desc[2], desc[3], desc[4])] += 1
            rows.append([pkg_name, cls, status, why, parse, exp["size"]] + list(desc))
    rows.sort(key=lambda r: (r[0], r[1]))
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "class_defaults_withheld.csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(EXPLAIN_COLUMNS)
            w.writerows(rows)
        print("wrote %s (%d classes)" % (path, len(rows)))
    print("outcomes: %s" % dict(bucket))
    print("%d classes withheld; shapes (reason, parse, tags, declared, undeclared, one_byte):" % len(rows))
    for k, v in shapes.most_common(12):
        print("   %5d  %s" % (v, k))
    partial = [r for r in rows if r[3] == "partial"]
    if partial:
        print("the %d rows that name a declared property:" % len(partial))
        for r in partial:
            print("   %-16s %-28s tags=%d declared=%d undeclared=%d one_byte=%d trailer=%s "
                  "declared=%s (%s/%s ok=%s) first=%s (%s/%s)"
                  % (r[0], r[1], r[7], r[8], r[9], r[10], r[11], r[15], r[16], r[17], r[18], r[12], r[13],
                     r[14]))
    return rows, bucket


def reconcile(system_dir):
    """This tool's two choosers against the locator, per class export.

    `tools/class-defaults.py` chooses from `guarded()`'s candidates and this tool now does the same, so the
    adopted path's residue against that artifact should be zero -- `cross_check()` gates exactly that on the
    committed CSVs. What this mode adds is the other side: the pre-guard chooser, class by class, which is the
    trade the adoption settled (the blocks it loses, the ones it held on the record's own bytes).
    """
    supers = cd.super_map()
    declared_all = cd.declared_properties()
    cats = collections.Counter()
    agree_start = 0
    gained, lost = [], []
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            decl = class_declared(cls, supers, declared_all)
            kinds = cd.kinds_for_class(cls)
            cands, data = cd.candidates(pkg, ps, exp, kinds=kinds)
            best_u, reason_u = choose_scored(cands, decl, declared_all.get(cls, set()))
            guarded, _where = cd.guarded(list(cands), data, pkg, decl, kinds, len(pkg.imports or []))
            best_g, _reason_g = choose_scored(guarded, decl, declared_all.get(cls, set()))
            here = "block" if best_u else ("no-declared-name" if reason_u == "no-declared-name" else "no-candidate")
            there = "no-block" if best_g is None else (
                "block" if any(n in decl for n in best_g[3]) else "no-declared-name")
            cats[(here, there)] += 1
            if best_u and best_g and best_u[2] == best_g[2]:
                agree_start += 1
            if there == "block" and here != "block":
                # A block the locator settles and the pre-guard chooser did not: `clean` is the same verdict
                # `render_class` publishes, so one that is not clean is withheld by the adopted path too.
                gained.append((best_g[0][0] == 1.0 and best_g[4] == 0, best_g[1]))
            if here == "block" and there != "block":
                lost.append(best_u[1])
    print("pre-guard chooser / locator, per class export:")
    for (here, there), count in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])):
        print("  pre-guard %-16s locator %-16s %5d" % (here, there, count))
    print("start identical on both paths: %d" % agree_start)
    print("blocks: pre-guard %d, locator %d (%+d)"
          % (sum(v for (h, _t), v in cats.items() if h == "block"),
             sum(v for (_h, t), v in cats.items() if t == "block"),
             sum(v for (_h, t), v in cats.items() if t == "block")
             - sum(v for (h, _t), v in cats.items() if h == "block")))
    if gained:
        print("the guard adds %d blocks: %d of them fully declared, %d properties in all, %d in the clean ones"
              % (len(gained), sum(1 for clean, _n in gained if clean), sum(n for _c, n in gained),
                 sum(n for clean, n in gained if clean)))
    if lost:
        print("the guard removes %d block(s): %d properties" % (len(lost), sum(lost)))
    return 0


def cross_check(outdir=None):
    """The emitter's chosen starts against the locator's, class for class -- dump-free.

    `inventory/class_defaults_per_class.csv` is what `tools/class-defaults.py` publishes, and since the guard
    was adopted on both sides the two artifacts must agree on **every** class: same start where the locator
    publishes one, no start where it publishes none. That equality is the whole point of the change (the
    renderer was 85 blocks short of the locator before it), so it is a gate and not a note. It also reads
    without the packages, which is what `make ci` can do.
    """
    outdir = outdir or os.path.join(ROOT, "inventory")
    mine = {}
    with open(os.path.join(outdir, "class_defaults_emit_classes.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            mine[(r["package"], r["class"])] = r
    problems, checked, moved = [], 0, 0
    with open(os.path.join(outdir, "class_defaults_per_class.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            key = (r["package"], r["class"])
            row = mine.get(key)
            if row is None:
                problems.append("%s.%s is in the locator and not in the emitter's classes" % key)
                continue
            checked += 1
            if (r["block_start"] or "") != (row["start"] or ""):
                problems.append("%s.%s: locator start %r, emitter start %r"
                                % (key[0], key[1], r["block_start"], row["start"]))
            u = row["unguarded_start"] or ""
            if u and u != (row["start"] or ""):
                moved += 1
    if problems:
        print("FAIL class-defaults-emit: %d disagreement(s) between the emitter and the locator, e.g. %s"
              % (len(problems), "; ".join(problems[:4])))
        return 1
    print("ok   class-defaults-emit: %d classes, the emitter's start is the locator's on every one "
          "(%d moved off the unguarded start)" % (checked, moved))
    return 0


EXPLAIN_COLUMNS = ["package", "class", "status", "reason", "parse", "payload_size", "start", "tags",
                   "declared", "undeclared", "one_byte", "trailer", "first_name", "first_kind", "first_type",
                   "declared_name", "declared_kind", "declared_type", "declared_ok"]

REASON_TEXT = {
    "partial": "one tag names a declared property and the rest name nothing the class's chain declares",
    "names-no-declared-property": "the best parse names none of the class's properties",
    "no-candidate": "no offset parses two tags to the payload's last byte",
}

DOC = os.path.join(ROOT, "docs", "CLASS_DEFAULTS.md")
DOC_START = "<!-- generated:defaults-withheld -->"
DOC_END = "<!-- /generated:defaults-withheld -->"


def withheld_rows(path=None):
    path = path or os.path.join(ROOT, "inventory", "class_defaults_withheld.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def render_withheld():
    """The page block, from the committed artifact: the reason census and the rows that name a property."""
    rows = withheld_rows()
    reasons = collections.Counter(r["reason"] for r in rows)
    parses = collections.defaultdict(collections.Counter)
    for r in rows:
        parses[r["reason"]][r["parse"]] += 1
    lines = ["| reason | classes | parse the row describes | what it says |", "|---|---:|---|---|"]
    for reason in ("partial", "names-no-declared-property", "no-candidate"):
        if not reasons.get(reason):
            continue
        got = ", ".join("%s %d" % (k, v) for k, v in sorted(parses[reason].items()))
        lines.append("| `%s` | %d | %s | %s |" % (reason, reasons[reason], got, REASON_TEXT[reason]))
    partial = [r for r in rows if r["reason"] == "partial"]
    if partial:
        lines += ["", "| class | tags | declared | undeclared | one-byte undeclared | trailer | "
                      "the declared tag | its kind | its type | type agrees | the first tag | its type |",
                  "|---|---:|---:|---:|---:|---:|---|---|---|---|---|---|"]
        for r in partial:
            agree = {"1": "yes", "0": "**no**"}.get(r["declared_ok"], "-")
            lines.append("| `%s.%s` | %s | %s | %s | %s | %s | `%s` | %s | %s | %s | `%s` | %s |"
                         % (r["package"], r["class"], r["tags"], r["declared"], r["undeclared"],
                            r["one_byte"], r["trailer"] or "0", r["declared_name"],
                            r["declared_kind"] or "-", r["declared_type"], agree, r["first_name"],
                            r["first_type"]))
    one_byte = sum(1 for r in rows if r["undeclared"] != "0" and r["one_byte"] == r["undeclared"])
    no_parse = sum(1 for r in rows if r["parse"] == "none")
    unguarded = sum(1 for r in rows if r["parse"] == "unguarded")
    lines += ["", "| reading | classes |", "|---|---:|",
              "| every tag in the parse is one byte wide | %d |" % one_byte,
              "| no parse at all, on either candidate list | %d |" % no_parse,
              "| a parse only the unguarded list holds | %d |" % unguarded]
    per = os.path.join(ROOT, "inventory", "class_defaults_per_class.csv")
    with open(per, newline="", encoding="utf-8") as fh:
        no_block = {(r["package"], r["class"]) for r in csv.DictReader(fh) if r["defaults"] == "0"}
    here = {(r["package"], r["class"]) for r in rows}
    lines += ["", "%s withheld: the locator's %s classes with no block (`inventory/class_defaults_per_class.csv`) "
                  "plus the %d whose parse names a declared property, and every class with no block is here -- "
                  "`inventory/class_defaults_withheld.csv`, written by `python3 tools/defaults-emit.py --explain "
                  "-o inventory` and rendered here by `--explain-doc`."
              % ("{:,}".format(len(rows)), "{:,}".format(len(no_block)), len(here - no_block))]
    return "\n".join(lines)


def splice_doc(text, block, path=DOC):
    if DOC_START not in text or DOC_END not in text:
        raise ValueError("%s has no %s ... %s markers" % (path, DOC_START, DOC_END))
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def write_doc(path=DOC):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(splice_doc(text, render_withheld(), path))
    print("wrote %s" % path)
    return 0


def doc_check(path=DOC):
    problems = []
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        want = splice_doc(text, render_withheld(), path)
    except ValueError as exc:
        problems.append(str(exc))
        want = text
    if want != text:
        problems.append("the block does not match inventory/class_defaults_withheld.csv; run `--explain-doc`")
    if problems:
        print("FAIL defaults-emit: " + "; ".join(problems[:4]))
        return 1
    print("ok   defaults-emit: docs/CLASS_DEFAULTS.md's withheld block matches the artifact (%d classes)"
          % len(withheld_rows()))
    return 0


def withheld_check():
    """Dump-free: the withheld ledger's shape, and that it holds exactly the classes the emitter did not emit."""
    problems = checkcsv.shape(os.path.join(ROOT, "inventory", "class_defaults_withheld.csv"), EXPLAIN_COLUMNS,
                              key="class")
    if problems:
        print("FAIL defaults-emit: " + "; ".join(problems[:5]))
        return 1
    rows = withheld_rows()
    classes = []
    with open(os.path.join(ROOT, "inventory", "class_defaults_emit_classes.csv"), newline="",
              encoding="utf-8") as fh:
        classes = list(csv.DictReader(fh))
    want = {(r["package"], r["class"]): r["status"] for r in classes if r["status"] != "clean"}
    got = {(r["package"], r["class"]): r["status"] for r in rows}
    if set(want) != set(got):
        extra = sorted(set(got) - set(want))[:2]
        missing = sorted(set(want) - set(got))[:2]
        print("FAIL defaults-emit: the withheld ledger and the emitter's ledger name different classes "
              "(%d vs %d; extra %s, missing %s)" % (len(got), len(want), extra, missing))
        return 1
    disagreed = [k for k in want if got[k] != want[k]]
    if disagreed:
        print("FAIL defaults-emit: %d class(es) carry a different status in the two ledgers, first %s.%s"
              % (len(disagreed), disagreed[0][0], disagreed[0][1]))
        return 1
    if any(r["reason"] not in REASON_TEXT for r in rows):
        print("FAIL defaults-emit: a withheld row carries a reason the page does not explain")
        return 1
    return 0


def check_dump_free():
    """`--check` without the dump: the artifacts' shape, the two ledgers' population, and their agreement."""
    problems = checkcsv.shape(os.path.join(ROOT, "inventory", "class_defaults_emit.csv"), COLUMNS,
                              key="package")
    problems += checkcsv.shape(os.path.join(ROOT, "inventory", "class_defaults_emit_classes.csv"),
                               CLASS_COLUMNS, key="class")
    if problems:
        print("FAIL defaults-emit: " + "; ".join(problems[:5]))
        return 1
    if withheld_check():
        return 1
    return cross_check()


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
    ap.add_argument("--unguarded", action="store_true",
                    help="locate with the pre-guard chooser (reproduces the artifact before the guard was taken)")
    ap.add_argument("--explain", action="store_true",
                    help="classify the classes that are not emitted, with the evidence for why "
                         "(`-o inventory` writes inventory/class_defaults_withheld.csv)")
    ap.add_argument("--explain-doc", action="store_true",
                    help="render docs/CLASS_DEFAULTS.md's withheld block from the artifact")
    ap.add_argument("--check-doc", action="store_true", help="check that block instead of writing it")
    ap.add_argument("--reconcile", action="store_true",
                    help="cross-tab this tool's block per class against tools/class-defaults.py (the guard's share)")
    ap.add_argument("--class", dest="cls", help="limit --show to this class")
    ap.add_argument("--package", dest="pkg", help="the class's package (with --class)")
    ap.add_argument("--show", action="store_true", help="print one class's block (game data: explicit)")
    args = ap.parse_args()
    guard = not args.unguarded
    if args.check and not args.path:
        # Dump-free: the artifacts cannot be recomputed here, but their shape and their agreement can be checked.
        return check_dump_free()

    if args.check:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sweep(args.path, tmp, guard=guard)
            explain(args.path, tmp)
            for name in ("class_defaults_emit.csv", "class_defaults_emit_classes.csv",
                         "class_defaults_withheld.csv"):
                new = open(os.path.join(tmp, name), "rb").read()
                committed = os.path.join(ROOT, "inventory", name)
                old = open(committed, "rb").read()
                if new != old:
                    for n, (a, b) in enumerate(zip(new.splitlines(), old.splitlines()), start=1):
                        if a != b:
                            sys.exit("inventory/%s drifted at line %d: %r != %r" % (name, n, a, b))
                    sys.exit("inventory/%s drifted (length %d vs %d)" % (name, len(new), len(old)))
        print("ok   class-defaults-emit: inventory/class_defaults_emit.csv, "
              "class_defaults_emit_classes.csv and class_defaults_withheld.csv reproduce")
        return 0
    if args.explain_doc:
        return doc_check() if args.check_doc else write_doc()
    if args.explain:
        explain(args.path, args.out)
        return 0
    if args.reconcile:
        return reconcile(args.path)
    if args.verify:
        verify(args.path, show=args.show)
        return 0
    if args.emit or args.merge:
        if args.emit:
            os.makedirs(args.emit, exist_ok=True)
        sweep(args.path, None, emit_dir=args.emit, merge_dir=args.merge, guard=guard)
        return 0
    if args.show:
        if not args.cls:
            sys.exit("--show needs --class")
        rows, _tot = sweep(args.path, None, show_class=(args.pkg, args.cls), guard=guard)
        return 0
    sweep(args.path, args.out, guard=guard)
    return 0


if __name__ == "__main__":
    sys.exit(main())
