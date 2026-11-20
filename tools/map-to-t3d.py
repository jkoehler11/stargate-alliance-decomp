#!/usr/bin/env python3
"""Write a map's actors out as T3D (PLAN 5.2).

The parsing is done (`docs/ACTOR_PROPERTIES.md`): a `.unr`'s actors are exports, and 62% of exports — every
actor — carry a readable property block. This is the formatting step: one `Begin Actor` … `End Actor` section
per actor, with the properties written the way the editor writes them.

    python3 tools/map-to-t3d.py DUMP/Maps/M02A.unr --out $WORK/extracted/maps
    python3 tools/map-to-t3d.py DUMP/Maps --out $WORK/extracted/maps --stats inventory/t3d_coverage.csv

**The writer uses the reader's locator** (2026-11-19). `locate()` used to carry a rule of its own -- the start
whose block spends every object reference, then the most properties. `prop_stream.find_block` now runs a
stronger test (every checkable value consumes exactly its declared `Size`), and the writer delegates to it with
the prefix pinned to 64 bytes: no map actor's block starts later than 32 bytes in, while the only exports a
wider look finds are `StaticMeshInstance`s whose payload is a packed native record. The block starts move on **4,133 of
121,394** exports, properties fall by **6,511** (an invented tag is not a property), the struct census drops
from **79 names to 12** -- 11 the classes declare plus one named residue -- and file size rises 0.7% because the
properties that survive carry values where the invented ones did not.

**An array is its elements** (2026-11-19). An `Array` property's payload is a compact count followed by exactly
that many compact indices when its elements are object references — the rule `tools/action-wiring.py` measured
on `Actions`, now applied to every array. A payload that does not consume itself exactly, or whose indices do
not land in the package's export/import tables, is *not* read as objects: a guess that happens to consume its
bytes would print invented actor names, so it is counted as a refusal instead. Arrays that are not object
arrays are counted with their count and element width (`rest / count`, when it divides) and left as a comment:
an array's element type lives in the class's property declaration, which this reader does not carry.

**A struct array's element is a tagged block** (2026-11-19). The array that "divides by its count" was left
as a comment because a struct's width is not a fact about its name; the width is a fact about the *element*,
and the element is a tagged property block like every other struct value (`decode_struct_elements`). It
replaces the element-layout deferral: the walk reads each element's own field names and its own end, and it is
the struct rule (`struct_fields`) applied to each element rather than a second rule. Every declared struct
array in these 73 maps decodes, including the 46 the width rule could not reach, so no array payload is left
unread.

**Two native records the page deferred, read from the class's own serialiser** (2026-11-19). A map's `Level`
export is not an actor and has no property block; its payload is `[0x00][count][count][count x compact export
indices][trailer]`, i.e. **the level's actor list**, and the writer now cross-checks its own output against it.
The `StaticMeshInstance` payload's tail is `[dword][compact count][that many records]`, the record list
`UStaticMeshInstance::Serialize` (`0x0024b5f0`) writes after the colour table; it is a count, not a field past
a head. Nothing here is a guess about the bytes: both frames come from the functions that write them.

**The palette records name lights, and the terrain actor was never unwritten** (2026-11-19). The
`StaticMeshInstance` tail's records were 97.6% parsed; the 790 that were not are records whose trailing dword is
**0** rather than 1, and the grammar closes on all 33,509 payloads once both are allowed. The record is
`[compact export index][compact length][that many bytes][dword 0 or 1]`, and the index is into the map's own
export table on every one of the 466,858 records -- it names the light actor the record bakes (Light, Sunlight,
Spotlight, Emitter). The `Level` trailer is four `FString`s (`"unreal"`, empty, `"Index.unr"`, empty) and 32
bytes, not "41 to 43 compact values". And a `TerrainInfo`'s payload is `[prefix record][property block][native
terrain record]`: the block was only ever looked for at the export's end, so the 65 actors a level names and no
T3D held are written now, and the `Level` cross-check finds nothing it does not write.

Output is game-derived content: `$WORK` only, never this repository (`.gitignore` keeps `/work` and `/out` out,
and the docs say the same). What is committed is the tool and the counts it prints.
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
VALUES = os.path.normpath(os.path.join(HERE, "..", "inventory", "t3d_values.csv"))
STRUCTS = os.path.normpath(os.path.join(HERE, "..", "inventory", "t3d_structs.csv"))
ARRAYS = os.path.normpath(os.path.join(HERE, "..", "inventory", "t3d_arrays.csv"))
COVERAGE = os.path.normpath(os.path.join(HERE, "..", "inventory", "t3d_coverage.csv"))
CLASSES = os.path.normpath(os.path.join(HERE, "..", "inventory", "classes.csv"))
DECLARED = os.path.normpath(os.path.join(HERE, "..", "inventory", "class_properties.csv"))

# The element types whose width is a fact about the type, so a payload that divides by its count can be decoded:
# bytes per element and the struct format. A name here is a claim about the *type* -- `Vector` is 12 bytes on
# 152,109 properties and 16 on 19 -- so the width test in `decode_elements` is what keeps the claim honest.
ELEMENT = {"int": (4, "<i"), "float": (4, "<f"), "byte": (1, "<B"), "bool": (4, "<i"),
           "Vector": (12, "<3f"), "Rotator": (12, "<3i"), "Color": (4, "<4B")}


def load_declarations(path_classes=CLASSES, path_props=DECLARED):
    """(class -> super) and (class, property) -> declared friendly type, from the two committed class artifacts.

    An array's element type is written down in one place only: the declaration. A map's payload carries values,
    not a declaration, which is why this writer printed a struct array as "N element(s) of W byte(s), element
    type not in the export". `inventory/class_properties.csv` has the declaration on the class that declares it
    and a placed class inherits -- `SpriteEmitter.ColorScale` is `array<ParticleColorScale>` on its parent
    `ParticleEmitter` -- so the lookup walks the super chain in `inventory/classes.csv`.
    """
    parent, types = {}, {}
    with open(path_classes, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            parent.setdefault(r["class"], r["super"])
    with open(path_props, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            types[(r["class"], r["property"])] = r["friendly_type"]
    return parent, types


def declared(parent, types, cls, prop, depth=40):
    """The property's declaration through the super chain: (friendly type, the class that declares it)."""
    seen = 0
    while cls and seen < depth:
        hit = types.get((cls, prop))
        if hit:
            return hit, cls
        cls = parent.get(cls)
        seen += 1
    return "", ""


def load_prop_stream():
    import importlib.util
    path = os.path.join(HERE, "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_prefix_record():
    """`actor-prefix-record.py`, whose `record()` reads the actor's prefix and names where the block starts.

    The writer delegates its start the same way it delegates the value test: the prefix rule is a *positive*
    reading of the bytes in front of the block (`[class index][the index again][8 x 0xff][4 bytes][0x81]`, so
    `2*w + 13`), and measured against this writer's own choice it names a later start on 25,499 of the 79,925
    blocks that carry the record. Those are the blocks whose start sat inside the record's own tail.
    """
    import importlib.util
    path = os.path.join(HERE, "actor-prefix-record.py")
    spec = importlib.util.spec_from_file_location("actor_prefix_record", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fmt(value):
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return "%.6g" % value
    if isinstance(value, int):
        # An `Int`/`Byte` value is a number in the editor's spelling, not a string: the quoted form this used
        # to write (`iLeaf="-1"`) is what a struct's fields made obvious, because they are numbers beside
        # quoted names in the same tuple.
        return str(value)
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


def decode_elements(raw, ps, element_type):
    """An array of a fixed-width element type -> its values, or None when the type's width is not the payload's.

    The width test is the whole guard: `Vector` is 12 bytes in 152,109 places and 16 in 19, so a payload whose
    remainder over its count is not the type's width is left as a typed comment rather than decoded. Nothing here
    guesses at a struct's field layout -- a struct element type is named and left alone, because its width is a
    property of the instance (compact indices inside it), not of the name.
    """
    lay = element_layout(raw, ps)
    if not lay:
        return None
    count, width = lay
    fmt = ELEMENT.get(element_type)
    if not fmt or fmt[0] != width:
        return None
    pat = fmt[1]
    step = struct.calcsize(pat)
    off = len(raw) - count * width
    out = []
    for _ in range(count):
        vals = struct.unpack_from(pat, raw, off)
        off += step
        out.append(vals[0] if len(vals) == 1 else vals)
    return out


def fmt_element(value):
    """One decoded element, in the editor's spelling (`Vector` is `(X=…,Y=…,Z=…,`Rotator` `(Pitch=…)`)."""
    if isinstance(value, tuple) and len(value) == 3:
        if all(isinstance(v, float) for v in value):
            return "(X=%.6g,Y=%.6g,Z=%.6g)" % value
        return "(Pitch=%d,Yaw=%d,Roll=%d)" % value
    if isinstance(value, tuple):
        return "(%s)" % ",".join(str(v) for v in value)
    if isinstance(value, float):
        return "%.6g" % value
    return str(int(value))


def fmt_struct(fields):
    """A struct's tagged fields in the editor's spelling, or `None` when a field has no printable value.

    The field names are the payload's own: the struct's value is a tagged block, so `PointRegion` is
    `(Zone=LevelInfo'LevelInfo0',iLeaf=-1,ZoneNumber=0)` rather than three numbers whose order only the
    class declaration could name. A field this writer cannot print (an array inside a struct, the one
    `InterpCurve` carries) refuses the whole struct rather than printing a hole in the tuple.
    """
    parts = []
    for f in fields:
        name = f.get("name") or "?"
        if f["type"] == "Struct":
            value = f.get("value")
            if isinstance(value, (tuple, list)):
                parts.append("%s=%s" % (name, fmt(value)))
                continue
            sub = f.get("fields")
            inner = fmt_struct(sub) if sub else None
            if inner is None:
                return None
            parts.append("%s=%s" % (name, inner))
        elif f["type"] in ("Array", "FixedArray", "Map", "Invalid", "Delegate"):
            return None
        elif f.get("value") is None and f["type"] not in ("Str", "String"):
            return None
        else:
            parts.append("%s=%s" % (name, fmt(f.get("value"))))
    return "(%s)" % ",".join(parts)


def struct_shape(fields):
    """A struct's field list as one string: `Zone:Object,iLeaf:Int,ZoneNumber:Byte`.

    The shape is the census's unit: two `PointRegion` values differ only in the width of the compact index
    inside `Zone`, so a shape that named sizes would report one struct as two types.
    """
    return "+".join("%s:%s%s" % (f.get("name") or "?", f["type"],
                                 ("<" + f["struct"] + ">") if f.get("struct") else "") for f in fields)


def decode_compact_array(raw, ps):
    """`[count][count compact indices]` -> the element count, or `None` when the payload does not consume itself.

    This is the residue the writer used to call "no self-consuming layout": an element that is a *compact
    index* is one or two bytes wide, so a payload's remainder over its count is not a width and
    `element_layout` refuses it. `ZoneInfo.ManualExcludes` is the shape -- eight `editinline ZoneInfo`
    indices in 14 bytes, six of them two bytes and two of them one. The rule is the object array's, count
    then self-consuming elements; what this returns is only that it consumed, because an index whose
    meaning is not in the declaration is not a value this writer should print.
    """
    try:
        count, off = ps.read_index(raw, 0)
    except IndexError:
        return None
    if count < 1 or count > 8192:
        return None
    for _ in range(count):
        if off >= len(raw):
            return None
        try:
            _v, off = ps.read_index(raw, off)
        except IndexError:
            return None
    return count if off == len(raw) else None


def decode_string_array(raw, ps):
    """`[count][count FStrings]` -> the strings, or `None` when the payload does not consume itself.

    `LevelInfo.LoadLipSincWithPrefix` is declared `array<string>` and its payload is five length-prefixed
    strings, so the count is exact and each element carries its own width. The length prefix counts the
    terminating NUL, the same rule `0x00058850` gives a `Str` value.
    """
    try:
        count, off = ps.read_index(raw, 0)
    except IndexError:
        return None
    if count < 1 or count > 4096:
        return None
    out = []
    for _ in range(count):
        try:
            n, after = ps.read_index(raw, off)
        except IndexError:
            return None
        width = 2 * abs(n) if n < 0 else n
        if n == 0 or after + width > len(raw):
            return None
        text = raw[after:after + width].split(b"\0")[0].decode("latin1", "replace")
        if len(text) != width - 1:
            return None
        out.append(text)
        off = after + width
    return out if off == len(raw) else None


def array_row(arrays, element_type, owner):
    """The census row for one (element type, declaring class), with every column the two rules fill."""
    return arrays.setdefault((element_type, owner),
                             {"arrays": 0, "records": 0, "widths": {}, "tagged": 0,
                              "tagged_widths": {}, "shapes": {}})


def decode_struct_elements(raw, pkg, ps):
    """`[count][count tagged blocks]` -> `[(width, fields)]`, or `None` when the payload is not that.

    An array whose element is a struct needs no element layout, and this is the section that used to defer
    them: a struct's value is a tagged property block (`prop_stream.struct_fields`), so each element carries
    its own field names, its own order and its own width -- the width moves because the first field is a
    compact index. `ParticleColorScale` is 14 bytes on some records, 17 on others, inside one array type.

    The element rule is the struct rule, not a second one: `read_block` gives the element's end, and
    `struct_fields` then has to accept exactly those bytes with both of its tests (ends at `None` on the last
    byte, `tag-encode` re-encodes the same tags). The array is this count followed by that many of them,
    consuming the payload exactly, so a payload of object indices or floats refuses rather than being counted
    as a struct array that happens to divide.
    """
    try:
        count, off = ps.read_index(raw, 0)
    except IndexError:
        return None
    if count < 1 or count > 8192:
        return None
    out = []
    for _ in range(count):
        if off >= len(raw):
            return None
        try:
            _props, end, reason = ps.read_block(raw[off:], pkg, True)
        except Exception:
            return None
        if reason != "None" or end <= 0:
            return None
        fields = ps.struct_fields(raw[off:off + end], pkg)
        if not fields:
            return None
        out.append((end, fields))
        off += end
    return out if off == len(raw) else None


def palette_record(data, ps):
    """A `StaticMeshInstance` payload -> `(colours, clear, tail bytes)` when the bytes are the packed colour table.

    The class is imported (`Class'Core.StaticMeshInstance'`) and its export carries no property block at
    all: the payload is a native record, `[0x00][compact count][count x FColor]` followed by the instance's
    own tail. The record's fourth byte is the colour's alpha, and in this corpus it is only ever `0xff` or
    `0x00`: 33,462 of the 33,509 payloads are opaque throughout and the other 47 start opaque and go clear
    after 84 records (`CTF-Abydos` #3539, `M07C` #2392, `M07D` #1459, `M07B` #3609 all break at the same
    index). So the test is `{0x00, 0xff}`, and a payload whose "count" is a byte of something else still
    refuses rather than being counted.

    `clear` is the number of records whose alpha is zero, so the census can tell the two populations apart.
    """
    if len(data) < 6 or data[0] != 0:
        return None
    try:
        count, off = ps.read_index(data, 1)
    except IndexError:
        return None
    if count < 1 or off + 4 * count > len(data):
        return None
    alphas = [data[off + 4 * k + 3] for k in range(count)]
    if any(a not in (0x00, 0xFF) for a in alphas):
        return None
    return count, alphas.count(0), len(data) - off - 4 * count


def level_trailer(tail, ps):
    """A `Level` trailer's bytes after its strings -> `(port, valid, model, width, seconds, alt, zero bytes)`.

    **The 32 bytes are a URL and one 4-byte float.** The first eight are `7777` and `1` on all 73 maps -- a
    `FURL`'s `Port` and `Valid`, which is what the five strings in front of them are (`"unreal"`, three empty,
    `"Index.unr"`). After them the second dword is a float at bytes 2..5 of the remaining 24 that equals the map's
    own `LevelInfo.TimeSeconds` on **73 of 73** maps (compared with the actor's property as the same pass reads
    it), so the trailer is a saved URL plus the level's saved time.

    The field ahead of the float is a **compact export reference** (two bytes on 72 maps, one on `Entry`, whose
    reference is 27), and the float sits immediately after it, so `Entry`'s trailer is 31 bytes rather than 32.
    After the float the rest is zero on **73 of 73** maps, 18 bytes each.
    """
    if len(tail) < 14:
        return None
    port, valid = struct.unpack("<II", tail[:8])
    # The field ahead of the float is a **compact index**, not a u16: its width is the encoding's, so the map
    # whose float sits one byte earlier (`Entry`) is the map whose reference needs one byte, not a map with a
    # narrow field. Both float alignments are handed back for the same reason as before -- the caller takes
    # the one that equals that map's own `LevelInfo.TimeSeconds`.
    try:
        model, nxt = ps.read_index(tail, 8)
    except IndexError:
        return None
    return (port, valid, model, nxt - 8, struct.unpack("<f", tail[nxt:nxt + 4])[0], nxt,
            sum(1 for b in tail[nxt + 4:] if b == 0))


def level_record(data, pkg, ps):
    """A `Level` export's payload -> `(actor count, {export indices}, strings, tail bytes, trailer)`.

    The `Level` object is not an actor and carries no property block. Its payload is
    `[0x00][u32 count][u32 count][count x compact export index][trailer]`: the leading zero is the empty
    property block's `None` terminator, the two dwords are the same number (the level's own actor count),
    and every index lands in this package's export table.

    The trailer is **not** a run of compact values: it begins with five length-prefixed strings of an ordinary
    `FString` shape (each length counts the terminating NUL), `"unreal"`, three empty ones and `"Index.unr"`, in
    all 73 maps, and then 32 bytes (31 in one map) that `level_trailer` reads. Reading those bytes as compact
    indices is what an earlier version did; it answered "41 to 43 values" because a compact index is any byte the
    sign rule accepts, and ASCII is full of them.
    """
    if len(data) < 10 or data[0] != 0:
        return None
    count, again = struct.unpack("<II", data[1:9])
    if count != again or not 0 < count <= len(pkg.exports):
        return None
    off, idx = 9, set()
    for _ in range(count):
        if off >= len(data):
            return None
        try:
            value, off = ps.read_index(data, off)
        except IndexError:
            return None
        if not 1 <= value <= len(pkg.exports):
            return None
        idx.add(value)
    strings = 0
    while off < len(data):
        try:
            size, nxt = ps.read_index(data, off)
        except IndexError:
            break
        if size < 0 or size > 128 or nxt + size > len(data):
            break
        if size:
            chunk = data[nxt:nxt + size]
            if chunk[-1] != 0 or any(b < 0x20 or b > 0x7E for b in chunk[:-1]):
                break
        strings += 1
        off = nxt + size
    return count, idx, strings, len(data) - off, level_trailer(data[off:], ps)


def smi_tail(tail, ps, colours=None):
    """A `StaticMeshInstance` payload's tail -> `(head, records, closes, flag-0, first fields, masks, mask bytes,
    masks that are not `ceil(colours/8)` bytes, masks with a set bit in the last byte's spare bits)`.

    `UStaticMeshInstance::Serialize` (`0x0024b5f0`) runs only on load, writes the class's instance record (a
    `TArray<FColor>` -- the colour table above -- and one dword) and then, when the package version is at least
    0x6e, a `TArray` of 20-byte records through the array serialiser at `0x0024e6e0` (a compact count, then each
    member). That is why the bytes after the colours are **a dword and a count of records**, not "fields past a
    head": the dword is a member of the instance record the same function writes, and the number after it counts
    the array that follows.

    The element is 20 bytes in memory and its three members are serialised in order: a compact index at `+0`
    (`[archive+0x18]`, the slot `inventory/archive_slots.csv` measures as `reference`), a `TArray<BYTE>` at `+4`
    (12 bytes: `FUN_0001aed0` writes the compact count and then the bytes), and a raw dword at `+0x10`
    (`FUN_00037110`, a four-byte `Serialize`). So a record's body is
    `[compact export index][compact length][that many bytes][dword flag]`, and the flag is **0 or 1** in this
    corpus -- the dword is not a constant, and the 790 payloads an earlier reading counted as a residue are
    payloads whose records carry 0. Every payload closes on this grammar (33,509 of 33,509).

    The first field is an export index into the level's own package and names the actor whose light the record
    bakes; the census counts how many of them resolve (`palette_refs`) and how many name a light (`palette_ref_light`).

    **The middle member is a bitmask over the colour table.** Measured on all 466,858 records of all 33,509
    payloads: the `TArray<BYTE>`'s length is `ceil(colours/8)` on every one, i.e. one bit per colour entry, so a
    record is "this light bakes these colours". The last byte's spare high bits are padding and are set on 43,126
    records, which is why the test is the *length* and not "no bit past the last colour". The census counts the masks (`palette_masks`), their bytes (`palette_mask_bytes`) and any record whose
    mask is the wrong length (`palette_mask_bad`), which is a gate at zero.
    """
    if len(tail) < 5:
        return None
    head = struct.unpack("<I", tail[:4])[0]
    try:
        count, off = ps.read_index(tail, 4)
    except IndexError:
        return None
    if count < 0:
        return None
    want = None if colours is None else -(-colours // 8)
    flags0, refs = 0, []
    masks = mask_bad = pad_bits = 0
    for _ in range(count):
        try:
            first, off = ps.read_index(tail, off)
            size, off = ps.read_index(tail, off)
        except IndexError:
            return head, count, False, flags0, refs, masks, 0, 0
        if size < 0 or off + size + 4 > len(tail):
            return head, count, False, flags0, refs, masks, 0, 0
        mask = tail[off:off + size]
        off += size
        flag = struct.unpack("<I", tail[off:off + 4])[0]
        if flag not in (0, 1):
            return head, count, False, flags0, refs, masks, 0, 0
        masks += size
        if want is not None:
            if size != want:
                mask_bad += 1
            elif want and colours % 8 and mask[-1] >> (colours % 8):
                pad_bits += 1
        flags0 += flag == 0
        off += 4
        refs.append(first)
    return head, count, off == len(tail), flags0, refs, masks, mask_bad, pad_bits


def terrain_colours(tail, ps):
    """A `TerrainInfo`'s native terrain record -> the vertex-colour count of the array it ends with.

    `ATerrainInfo::Serialize` (`0x00273e80`, decompiled from the kept Ghidra project) ends, for a package
    version above `0x74`, with `FUN_001f7df0(Ar, this+0x1404)` -- the **same** `TArray<FColor>` writer
    `UStaticMeshInstance`'s colour record uses (`[compact count][4 bytes per colour]`, each colour read and
    written one byte at a time). So the terrain record's last member is the terrain's own per-vertex colour
    array, and its count sits immediately before the colours.

    The run is found from the end: every colour in the corpus has alpha `0xff`, so the trailing records are
    the ones whose fourth byte is `0xff`. The count is then the compact index that ends exactly where the
    colours begin and equals their number -- a candidate that only closes when both hold, which is why a
    payload whose last colour is not opaque answers `None` rather than a shorter array.

    The counts this measures are squares (`16x16` .. `256x256`), the terrain's vertex grid: `256`, `1024`,
    `4096`, `16384`, `65536`. `inventory/t3d_coverage.csv` carries them and `--check` holds every one to a
    perfect square.
    """
    end = len(tail)
    start = end
    while start - 4 >= 0 and tail[start - 1] == 0xFF:
        start -= 4
    count = (end - start) // 4
    if count < 1:
        return None
    for width in (1, 2, 3, 4, 5):
        if start - width < 0:
            break
        try:
            value, after = ps.read_index(tail, start - width)
        except IndexError:
            continue
        if after == start and value == count:
            return count
    return None


def compact_width(value):
    """Width of the compact index that encodes `value` (bit 7 sign, bit 6 continuation, 6 + 7 bits)."""
    if value < 0:
        value = -value
    for width in (1, 2, 3, 4, 5):
        if value < (1 << (6 + 7 * (width - 1))):
            return width
    return 5


def terrain_body(tail, ps, colours):
    """A `TerrainInfo`'s native terrain record -> its members up to the vertex colours.

    `ATerrainInfo::Serialize` (`0x00273e80`) writes, for a package version above `0x74`:
    `Super::Serialize`, a list of sectors, one 12-byte member per vertex, two dwords, one 24-byte member per
    vertex, two 48-byte frames, the terrain grid's two dwords, then the `TArray<FColor>` `terrain_colours`
    reads:

        [compact count][that many compact object references]      the terrain's sectors
        [compact count][count * 12 bytes]                         one FVector per vertex (its position)
        [int][int]                                                the sector grid, x and y
        [compact count][count * 24 bytes]                         two triangle normals per vertex
        [48 bytes][48 bytes]                                      the terrain's frame and its inverse
        [int][int]                                                the terrain grid, its width and height
        [compact count][count * 4 bytes]                          the vertex colours

    **The stretch after the record array is 48 + 48 + 8, not 80 + 24.** The two members sit at
    `this+0x1358` and `this+0x1388` -- 0x30 = 48 bytes apart -- and `FUN_0021f140`, which writes each one,
    is twelve four-byte writes (`param+0x00` .. `param+0x2c`). So the last four dwords the earlier reading
    called "the tail" (`[0][0][0][float]`) are the *fourth vector of the second frame*, and the tail proper
    is the two dwords at `this+0x1334`/`this+0x1338` -- the grid `FUN_0026bda0` indexes the vertex array
    with (`[+0x1334] * row + col`). Both readings close on 66 of 66 exports; this one is the one the code
    writes, and `frames` carries the two frames' twelve floats each.

    Every one of the 66 exports closes exactly on this frame when `colours` is the count
    `terrain_colours` read, and the counts agree: positions and records both equal the vertex colours
    (**313,344** each), and every reference names a `TerrainSector` export. A frame that does not end where
    the colours' own count begins is refused, so a body this rule cannot read is a `None` rather than a
    shorter reading.
    """
    if not tail or not colours:
        return None
    end = len(tail) - 4 * colours - compact_width(colours)
    if end < 4:
        return None
    off = 0
    try:
        count, off = ps.read_index(tail, off)
        if count < 0 or count > 4096:
            return None
        refs = []
        for _ in range(count):
            ref, off = ps.read_index(tail, off)
            refs.append(ref)
        verts, off = ps.read_index(tail, off)
        if verts < 1 or verts > 65536:
            return None
        verts_at = off
        off += 12 * verts
        if off + 8 + 24 > end:
            return None
        grid = struct.unpack_from("<ii", tail, off)
        off += 8
        records, off = ps.read_index(tail, off)
        if records < 0 or records > 65536:
            return None
        records_at = off
        off += 24 * records
        if off + 96 + 8 > end:
            return None
        frames = (struct.unpack_from("<12f", tail, off), struct.unpack_from("<12f", tail, off + 48))
        off += 96
        dims = struct.unpack_from("<ii", tail, off)
        off += 8
    except (IndexError, struct.error):
        return None
    if off != end:
        return None
    if verts != colours or records != colours:
        return None
    if grid[0] < 1 or grid[1] < 1 or grid[0] * grid[1] != len(refs):
        return None
    if dims[0] != dims[1] or dims[0] < 2:
        return None
    return dict(sectors=refs, verts=verts, verts_at=verts_at, grid=grid, records=records,
                records_at=records_at, dims=dims, frames=frames)


def frame_inverse(frames):
    """`True` when the second frame is the first one's inverse, the way `FUN_000541f0` builds it.

    The first frame carries a translation in cells and one scale per axis (the x and y axes are world units
    per cell, the z axis world units per height sample); `FUN_00264220` copies it to `this+0x1358` and its
    inverse to `this+0x1388`. Measured over the corpus the inverse is `1/scale` on each axis and
    `-translation * scale` on each offset, so the test is those six products -- 66 of 66 exports hold it.
    """
    a, b = frames
    for i, scale in ((3, a[3]), (7, a[7]), (11, a[11])):
        if scale == 0 or abs(b[i] * scale - 1.0) > 1e-3:
            return False
    for offset, scale in ((0, a[3]), (1, a[7]), (2, a[11])):
        if abs(b[offset] + a[offset] * scale) > 1e-3 * max(1.0, abs(a[offset] * scale)):
            return False
    return True


def vertex_step(rec, verts_at, verts, scale):
    """The vertex grid's spacing per axis, as a multiple of the frame's x scale, or `None`.

    The positions are a rectilinear grid: `sqrt(n)` distinct x values and that many y values, evenly spaced.
    Their spacing is the frame's own x scale on 65 of the 66 exports and **half** of it on `CTF-Abydos`'s
    16,384-vertex terrain -- the one map that carries two vertices per cell on each axis.
    """
    side = int(round(verts ** 0.5))
    if side * side != verts or side < 3 or not scale:
        return None
    xs = sorted(set(round(struct.unpack_from("<f", rec, verts_at + 12 * i)[0], 3) for i in range(verts)))
    if len(xs) != side:
        return None
    gaps = [xs[i + 1] - xs[i] for i in range(side - 1)]
    if max(gaps) - min(gaps) > 1e-3 * max(1.0, abs(max(gaps))):
        return None
    return round(((min(gaps) + max(gaps)) / 2) / scale, 2)


def terrain_span(refs, sectors):
    """The furthest sector edge in the terrain's sector list, one per axis, or `None`.

    A `TerrainSector` payload opens `[0x00][compact index of its TerrainInfo][quads x][quads y]
    [origin x][origin y]`, and its quads are 8, 16 or 32 with the far edge one short (15 to avoid
    duplicating the shared boundary). The terrain body's own tail ints are one more than the furthest
    edge on every export, which is what ties the two records together: the header names the terrain, and
    the terrain's size counts its sectors.
    """
    edge = [0, 0]
    seen = 0
    for ref in refs:
        row = sectors.get(ref)
        if row is None:
            continue
        seen += 1
        edge[0] = max(edge[0], row[3] + row[1])
        edge[1] = max(edge[1], row[4] + row[2])
    if not seen:
        return None
    return edge[0] + 1, edge[1] + 1


def sector_record(data, ps):
    """A `TerrainSector` payload -> (terrain export index, quads x, quads y, origin x, origin y).

    The class has no property block (`class-prefix` reads no tagged record in its payload), so its
    payload is its native instance record: a zero byte, a compact index naming the `TerrainInfo` it
    belongs to, and four ints. The two sizes are 8, 16 or 32 quads and the two origins are multiples of
    eight; a payload that does not open this way is refused rather than read as a sector.
    """
    if len(data) < 19 or data[0] != 0:
        return None
    try:
        ref, off = ps.read_index(data, 1)
    except IndexError:
        return None
    if off + 16 > len(data):
        return None
    qx, qy, ox, oy = struct.unpack_from("<4i", data, off)
    if not (1 <= qx <= 32 and 1 <= qy <= 32):
        return None
    if ox < 0 or oy < 0 or ox % 8 or oy % 8:
        return None
    return ref, qx, qy, ox, oy


def locate(data, pkg, ps, preferred=None, cls=None):
    """The actor's property block, located by the reader's own value test.

    This writer used to carry a second locator: the start whose block spends every object reference in the
    package's own tables, then the most properties. `prop_stream.find_block` now runs a stronger test -- every
    checkable value has to consume exactly the `Size` its tag declares -- and that is the test a *writer* needs:
    a parse that begins inside a struct payload still ends at the export's end and still resolves every name,
    so only the values can tell it is not a block.

    The prefix is pinned to the reader's 64-byte window for the same reason. Left to itself `find_block` looks
    one window further when nothing inside decodes, which is right for a class header; no map actor's block
    starts later than 32 bytes in (`inventory/actor_prefix_census.csv`), so the only exports a wider look finds
    are `StaticMeshInstance`s, whose payload is a packed native record with no tagged block at all -- the tags
    read out of it are two bytes of a transform.

    **A class that serialises a native record after its block keeps the block at the prefix's end.** `find_block`
    requires the block to end at the export's end, and a `TerrainInfo`'s payload is
    `[prefix record][property block][native terrain record]`, so no exact-end candidate exists and the class read
    as "no property block at all". The block still terminates on `None` where it always does; for the classes in
    `NATIVE_TAIL` the parse there is taken when the value test accepts it, and the native tail is counted.

    `preferred` is the prefix record's end when its bytes are the record, and `pad_tail=1` reads the two
    `Cubemap`s whose block ends one zero byte short of the export (U30).
    """
    found = ps.find_block(data, pkg, 2, True, window=getattr(ps, "WINDOW", 64),
                          preferred=preferred, pad_tail=1)
    if found:
        _count, start, _props = found
        props, end, _reason = ps.read_block(data[start:], pkg, True)
        if props:
            return len(props), start, props, len(data) - start - end, False
    if cls in NATIVE_TAIL and preferred:
        props, end, why = ps.read_block(data[preferred:], pkg, True)
        if props and why == "None" and 0 < end < len(data) - preferred:
            bad, seen = ps.block_bad(props, data, preferred)
            if bad == 0 and seen:
                return len(props), preferred, props, len(data) - preferred - end, True
    return None


def drive_map(path, outdir, ps, parent=None, types=None, verbose=False, prec=None):
    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    name = os.path.splitext(os.path.basename(path))[0]
    lines = ["// %s — written by tools/map-to-t3d.py" % name, ""]
    actors = props_total = 0
    st = {"arrays": 0, "object_arrays": 0, "object_elements": 0, "struct_arrays": 0,
          "struct_records": 0, "unshaped": 0, "struct_props": 0, "struct_decoded": 0, "struct_raw": 0,
          "struct_tagged": 0, "struct_native": 0, "compact_arrays": 0, "compact_elements": 0,
          "string_arrays": 0, "string_elements": 0, "elements_decoded": 0, "record_starts": 0,
          "padded_blocks": 0, "palette_payloads": 0, "palette_refused": 0, "palette_colours": 0,
          "palette_clear": 0, "palette_tail": 0, "palette_tails": 0, "palette_head2": 0,
          "palette_records": 0, "palette_records_flag0": 0, "palette_refs": 0, "palette_ref_light": 0,
          "palette_ref_other": 0, "palette_closed": 0, "level_actors": 0, "level_strings": 0,
          "level_tail_bytes": 0, "level_unwritten": 0, "native_tails": 0, "struct_element_arrays": 0,
          "struct_element_records": 0, "terrain_bytes": 0, "terrain_colours": 0,
          "terrain_colour_exports": 0, "terrain_grids": "", "terrain_body": 0,
          "terrain_sectors": 0, "terrain_sector_refs": 0, "terrain_verts": 0, "terrain_records": 0,
          "terrain_span": 0, "sector_payloads": 0, "sector_named": 0, "sector_quads": 0,
          "terrain_frames": 0, "terrain_half_grid": 0, "terrain_steps": [],
          "palette_masks": 0, "palette_mask_bytes": 0, "palette_mask_bad": 0, "palette_pad_bits": 0,
          "level_tail_port": 0, "level_tail_prefix": 0, "level_tail_zero": 0, "level_seconds_match": 0,
          "level_seconds_off": 0, "palette_heads": {}, "palette_other": {},
          "level_model_class": "", "level_model_biggest": 0, "level_model_width": 0}
    structs, arrays = {}, {}
    terrain_grid_set = set()
    written, level_idx = set(), set()
    level_seconds = levelinfo_seconds = None
    # A `TerrainInfo` counts its sectors and each sector names its terrain, so the two records are read as
    # one table: the sector headers are collected first, then the terrain body's tail ints are held to the
    # furthest edge in the list it carries (`terrain_span`).
    sector_rows = {}
    for exp in pkg.exports:
        if pkg.class_name_of(exp) == "TerrainSector":
            row = sector_record(pkg.data[exp["offset"]:exp["offset"] + exp["size"]], ps)
            if row is not None:
                sector_rows[exp["index"] + 1] = row
    for exp in pkg.exports:
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        cls = pkg.class_name_of(exp)
        if cls == "Level":
            # The level object: not an actor, no property block. Its payload is the level's own actor list
            # (`level_record`), which is the one reading of this export that the writer can cross-check
            # against its own output -- every index it names should be an actor the writer writes.
            level = level_record(data, pkg, ps)
            if level is not None:
                st["level_actors"], level_idx = level[0], level[1]
                st["level_strings"], st["level_tail_bytes"] = level[2], level[3]
                # The 32 bytes after the strings are a `FURL` (`7777`, `1`) and one float, which is the
                # level's own `TimeSeconds`: it is held against the `LevelInfo` actor's property below.
                if level[4] is not None:
                    port, _valid, model, width, seconds, at, zero = level[4]
                    st["level_tail_port"], st["level_tail_prefix"] = port, model
                    st["level_tail_zero"] = zero
                    st["level_model_width"] = width
                    level_seconds = (seconds, at)
                    # The reference names the level's own `Model`: the *largest* `Model` export in the
                    # package on **73 of 73** maps, which is the level's BSP (`Model1` on most maps -- the
                    # other `Model` exports belong to brushes and movers). Both halves are held because a
                    # reference that lands on a `Model` at random would satisfy neither.
                    if 1 <= model <= len(pkg.exports):
                        target = pkg.exports[model - 1]
                        st["level_model_class"] = pkg.class_name_of(target)
                        sizes = [e["size"] for e in pkg.exports if pkg.class_name_of(e) == "Model"]
                        if sizes and target["size"] == max(sizes):
                            st["level_model_biggest"] += 1
            continue
        if cls == "TerrainSector":
            # A sector's payload is its native record (`sector_record`): no property block, a header that
            # names the `TerrainInfo` it belongs to and gives its size and origin in quads. Counted here;
            # the export still goes through the writer's own locator below, which writes the one sector in
            # `DM-WorstCaseScenario` that does carry a tagged block and skips the rest as it always has.
            row = sector_rows.get(exp["index"] + 1)
            if row is not None:
                ref, qx, qy, _ox, _oy = row
                st["sector_payloads"] += 1
                st["sector_quads"] += qx * qy
                if 1 <= ref <= len(pkg.exports) and pkg.class_name_of(pkg.exports[ref - 1]) == "TerrainInfo":
                    st["sector_named"] += 1
        if cls == "StaticMeshInstance":
            # No property block at all: the payload is the instance's native record, whose first half is a
            # packed colour table (`palette_record`). Counted here rather than written as an actor -- there
            # is no property to place and no actor in any map references the export.
            palette = palette_record(data, ps)
            if palette is None:
                st["palette_refused"] += 1
            else:
                st["palette_payloads"] += 1
                st["palette_colours"] += palette[0]
                st["palette_clear"] += palette[1]
                st["palette_tail"] += palette[2]
                if palette[2] >= 4:
                    st["palette_tails"] += 1
                    if data[-palette[2]:4 - palette[2]] == b"\x02\x00\x00\x00":
                        st["palette_head2"] += 1
                    # The head dword is carried per map: it is one value on 59 of 72 maps (2 on 30,450
                    # payloads), so the census records the histogram rather than a single number.
                    head = struct.unpack("<I", data[len(data) - palette[2]:][:4])[0]
                    st["palette_heads"][head] = st["palette_heads"].get(head, 0) + 1
                tail = smi_tail(data[len(data) - palette[2]:], ps, palette[0])
                if tail is not None:
                    st["palette_records"] += tail[1]
                    st["palette_masks"] += tail[1]
                    st["palette_mask_bytes"] += tail[5]
                    st["palette_mask_bad"] += tail[6]
                    st["palette_pad_bits"] += tail[7]
                    st["palette_records_flag0"] += tail[3]
                    if tail[2]:
                        st["palette_closed"] += 1
                    for ref in tail[4]:
                        st["palette_refs"] += 1
                        if 1 <= ref <= len(pkg.exports):
                            cname = pkg.class_name_of(pkg.exports[ref - 1])
                            if "light" in cname.lower() or cname in LIGHT_FAMILY:
                                st["palette_ref_light"] += 1
                            else:
                                st["palette_ref_other"] += 1
                                st["palette_other"][cname] = st["palette_other"].get(cname, 0) + 1
                        else:
                            st["palette_ref_other"] += 1
                            st["palette_other"]["<out of range>"] = st["palette_other"].get("<out of range>", 0) + 1
            continue
        preferred = None
        if prec is not None:
            predicted, matched = prec.record(data, exp["class"])
            if matched:
                st["record_starts"] += 1
                preferred = predicted
        found = locate(data, pkg, ps, preferred, cls)
        if not found:
            continue
        _count, start, props, tail, native = found
        if cls == "LevelInfo":
            # The level's saved time, as the actor carries it: the `Level` trailer's float is held to this.
            for prop in props or ():
                if prop.get("name") == "TimeSeconds" and isinstance(prop.get("value"), (int, float)):
                    levelinfo_seconds = float(prop["value"])
                    break
        native_bytes = data[start + (len(data) - start - tail):] if native else b""
        terrain_n = None
        body = None
        if native:
            st["native_tails"] += 1
            # The record's last member is the terrain's own vertex-colour array (`terrain_colours`): its
            # count is what this class reads back, so it is counted here rather than left as "10 KB to
            # 2.6 MB of terrain record". The grids are carried per map so `--check` can hold every count
            # to a square without the tool re-reading the dump.
            st["terrain_bytes"] += len(native_bytes)
            terrain_n = terrain_colours(native_bytes, ps)
            if terrain_n:
                st["terrain_colours"] += terrain_n
                st["terrain_colour_exports"] += 1
                terrain_grid_set.add(terrain_n)
            # The body between the block and the colours: its sector list, its per-vertex members, its
            # sector grid, its two frames and its grid (`terrain_body`). Counted against the colours it
            # ends on, and its grid held to the furthest edge in the sector list it carries.
            body = terrain_body(native_bytes, ps, terrain_n)
            if body is not None:
                st["terrain_body"] += 1
                st["terrain_sectors"] += len(body["sectors"])
                st["terrain_verts"] += body["verts"]
                st["terrain_records"] += body["records"]
                for ref in body["sectors"]:
                    if 1 <= ref <= len(pkg.exports) and pkg.class_name_of(pkg.exports[ref - 1]) == "TerrainSector":
                        st["terrain_sector_refs"] += 1
                side = int(round(body["verts"] ** 0.5))
                span = terrain_span(body["sectors"], sector_rows)
                if span == body["dims"]:
                    st["terrain_span"] += 1
                # The two frames: `FUN_00264220` builds a grid->world frame at `this+0x1358` and its
                # inverse at `this+0x1388`, so the second has to be the first one's reciprocal on every
                # export -- the test `frame_inverse` makes.
                if frame_inverse(body["frames"]):
                    st["terrain_frames"] += 1
                # The vertex grid against the frame's own x scale, and against the grid dwords: the two
                # places the four largest terrains differ from the other 62.
                step = vertex_step(native_bytes, body["verts_at"], body["verts"], body["frames"][0][3])
                if step is not None:
                    st["terrain_steps"].append("%.2f" % step)
                if body["dims"][0] * 2 == side:
                    st["terrain_half_grid"] += 1
        elif tail:
            st["padded_blocks"] += 1
        lines.append("Begin Actor Class=%s Name=%s_%d" % (cls, cls, exp["index"] + 1))
        written.add(exp["index"] + 1)
        for p in props:
            value = p.get("value")
            raw = data[start + p["value_offset"]:start + p["value_offset"] + p["size"]]
            if p["type"] == "Array":
                st["arrays"] += 1
                dt, owner = declared(parent or {}, types or {}, cls, p["name"])
                et = dt[6:-1] if dt.startswith("array<") else ""
                # The declaration decides the string half, and it has to be asked first: a payload of two
                # equally long strings divides by its count (`\x02\x04abc\x00\x04def\x00` is 4 bytes per
                # element), so a divisibility test alone would read an `array<string>` as a struct array.
                if et == "string":
                    strings = decode_string_array(raw, ps)
                    if strings is not None:
                        st["string_arrays"] += 1
                        st["string_elements"] += len(strings)
                        for i, text in enumerate(strings):
                            lines.append('   %s(%d)="%s"' % (p["name"], i, text.replace('"', "'")))
                        continue
                refs = decode_obj_array(raw, pkg, ps)
                if refs is not None:
                    st["object_arrays"] += 1
                    st["object_elements"] += len(refs)
                    for i, ref in enumerate(refs):
                        lines.append("   %s(%d)=%s" % (p["name"], i, ps.resolve_ref(pkg, ref)))
                    continue
                elements = decode_struct_elements(raw, pkg, ps)
                if elements is not None:
                    # A struct array whichever rule reads it: `struct_arrays` is the refused population the
                    # partition in `--check` counts, and `struct_element_*` is the half this rule settled.
                    st["struct_arrays"] += 1
                    st["struct_records"] += len(elements)
                    st["struct_element_arrays"] += 1
                    st["struct_element_records"] += len(elements)
                    row = array_row(arrays, et, owner)
                    row["arrays"] += 1
                    row["records"] += len(elements)
                    row["tagged"] += 1
                    printed = []
                    for width, fields in elements:
                        row["tagged_widths"][width] = row["tagged_widths"].get(width, 0) + 1
                        shape = struct_shape(fields)
                        row["shapes"][shape] = row["shapes"].get(shape, 0) + 1
                        printed.append(fmt_struct(fields))
                    if any(text is None for text in printed):
                        # One field is an array (`IMusicSegment.Transition`), so the field list is known and
                        # the element is not printable as a tuple: say which shape, never a hole.
                        lines.append("   // %s: %d element(s) of %s"
                                     % (p["name"], len(elements), struct_shape(elements[0][1])))
                        continue
                    for i, text in enumerate(printed):
                        lines.append("   %s(%d)=%s" % (p["name"], i, text))
                    continue
                layout = element_layout(raw, ps)
                if layout:
                    st["struct_arrays"] += 1
                    st["struct_records"] += layout[0]
                    row = array_row(arrays, et, owner)
                    row["arrays"] += 1
                    row["records"] += layout[0]
                    row["widths"][layout[1]] = row["widths"].get(layout[1], 0) + 1
                    elements = decode_elements(raw, ps, et)
                    if elements is not None:
                        st["elements_decoded"] += len(elements)
                        for i, value in enumerate(elements):
                            lines.append("   %s(%d)=%s" % (p["name"], i, fmt_element(value)))
                        continue
                    lines.append("   // %s: %d element(s) of %d byte(s)%s"
                                 % (p["name"], layout[0], layout[1],
                                    (" of " + et) if et else ", element type not declared"))
                else:
                    compact = decode_compact_array(raw, ps)
                    if compact is not None:
                        st["compact_arrays"] += 1
                        st["compact_elements"] += compact
                        lines.append("   // %s: %d element(s) of a compact index in %d byte(s)%s"
                                     % (p["name"], compact, len(raw), (" of " + et) if et else ""))
                        continue
                    st["unshaped"] += 1
                    lines.append("   // %s: %d byte(s), no self-consuming layout" % (p["name"], len(raw)))
                continue
            if p["type"] == "Struct":
                st["struct_props"] += 1
                sn = p.get("struct") or "?"
                row = structs.setdefault(sn, {"n": 0, "sizes": {}, "decoded": 0, "tagged": 0,
                                              "native": 0, "shapes": {}})
                row["n"] += 1
                row["sizes"][p["size"]] = row["sizes"].get(p["size"], 0) + 1
                text = None
                if isinstance(value, (tuple, list)):
                    st["struct_native"] += 1
                    row["native"] += 1
                    text = fmt(value)
                else:
                    fields = p.get("fields")
                    if fields:
                        row["shapes"][struct_shape(fields)] = row["shapes"].get(
                            struct_shape(fields), 0) + 1
                        text = fmt_struct(fields)
                        if text is not None:
                            st["struct_tagged"] += 1
                            row["tagged"] += 1
                if text is None:
                    st["struct_raw"] += 1
                    text = fmt(value)
                else:
                    st["struct_decoded"] += 1
                    row["decoded"] += 1
                lines.append("   %s=%s" % (p["name"], text))
                continue
            lines.append("   %s=%s" % (p["name"], fmt(value)))
        props_total += len(props)
        if native:
            if body is not None:
                lines.append("   // Terrain: %d native byte(s), %s, %d sector(s) in a %dx%d grid, "
                             "%d vertex position(s), %d per-vertex record(s), %dx%d grid, "
                             "frame scale (%.4g, %.4g, %.4g)"
                             % (len(native_bytes), ("%d vertex colour(s)" % terrain_n) if terrain_n
                                else "no vertex-colour array read", len(body["sectors"]),
                                body["grid"][0], body["grid"][1], body["verts"], body["records"],
                                body["dims"][0], body["dims"][1],
                                body["frames"][0][3], body["frames"][0][7], body["frames"][0][11]))
            else:
                lines.append("   // Terrain: %d native byte(s), %s"
                             % (len(native_bytes), ("%d vertex colour(s)" % terrain_n) if terrain_n
                                else "no vertex-colour array read"))
        lines.append("End Actor")
        lines.append("")
        actors += 1
    if level_idx:
        st["level_unwritten"] = len(level_idx - written)
        lines.insert(2, "// Level: %d actor(s) named by the level's own list, %d not written"
                     % (st["level_actors"], st["level_unwritten"]))
        lines.insert(3, "")
    st["terrain_grids"] = ";".join(str(n) for n in sorted(terrain_grid_set))
    if level_seconds and levelinfo_seconds is not None:
        # The float's offset is the one the reference's own width puts it at (9 on `Entry`, 10 elsewhere), so
        # the match against `LevelInfo.TimeSeconds` is an independent control on where the reader put it.
        seconds, at = level_seconds
        if abs(seconds - levelinfo_seconds) <= 1e-4 * max(1.0, abs(levelinfo_seconds)):
            st["level_seconds_match"], st["level_seconds_off"] = 1, at - 8
    text = "\n".join(lines)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, name + ".t3d"), "w") as f:
        f.write(text)
    if verbose:
        print("  %s" % os.path.join(outdir, name + ".t3d"))
    return {"map": name, "actors": actors, "bytes": len(text), "props": props_total,
            "unresolved": text.count("ref:"), "values": st, "structs": structs, "arrays": arrays}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class _Pkg:
    """A stand-in package for the synthetic reader test: N exports, no imports."""

    def __init__(self, n):
        self.exports = [{} for _ in range(n)]
        self.imports = []


class _Ps:
    """Canned blocks keyed by the first byte of the buffer, so the delegation is driven without a package.

    The value test itself is `prop-stream`'s and is proved there; what this writer has to prove is that it hands
    the location over to it -- with the writer's pinned window, the prefix record's start and the one-byte tail
    -- and writes the block *it* chose rather than a second rule of its own.
    """

    WINDOW = 64

    def __init__(self, table, start=0):
        self.table = table
        self.start = start
        self.seen = {}

    def find_block(self, data, pkg, min_props=2, values=True, native_width=None, window=None,
                   preferred=None, pad_tail=0):
        self.seen = {"window": window, "preferred": preferred, "pad_tail": pad_tail}
        props = [dict(p) for p in self.table[data[self.start]]]
        return len(props), self.start, props

    def read_block(self, buf, pkg, want_values=False):
        return [dict(p) for p in self.table[buf[0]]], len(buf), "None"


class _NoBlock:
    """A reader that finds no block, so the writer cannot fall back to a rule of its own."""

    WINDOW = 64

    def __init__(self):
        self.seen = {}

    def find_block(self, data, pkg, min_props=2, values=True, native_width=None, window=None,
                   preferred=None, pad_tail=0):
        self.seen = {"window": window, "preferred": preferred, "pad_tail": pad_tail}
        return None


def struct_types(path=DECLARED):
    """Every struct name the corpus declares: a `UStructProperty`'s friendly type in class_properties.csv.

    A tag's struct field is a *name index*, so a block that begins inside a value reads whatever word sits at
    that index as the struct's type. Only a declaration can say whether the word is a type.
    """
    out = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["kind"] == "UStructProperty" and r["friendly_type"]:
                out.add(r["friendly_type"])
    return out


# A struct name the corpus does not declare is a mislocated start, and since the prefix record names the start
# (`locate()`'s `preferred`) none is left: the gate below is strict, and `padded_blocks` pins the only export
# whose block ends short of the export -- the two `Cubemap`s (U30).
RESIDUE_STRUCTS = {}

# The classes whose property block is followed by a native record inside the same export, so the block cannot
# end at the export's end. `TerrainInfo`'s payload is `[prefix record 17][property block][native terrain
# record]` on all 66 of its exports: the block opens `TerrainMap`, `TerrainScale`, `Layers`, the two bitmaps,
# `Level`, `Region`, ... and terminates on `None` at 258..4,850 bytes in, and the terrain bytes after it are
# 10 KB to 2.6 MB. `locate()` reads the block at the prefix record's end for these and counts the tail.
NATIVE_TAIL = {"TerrainInfo"}
NATIVE_TAIL_PIN = 66

# The `Level` trailer is five length-prefixed strings -- `"unreal"`, three empty ones and `"Index.unr"`, in
# that order -- followed by 32 bytes (31 in `DM-WorstCaseScenario`). The count is pinned so a trailer that
# starts with something else is a failure rather than a shorter reading.
LEVEL_STRINGS = 5

# The 32 bytes after those strings are a `FURL` and one float: `7777` (`Port`) and `1` (`Valid`) on all 73
# maps, then a float that equals the map's own `LevelInfo.TimeSeconds` on 73 of 73. What follows the float is
# zero: 18 bytes on 72 maps and 17 of 18 on `Entry`, whose two-byte field ahead of the float is one byte wide.
LEVEL_TAIL_PORT = 7777
LEVEL_SECONDS_MATCH = 73
LEVEL_SECONDS_SHIFTS = {2: 72, 1: 1}
LEVEL_TAIL_ZERO = 1314

# The field ahead of that float is a **compact export reference** and it names the level's own `Model`: the
# *largest* `Model` export of the package on **73 of 73** maps, and its class is `Model` on every one. The
# one-byte width on `Entry` is the compact encoding's own width (`63` and under encode in one byte), which is
# the same map whose float sits one byte earlier -- so the census holds `level_model_width` to
# `level_seconds_off` and the two readings explain each other instead of contradicting.
LEVEL_MODEL_CLASS = "Model"
LEVEL_MODEL_BIGGEST = 73

# The head dword of a `StaticMeshInstance` tail is one value per map: **59 of 72** maps carry a single value
# (2 on 30,450 of 33,509 payloads) and the other 13 carry a second value scattered through the export table
# (1..112 runs), so it is a mark the bake wrote per map, not a count of anything in the payload.
PALETTE_HEAD_VALUES = 9
PALETTE_HEAD_SINGLE = 59

# The palette references that do not name a light are **423** and exactly three classes: the invisible
# navigation actors `InterpolationPoint`, `PathNode` and `LookTarget`. A fourth class is a new reading.
PALETTE_REF_OTHER = 423
PALETTE_OTHER_CLASSES = {"InterpolationPoint": 292, "PathNode": 96, "LookTarget": 35}

# A palette record's middle member is a bitmask over the payload's colour table: its length is `ceil(colours/8)`
# on **466,858 of 466,858** records, so a record is one light and the colours it bakes. The last byte's spare
# bits are padding and are set on 43,126 records. `palette_mask_bad` is the gate: a record whose mask is the
# wrong length is a record whose grammar moved.
PALETTE_MASKS = 466858
PALETTE_PAD_BITS = 43126

# Classes whose name does not carry "Light" but which are lights: an `Emitter` declares its own light in UE2, and
# the palette records name it after the `Light`/`Sunlight`/`Spotlight` families.
LIGHT_FAMILY = {"Emitter"}

# Every `Level` export names its own actors, and the writer writes each one it names: `TerrainInfo` was the last
# class a `Level` named and no T3D held, because its block was only searched for at the export's end. It is
# written now, so the residue is empty and this gate fails if a class stops being written.
LEVEL_UNWRITTEN = 0

# The terrain record's tail is the terrain's own vertex-colour array (`terrain_colours`): a square count,
# `16x16` .. `256x256`, because the record is a vertex grid. `TERRAIN_GRIDS` is every count the corpus holds
# and `TERRAIN_COLOUR_EXPORTS` is how many `TerrainInfo` exports close on it -- the gate fails when a payload
# stops closing, when a count is not a square, or when the record is too short to hold the colours it names.
TERRAIN_GRIDS = {256, 1024, 4096, 16384, 65536}
TERRAIN_COLOUR_EXPORTS = 66

# The terrain record's body between the property block and the colours is a member chain
# (`terrain_body`): every one of the 66 exports closes on it, and its three per-vertex counts agree with
# the colours it ends on -- **313,344** positions, **313,344** 24-byte records, **313,344** colours. Its
# sector list names **765** `TerrainSector` exports, and those payloads in turn name their terrain, so the
# two records are one table: the sector headers (`sector_record`) are the terrain's own sector grid, and
# their quads add up to `TERRAIN_QUADS`.
TERRAIN_BODY_EXPORTS = 66
TERRAIN_SECTORS = 765
TERRAIN_QUADS = 194114

# The two 48-byte members after the record array are a grid->world frame and its inverse (`frame_inverse`):
# the inverse's scale is the forward scale's reciprocal and its offset is the forward offset times that
# scale, on **66 of 66** exports. The vertex grid's spacing against the frame's own x scale is **1.00** on
# 65 exports and **0.50** on one (`CTF-Abydos`'s 16,384-vertex terrain, which carries two vertices per cell
# on each axis), and **4** exports carry a vertex grid twice the grid dwords (`CTF-Abydos` 217 and 246 and
# both `LostCity` maps).
TERRAIN_FRAME_EXPORTS = 66
TERRAIN_HALF_GRIDS = 4
TERRAIN_STEPS = {"1.00": 65, "0.50": 1}


def check(values, structs, stats, arrays=ARRAYS):
    """The censuses are arithmetic on each other, and a synthetic payload drives the reader. No dump."""
    problems = []
    ps = load_prop_stream()
    vrows = read_csv(values)
    if len(vrows) != 73:
        problems.append("%s: %d rows, expected 73" % (values, len(vrows)))
    tot = {"arrays": 0, "object_arrays": 0, "arrays_refused": 0, "struct_props": 0,
           "struct_decoded": 0, "object_elements": 0, "struct_arrays": 0, "struct_array_records": 0,
           "struct_tagged": 0, "struct_native": 0, "compact_arrays": 0, "compact_elements": 0,
           "string_arrays": 0, "string_elements": 0,
           "struct_element_arrays": 0, "struct_element_records": 0}
    for r in vrows:
        n_arrays, obj, refused = int(r["arrays"]), int(r["object_arrays"]), int(r["arrays_refused"])
        sp, sd = int(r["struct_props"]), int(r["struct_decoded"])
        ca, sa = int(r["compact_arrays"]), int(r["string_arrays"])
        if obj + refused + ca + sa != n_arrays:
            problems.append("%s: %s says %d object + %d refused + %d compact + %d string != %d arrays"
                            % (values, r["map"], obj, refused, ca, sa, n_arrays))
        if sd > sp:
            problems.append("%s: %s decodes %d of %d structs" % (values, r["map"], sd, sp))
        if int(r["struct_tagged"]) + int(r["struct_native"]) != sd:
            problems.append("%s: %s decodes %d structs from %d tagged + %d native"
                            % (values, r["map"], sd, int(r["struct_tagged"]), int(r["struct_native"])))
        if int(r["compact_elements"]) < ca or int(r["string_elements"]) < sa:
            problems.append("%s: %s counts fewer elements than arrays" % (values, r["map"]))
        if int(r["struct_element_arrays"]) > int(r["struct_arrays"]):
            problems.append("%s: %s decodes %d tagged element arrays of %d struct arrays"
                            % (values, r["map"], int(r["struct_element_arrays"]), int(r["struct_arrays"])))
        if int(r["struct_element_arrays"]) and not int(r["struct_element_records"]):
            problems.append("%s: %s decodes tagged element arrays with no records" % (values, r["map"]))
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
    if sum(int(r["tagged"]) for r in srows) != tot["struct_tagged"]:
        problems.append("%s: tagged %d != per-map %d"
                        % (structs, sum(int(r["tagged"]) for r in srows), tot["struct_tagged"]))
    if sum(int(r["native"]) for r in srows) != tot["struct_native"]:
        problems.append("%s: native %d != per-map %d"
                        % (structs, sum(int(r["native"]) for r in srows), tot["struct_native"]))
    if any(int(r["tagged"]) and not r["fields"] for r in srows):
        problems.append("%s: a tagged struct row names no fields" % structs)
    # A struct name is a *type*, and the corpus declares 55 of them (`class_properties.csv`, a
    # `UStructProperty`'s friendly type). A name in this census that no declaration carries is a name-table
    # word a mislocated block start read as the tag's struct-name index -- the earlier reading of these 73
    # maps had 79 rows and 67 of them were actor instance names (`StaticMeshActor448`, `ACTION_Talk1`),
    # property names (`ColLocation`) and `X`/`Y`/`Z` read out of a payload. `?` is the one allowed residue:
    # it is a name index the table does not hold. This gate fails on that reading, which is what makes it a
    # gate and not a description.
    declared_types = struct_types()
    stray = [(r["struct"], int(r["properties"])) for r in srows
             if r["struct"] not in declared_types and r["struct"] != "?"
             and RESIDUE_STRUCTS.get(r["struct"]) != int(r["properties"])]
    if stray:
        problems.append("%s: %d struct name(s) no property declaration names (%d properties): %s"
                        % (structs, len(stray), sum(n for _s, n in stray),
                           ", ".join(s for s, _n in stray[:8])))
    if stats:
        crows = read_csv(stats)
        if [r["map"] for r in crows] != [r["map"] for r in vrows]:
            problems.append("%s: the map column disagrees with the values census" % stats)
        padded = [(r["map"], int(r.get("padded_blocks") or 0)) for r in crows if int(r.get("padded_blocks") or 0)]
        if sum(n for _m, n in padded) != 2 or [m for m, _n in padded] != ["M07A", "M07B"]:
            problems.append("%s: padded blocks %s, expected the two Cubemaps in M07A and M07B"
                            % (stats, padded))
        if not any(int(r.get("record_starts") or 0) for r in crows):
            problems.append("%s: no block was located through the prefix record" % stats)
        # The class whose block is followed by a native record: every `TerrainInfo` export is placed by that rule
        # and none by the exact-end one, so the count is the class's own export total (66 across the 73 maps).
        native = sum(int(r.get("native_tails") or 0) for r in crows)
        if native != NATIVE_TAIL_PIN:
            problems.append("%s: %d export(s) read through a native tail, expected %d (`TerrainInfo`)"
                            % (stats, native, NATIVE_TAIL_PIN))
        # The palette rule is a positive reading of a payload the writer never writes as an actor, so its
        # controls are the ones that would break silently: a refusal means the head/alpha rule stopped
        # matching, and a map with fewer colours than payloads would mean a count was read off the wrong byte.
        refused = sum(int(r.get("palette_refused") or 0) for r in crows)
        if refused:
            problems.append("%s: %d StaticMeshInstance payload(s) refuse the palette rule" % (stats, refused))
        if sum(int(r.get("palette_head2") or 0) for r in crows) > sum(int(r.get("palette_tails") or 0) for r in crows):
            problems.append("%s: more head-dword-2 tails than tails" % stats)
        if sum(int(r.get("palette_tails") or 0) for r in crows) > sum(int(r.get("palette_payloads") or 0) for r in crows):
            problems.append("%s: more tails than palette payloads" % stats)
        if sum(int(r.get("palette_payloads") or 0) for r in crows) == 0:
            problems.append("%s: no palette payload measured" % stats)
        fewer = [r["map"] for r in crows
                 if int(r.get("palette_colours") or 0) < int(r.get("palette_payloads") or 0)]
        if fewer:
            problems.append("%s: %d map(s) count fewer colours than payloads" % (stats, len(fewer)))
        # The tail is a frame the class's serialiser fixes: a dword, a compact count, that many records, each
        # `[compact export index][compact length][bytes][dword 0 or 1]`. Every payload has to close on it now --
        # the 790 an earlier reading left over were the records whose flag is 0, not a different body shape.
        records = sum(int(r.get("palette_records") or 0) for r in crows)
        closed = sum(int(r.get("palette_closed") or 0) for r in crows)
        payloads = sum(int(r.get("palette_payloads") or 0) for r in crows)
        if records <= payloads:
            problems.append("%s: the palette tails carry no more records than payloads" % stats)
        if closed != payloads:
            problems.append("%s: %d of %d palette tails close on the record grammar, expected every one"
                            % (stats, closed, payloads))
        if sum(int(r.get("palette_records_flag0") or 0) for r in crows) > records:
            problems.append("%s: more flag-0 records than records" % stats)
        # A record's middle member is a bitmask over the payload's colour table: `ceil(colours/8)` bytes on
        # every record of the corpus. A mask of another length is a record read against the wrong count, so
        # the gate is zero and the two controls are the total and the padded last bytes.
        masks = sum(int(r.get("palette_masks") or 0) for r in crows)
        bad_masks = sum(int(r.get("palette_mask_bad") or 0) for r in crows)
        if bad_masks:
            problems.append("%s: %d palette record(s) whose mask is not ceil(colours/8) bytes"
                            % (stats, bad_masks))
        if masks != PALETTE_MASKS:
            problems.append("%s: %d palette record mask(s), expected %d" % (stats, masks, PALETTE_MASKS))
        if masks != records:
            problems.append("%s: %d masks for %d records" % (stats, masks, records))
        if sum(int(r.get("palette_mask_bytes") or 0) for r in crows) <= masks:
            problems.append("%s: mask bytes do not average one per record" % stats)
        if sum(int(r.get("palette_pad_bits") or 0) for r in crows) > masks:
            problems.append("%s: more padded masks than masks" % stats)
        # The `Level` trailer is a `FURL` and the level's saved time: `7777`/`1` on every map and a float that
        # equals that map's own `LevelInfo.TimeSeconds`. A map that stops agreeing is a trailer read off the
        # wrong byte, not a map saved at another time.
        ports = [r["map"] for r in crows if int(r.get("level_tail_port") or 0) != LEVEL_TAIL_PORT]
        if ports:
            problems.append("%s: %d map(s) whose Level trailer is not the URL frame: %s"
                            % (stats, len(ports), ", ".join(ports[:6])))
        matched = sum(int(r.get("level_seconds_match") or 0) for r in crows)
        if matched != LEVEL_SECONDS_MATCH:
            problems.append("%s: the Level trailer's float equals LevelInfo.TimeSeconds on %d maps, expected %d"
                            % (stats, matched, LEVEL_SECONDS_MATCH))
        zeros = sum(int(r.get("level_tail_zero") or 0) for r in crows)
        if zeros != LEVEL_TAIL_ZERO:
            problems.append("%s: %d zero bytes after the Level trailer's float, expected %d"
                            % (stats, zeros, LEVEL_TAIL_ZERO))
        shifts = {}
        for r in crows:
            off = int(r.get("level_seconds_off") or 0)
            if off:
                shifts[off] = shifts.get(off, 0) + 1
        if shifts != LEVEL_SECONDS_SHIFTS:
            problems.append("%s: the Level trailer's float sits at %s, expected %s"
                            % (stats, shifts or "no offset", LEVEL_SECONDS_SHIFTS))
        # The compact reference before that float names the level's `Model`: its class is `Model` and it is
        # the largest `Model` export on every map, and its own width is what moves the float on `Entry`.
        wrong_class = [r["map"] for r in crows if (r.get("level_model_class") or "?") != LEVEL_MODEL_CLASS]
        if wrong_class:
            problems.append("%s: %d map(s) whose trailer reference is not a Model: %s"
                            % (stats, len(wrong_class), ", ".join(wrong_class[:6])))
        biggest = sum(int(r.get("level_model_biggest") or 0) for r in crows)
        if biggest != LEVEL_MODEL_BIGGEST:
            problems.append("%s: the trailer names the largest Model export on %d maps, expected %d"
                            % (stats, biggest, LEVEL_MODEL_BIGGEST))
        moved = [r["map"] for r in crows
                 if int(r.get("level_model_width") or 0) != int(r.get("level_seconds_off") or 0)]
        if moved:
            problems.append("%s: %d map(s) whose float offset is not the reference's width: %s"
                            % (stats, len(moved), ", ".join(moved[:6])))
        # A record's first field is an export index: it has to land in the package's export table, and the class
        # it names is the actor whose light the record bakes. `palette_ref_other` is the control -- it is not
        # asserted zero, because an `InterpolationPoint`/`PathNode` is an actor too, only that it stays small.
        refs = sum(int(r.get("palette_refs") or 0) for r in crows)
        if refs != records:
            problems.append("%s: %d of %d records name an export index" % (stats, refs, records))
        light = sum(int(r.get("palette_ref_light") or 0) for r in crows)
        other = sum(int(r.get("palette_ref_other") or 0) for r in crows)
        if light + other != refs:
            problems.append("%s: light %d + other %d != %d references" % (stats, light, other, refs))
        if other * 100 > refs:
            problems.append("%s: %d of %d palette records do not name a light" % (stats, other, refs))
        if other != PALETTE_REF_OTHER:
            problems.append("%s: %d palette records name something other than a light, expected %d"
                            % (stats, other, PALETTE_REF_OTHER))
        seen = {}
        for r in crows:
            for part in (r.get("palette_other_classes") or "").split(";"):
                if not part:
                    continue
                cls, _sep, n = part.rpartition(":")
                seen[cls] = seen.get(cls, 0) + int(n)
        if seen != PALETTE_OTHER_CLASSES:
            problems.append("%s: the non-light palette references name %s, expected %s"
                            % (stats, seen or "nothing", PALETTE_OTHER_CLASSES))
        # The head dword's per-map histogram: every tail is counted once, and the corpus keeps its shape.
        heads, single = {}, 0
        for r in crows:
            hist = {}
            for part in (r.get("palette_heads") or "").split(";"):
                if not part:
                    continue
                val, _sep, n = part.rpartition(":")
                hist[int(val)] = int(n)
                heads[int(val)] = heads.get(int(val), 0) + int(n)
            if hist and sum(hist.values()) != int(r.get("palette_tails") or 0):
                problems.append("%s: %s counts %d tails but the map has %s"
                                % (stats, r["map"], sum(hist.values()), r.get("palette_tails")))
            if len(hist) == 1:
                single += 1
        if len(heads) != PALETTE_HEAD_VALUES:
            problems.append("%s: the palette head takes %d values (%s), expected %d"
                            % (stats, len(heads), sorted(heads), PALETTE_HEAD_VALUES))
        if single != PALETTE_HEAD_SINGLE:
            problems.append("%s: %d map(s) carry one palette head value, expected %d"
                            % (stats, single, PALETTE_HEAD_SINGLE))
        # Every map's `Level` export names its own actors, and the writer writes each one it names: a map
        # whose level names an actor the writer skipped is the writer's bug, not a census question.
        bare = [r["map"] for r in crows if int(r.get("level_actors") or 0) <= 0]
        if bare:
            problems.append("%s: %d map(s) read no actor list from the Level export: %s"
                            % (stats, len(bare), ", ".join(bare[:6])))
        odd = [r["map"] for r in crows if int(r.get("level_strings") or 0) != LEVEL_STRINGS]
        if odd:
            problems.append("%s: %d map(s) whose Level trailer is not %d strings: %s"
                            % (stats, len(odd), LEVEL_STRINGS, ", ".join(odd[:6])))
        unwritten = sum(int(r.get("level_unwritten") or 0) for r in crows)
        if unwritten != LEVEL_UNWRITTEN:
            problems.append("%s: the Level exports name %d actor(s) the writer does not write, expected %d "
                            "(`TerrainInfo` is written from its block at the prefix record's end)"
                            % (stats, unwritten, LEVEL_UNWRITTEN))
        # The terrain record ends with the terrain's vertex-colour array, so a `TerrainInfo` that has a native
        # tail and no colour array is a payload whose tail stopped being read. Every count is a square -- the
        # grid -- and the colours have to fit inside the record they were read from.
        closes = sum(int(r.get("terrain_colour_exports") or 0) for r in crows)
        if closes != TERRAIN_COLOUR_EXPORTS:
            problems.append("%s: %d export(s) close on the terrain vertex-colour array, expected %d"
                            % (stats, closes, TERRAIN_COLOUR_EXPORTS))
        short = [r["map"] for r in crows
                 if int(r.get("native_tails") or 0) and not int(r.get("terrain_colour_exports") or 0)]
        if short:
            problems.append("%s: %d map(s) with a native terrain tail and no colour array: %s"
                            % (stats, len(short), ", ".join(short[:6])))
        grids, stray = set(), set()
        for r in crows:
            for token in (r.get("terrain_grids") or "").split(";"):
                if not token:
                    continue
                n = int(token)
                grids.add(n)
                if int(n ** 0.5) ** 2 != n:
                    stray.add(n)
        if stray:
            problems.append("%s: terrain colour counts that are not a square grid: %s"
                            % (stats, ", ".join(str(n) for n in sorted(stray))))
        if grids != TERRAIN_GRIDS:
            problems.append("%s: terrain colour grids %s, expected %s"
                            % (stats, sorted(grids), sorted(TERRAIN_GRIDS)))
        room = [r["map"] for r in crows
                if int(r.get("terrain_bytes") or 0) < 4 * int(r.get("terrain_colours") or 0)]
        if room:
            problems.append("%s: %d map(s) whose terrain record is shorter than its colours: %s"
                            % (stats, len(room), ", ".join(room[:6])))
        # The body between the block and the colours is a member chain (`terrain_body`), so every record
        # with a native tail has to close on it, its three per-vertex counts have to be the colours' count,
        # its sector list has to name only `TerrainSector` exports, and its tail ints have to be one more
        # than the furthest sector edge (`terrain_span`).
        closed = sum(int(r.get("terrain_body") or 0) for r in crows)
        if closed != TERRAIN_BODY_EXPORTS:
            problems.append("%s: %d terrain record(s) close on the body's member chain, expected %d"
                            % (stats, closed, TERRAIN_BODY_EXPORTS))
        verts = sum(int(r.get("terrain_verts") or 0) for r in crows)
        records = sum(int(r.get("terrain_records") or 0) for r in crows)
        tot_colours = sum(int(r.get("terrain_colours") or 0) for r in crows)
        if not verts == records == tot_colours:
            problems.append("%s: %d terrain positions, %d per-vertex records, %d vertex colours -- expected "
                            "the same count three times" % (stats, verts, records, tot_colours))
        sectors = sum(int(r.get("terrain_sectors") or 0) for r in crows)
        named = sum(int(r.get("terrain_sector_refs") or 0) for r in crows)
        if sectors != TERRAIN_SECTORS or named != TERRAIN_SECTORS:
            problems.append("%s: %d sector reference(s) (%d naming a TerrainSector), expected %d"
                            % (stats, sectors, named, TERRAIN_SECTORS))
        spanned = sum(int(r.get("terrain_span") or 0) for r in crows)
        if spanned != TERRAIN_BODY_EXPORTS:
            problems.append("%s: %d terrain tail(s) equal the sector list's furthest edge, expected %d"
                            % (stats, spanned, TERRAIN_BODY_EXPORTS))
        # The two 48-byte members: an export whose second frame is not the first one's inverse is a
        # payload whose frame pair stopped being readable, and the vertex spacing and the grid dwords are
        # the two measurements that separate the four large terrains from the other 62.
        frames = sum(int(r.get("terrain_frames") or 0) for r in crows)
        if frames != TERRAIN_FRAME_EXPORTS:
            problems.append("%s: %d terrain export(s) carry a frame and its inverse, expected %d"
                            % (stats, frames, TERRAIN_FRAME_EXPORTS))
        half = sum(int(r.get("terrain_half_grid") or 0) for r in crows)
        if half != TERRAIN_HALF_GRIDS:
            problems.append("%s: %d terrain export(s) whose vertex grid is twice the grid dwords, expected %d"
                            % (stats, half, TERRAIN_HALF_GRIDS))
        steps = {}
        for r in crows:
            for token in (r.get("terrain_steps") or "").split(";"):
                if token:
                    steps[token] = steps.get(token, 0) + 1
        if steps != TERRAIN_STEPS:
            problems.append("%s: vertex spacing / frame scale %s, expected %s"
                            % (stats, sorted(steps.items()), sorted(TERRAIN_STEPS.items())))
        payloads = sum(int(r.get("sector_payloads") or 0) for r in crows)
        sector_named = sum(int(r.get("sector_named") or 0) for r in crows)
        if payloads != TERRAIN_SECTORS or sector_named != TERRAIN_SECTORS:
            problems.append("%s: %d sector payload(s) read (%d naming a TerrainInfo), expected %d"
                            % (stats, payloads, sector_named, TERRAIN_SECTORS))
        quads = sum(int(r.get("sector_quads") or 0) for r in crows)
        if quads != TERRAIN_QUADS:
            problems.append("%s: the sector records cover %d quads, expected %d" % (stats, quads, TERRAIN_QUADS))
    # The element-type census partitions the refused arrays the values census counts: every refused array is
    # either unshaped or has a (count, width) layout and therefore one row here.
    arows = read_csv(arrays)
    if not arows:
        problems.append("%s: empty" % arrays)
    if sum(int(r["arrays"]) for r in arows) != tot["struct_arrays"]:
        problems.append("%s: %d arrays != the values census's %d struct arrays"
                        % (arrays, sum(int(r["arrays"]) for r in arows), tot["struct_arrays"]))
    if sum(int(r["records"]) for r in arows) != int(sum(int(r["struct_array_records"]) for r in vrows)):
        problems.append("%s: records do not add up to the values census's struct_array_records" % arrays)
    if any(r["element_type"] != "?" and not r["declared_by"] for r in arows):
        problems.append("%s: a declared element type does not name the class that declares it" % arrays)
    if any(not r["widths"] and not r["element_widths"] for r in arows):
        problems.append("%s: a row carries no element width" % arrays)
    if sum(int(r["tagged_elements"]) for r in arows) != tot["struct_element_arrays"]:
        problems.append("%s: %d tagged arrays != the values census's %d"
                        % (arrays, sum(int(r["tagged_elements"]) for r in arows), tot["struct_element_arrays"]))
    if any(int(r["tagged_elements"]) and not r["element_widths"] for r in arows):
        problems.append("%s: a tagged row carries no element width" % arrays)
    # The rule's bite is that nothing is left: every refused array either has a (count, width) layout or is a
    # count of tagged elements, so `arrays_refused == struct_arrays` and the `unshaped` residue is zero.
    if tot["arrays_refused"] != tot["struct_arrays"]:
        problems.append("%s: %d refused arrays are not struct arrays (unshaped: %d)"
                        % (values, tot["arrays_refused"], tot["arrays_refused"] - tot["struct_arrays"]))
    parent = {"Child": "Base", "Base": ""}
    types = {("Base", "Foo"): "array<int>", ("Base", "Bar"): "float"}
    if declared(parent, types, "Child", "Foo") != ("array<int>", "Base"):
        problems.append("declared() does not walk the super chain")
    if declared(parent, types, "Child", "Nope") != ("", ""):
        problems.append("declared() invents a declaration")
    if decode_elements(b"\x02" + struct.pack("<2i", 7, -8), ps, "int") != [7, -8]:
        problems.append("decode_elements does not decode two ints")
    if decode_elements(b"\x02" + struct.pack("<2i", 7, -8), ps, "byte") is not None:
        problems.append("decode_elements decodes an int payload as bytes")
    if decode_elements(b"\x01" + struct.pack("<3f", 1.0, 2.0, 3.0), ps, "Vector") != [(1.0, 2.0, 3.0)]:
        problems.append("decode_elements does not decode a Vector element")
    if fmt_element((1, 2, 3)) != "(Pitch=1,Yaw=2,Roll=3)":
        problems.append("fmt_element does not spell a Rotator element")
    if decode_obj_array(b"\x02\x01\x02", _Pkg(2), ps) != [1, 2]:
        problems.append("decode_obj_array refuses a well-formed two-element payload")
    if decode_obj_array(b"\x02\x01", _Pkg(2), ps) is not None:
        problems.append("decode_obj_array accepts a payload one element short")
    if decode_obj_array(b"\x01\x09", _Pkg(2), ps) is not None:
        problems.append("decode_obj_array accepts an index past the export table")
    # The element walk is driven with canned blocks: what is under test is the count, the exact consumption and
    # the two refusals, not the tag reader (whose own tests are `prop_stream`'s).
    class _ElemPs:
        def __init__(self, widths):
            self.widths = widths

        def read_index(self, buf, off):
            return buf[off], off + 1

        def read_block(self, buf, pkg, want_values=False, **kw):
            width = self.widths.get(buf[0])
            return ([{"name": "A", "type": "Int", "value": 1}], width, "None") if width else ([], 0, "?")

        def struct_fields(self, buf, pkg, native_width=None, depth=0):
            return [{"name": "A", "type": "Int", "value": 1}]

    two = b"\x02" + b"\x05\x00\x00\x00\x00" + b"\x06\x00\x00\x00\x00"
    if [w for w, _f in (decode_struct_elements(two, None, _ElemPs({5: 5, 6: 5})) or [])] != [5, 5]:
        problems.append("decode_struct_elements does not walk two tagged elements")
    if decode_struct_elements(two[:-1], None, _ElemPs({5: 5, 6: 5})) is not None:
        problems.append("decode_struct_elements accepts a payload one byte short")
    if decode_struct_elements(b"\x01\x07\x00\x00\x00\x00", None, _ElemPs({5: 5})) is not None:
        problems.append("decode_struct_elements accepts a block that is not tagged")
    if decode_struct_elements(b"\x00", None, _ElemPs({})) is not None:
        problems.append("decode_struct_elements accepts an empty payload")
    if element_layout(b"\x02" + b"\x00" * 8, ps) != (2, 4):
        problems.append("element_layout does not divide 8 bytes over 2 elements")
    # An array of compact indices is exactly the payload `element_layout` refuses, so the two rules have to be
    # driven on one buffer: 8 elements in 14 bytes is the shape `ZoneInfo.ManualExcludes` has.
    compact = b"\x08" + b"\x5a\x01\x50\x01\x46\x01\x4d\x01\x4a\x01\x40\x01" + b"\x2c\x07"
    if element_layout(compact, ps) is not None:
        problems.append("element_layout divides a compact-index array by its count")
    if decode_compact_array(compact, ps) != 8:
        problems.append("decode_compact_array does not read eight compact indices")
    if decode_compact_array(b"\x03\x01\x02", ps) is not None:
        problems.append("decode_compact_array accepts a payload one element short")
    if decode_compact_array(b"\x00", ps) is not None:
        problems.append("decode_compact_array accepts an empty payload")
    # The `StaticMeshInstance` tail's frame: a dword, a compact count, that many records, each
    # `[compact export index][compact length][bytes][dword flag]` with the flag 0 or 1.
    good_tail = (struct.pack("<I", 2) + b"\x02"
                 + b"\x03\x00" + b"\x01\x00\x00\x00"
                 + b"\x04\x01\xff" + b"\x01\x00\x00\x00")
    if smi_tail(good_tail, ps) != (2, 2, True, 0, [3, 4], 1, 0, 0):
        problems.append("smi_tail does not read two records")
    zero_flag = good_tail[:-4] + b"\x00\x00\x00\x00"
    if smi_tail(zero_flag, ps) != (2, 2, True, 1, [3, 4], 1, 0, 0):
        problems.append("smi_tail refuses a record whose flag dword is 0")
    if smi_tail(good_tail[:-4] + b"\x02\x00\x00\x00", ps) != (2, 2, False, 0, [3], 0, 0, 0):
        problems.append("smi_tail accepts a record flag that is neither 0 nor 1")
    if smi_tail(good_tail[:-4], ps) != (2, 2, False, 0, [3], 0, 0, 0):
        problems.append("smi_tail closes a tail whose last record has no terminator")
    # The middle member is a bitmask over the colour table: its length is `ceil(colours/8)` bytes for the
    # payload's colour count, so the same tail against 8, 9 and 16 colours is a right mask, a right mask, and
    # a wrong one. The spare bits of the last byte are padding: a mask that sets them is still a right mask.
    mask_tail = (struct.pack("<I", 2) + b"\x02"
                 + b"\x03\x01\x00" + b"\x01\x00\x00\x00"
                 + b"\x04\x01\xff" + b"\x01\x00\x00\x00")
    if smi_tail(mask_tail, ps, 8) != (2, 2, True, 0, [3, 4], 2, 0, 0):
        problems.append("smi_tail does not accept one-byte masks for eight colours")
    if smi_tail(mask_tail, ps) != (2, 2, True, 0, [3, 4], 2, 0, 0):
        problems.append("smi_tail measures masks without a colour count")
    if smi_tail(mask_tail, ps, 17) != (2, 2, True, 0, [3, 4], 2, 2, 0):
        problems.append("smi_tail counts 17 colours' masks as one byte")
    if smi_tail(mask_tail, ps, 4) != (2, 2, True, 0, [3, 4], 2, 0, 1):
        problems.append("smi_tail does not count a mask whose padding bits are set")
    if smi_tail(b"\x00\x00", ps) is not None:
        problems.append("smi_tail accepts a tail too short to read")
    # The `Level` payload's frame: an empty property block, the same count twice, that many export indices,
    # then a trailer. An index past the export table is a misread count, so it refuses.
    level = b"\x00" + struct.pack("<II", 2, 2) + b"\x01\x02" + b"\x05\x06"
    if level_record(level, _Pkg(2), ps) != (2, {1, 2}, 0, 2, None):
        problems.append("level_record does not read two actors and a two-byte trailer")
    # A trailer that begins with a string is read as one: the length counts the terminating NUL, and what
    # follows a length the payload cannot hold is the tail, not a string.
    if level_record(level[:-2] + b"\x02a\x00\x07\x00\x00", _Pkg(2), ps) != (2, {1, 2}, 1, 3, None):
        problems.append("level_record does not read a length-prefixed trailer string")
    # The trailer's 32 bytes: a `FURL` (`7777`, `1`), a two-byte value, a float and a zero run. The float is
    # the level's `TimeSeconds`; the reader has to hand back all four so the census can hold them.
    tail32 = struct.pack("<II", 7777, 1) + b"\x6c\x04" + struct.pack("<f", 59.005) + b"\x00" * 18
    if level_trailer(tail32, ps) != (7777, 1, 300, 2, struct.unpack("<f", struct.pack("<f", 59.005))[0],
                                     10, 18):
        problems.append("level_trailer does not read the URL frame, the reference, the float and the zero run")
    if level_trailer(tail32[:13], ps) is not None:
        problems.append("level_trailer accepts a trailer too short to hold the float")
    if level_trailer(tail32, ps)[4] != struct.unpack("<f", tail32[10:14])[0]:
        problems.append("level_trailer reads its float off the wrong byte")
    # A reference under 64 encodes in one byte, and then the float sits one byte earlier: the width is the
    # encoding's, not a different field -- which is the correction this bundle carries.
    short = struct.pack("<II", 7777, 1) + b"\x1b" + struct.pack("<f", 248.649) + b"\x00" * 18
    if level_trailer(short, ps)[3:] != (1, struct.unpack("<f", short[9:13])[0], 9, 18):
        problems.append("level_trailer does not read a one-byte reference as a one-byte width")
    if level_trailer(short, ps)[2] != 27:
        problems.append("level_trailer misreads a one-byte compact reference")
    if level_record(b"\x00" + struct.pack("<II", 2, 3) + b"\x01\x02", _Pkg(2), ps) is not None:
        problems.append("level_record accepts a count its second dword disagrees with")
    if level_record(b"\x00" + struct.pack("<II", 2, 2) + b"\x01\x07", _Pkg(2), ps) is not None:
        problems.append("level_record accepts an index past the export table")
    # A `Str` element carries its own length, and the length counts the terminating NUL: two strings of three
    # characters are `\x04abc\x00\x04def\x00`. A payload whose last element has no NUL must be refused.
    strings = b"\x02\x04abc\x00\x04def\x00"
    if element_layout(strings, ps) != (2, 5):
        problems.append("element_layout does not divide two equally long strings (the order matters)")
    if decode_string_array(strings, ps) != ["abc", "def"]:
        problems.append("decode_string_array does not read two length-prefixed strings")
    if decode_string_array(b"\x02\x04abc\x00\x04def", ps) is not None:
        problems.append("decode_string_array accepts an element with no terminator")
    # The palette record: a zero head, a compact count, and one `FColor` per entry. The third record's alpha is
    # not 0xff, so the same bytes with a wrong count must be refused rather than counted.
    pal = b"\x00\x02" + b"\x01\x02\x03\xff" + b"\x04\x05\x06\x00" + b"\x07\x08\x09"
    if palette_record(pal, ps) != (2, 1, 3):
        problems.append("palette_record does not read a two-colour table and its three-byte tail")
    if palette_record(b"\x00\x02" + b"\x01\x02\x03\xff" + b"\x04\x05\x06\x7f", ps) is not None:
        problems.append("palette_record accepts an alpha that is neither 0 nor 0xff")
    if palette_record(b"\x01" + pal[1:], ps) is not None:
        problems.append("palette_record accepts a payload whose head is not zero")
    if palette_record(b"\x00\x03" + pal[2:], ps) is not None:
        problems.append("palette_record counts a record whose alpha is not 0xff")
    if palette_record(b"\x00\x40" + b"\x00" * 8, ps) is not None:
        problems.append("palette_record accepts a count that runs past the payload")
    # The terrain record's tail: a compact count immediately before the vertex colours, whose alpha is 0xff
    # in every one of the 66 payloads. The count has to end where the colours start and equal their number,
    # so the same colours behind a different count are refused rather than read as a shorter array.
    colours = b"\x10\x20\x30\xff" * 3
    if terrain_colours(b"\x03" + colours, ps) != 3:
        problems.append("terrain_colours does not read three vertex colours behind their count")
    if terrain_colours(b"\x04" + colours, ps) is not None:
        problems.append("terrain_colours accepts a count that disagrees with the colours after it")
    if terrain_colours(colours, ps) is not None:
        problems.append("terrain_colours accepts a colour run with no count in front of it")
    if terrain_colours(colours[:-1] + b"\x00", ps) is not None:
        problems.append("terrain_colours counts a colour whose alpha is not 0xff")
    if terrain_colours(b"\x00", ps) is not None:
        problems.append("terrain_colours reads a tail with no colours")
    # The terrain body: one sector reference, two vertices, a 1x1 sector grid, two 24-byte records, a frame
    # and its inverse (48 bytes each, twelve floats) and the grid dwords (2, 2). Every count that disagrees
    # -- the positions not being the colours, a grid that does not count the sectors, a record list of the
    # wrong length, grid dwords that differ from each other, or a body one byte short -- has to be refused,
    # not read as a shorter body.
    def _frame(scale=(1.0, 2.0, 4.0), offset=(0.5, 0.25, 0.125), inverse=False):
        if inverse:
            return struct.pack(
                "<12f", -offset[0] * scale[0], -offset[1] * scale[1], -offset[2] * scale[2],
                1.0 / scale[0], 0.0, 0.0, 0.0, 1.0 / scale[1], 0.0, 0.0, 0.0, 1.0 / scale[2])
        return struct.pack("<12f", offset[0], offset[1], offset[2], scale[0], 0.0, 0.0, 0.0,
                           scale[1], 0.0, 0.0, 0.0, scale[2])

    def _tbody(nref=1, verts=2, grid=(1, 1), recs=2, dims=(2, 2), cut=0, inverse=True):
        return (bytes([nref]) + b"\x04" * nref if nref else b"\x00") + bytes([verts]) + \
            b"\x00" * (12 * verts) + struct.pack("<ii", *grid) + bytes([recs]) + \
            b"\x00" * (24 * recs) + _frame() + _frame(inverse=inverse) + \
            struct.pack("<ii", *dims) + b"\x02" + \
            b"\x01\x02\x03\xff" * 2 + b"\x00" * cut
    good = _tbody()
    body_ok = terrain_body(good, ps, 2)
    if body_ok is None or body_ok["verts"] != 2 or body_ok["grid"] != (1, 1) or body_ok["sectors"] != [4]:
        problems.append("terrain_body does not read a one-sector, two-vertex body: %s" % (body_ok,))
    if terrain_body(_tbody(verts=2, recs=1), ps, 2) is not None:
        problems.append("terrain_body accepts a record list shorter than the colours")
    if terrain_body(_tbody(grid=(2, 1)), ps, 2) is not None:
        problems.append("terrain_body accepts a sector grid that does not count its sectors")
    if terrain_body(_tbody(dims=(2, 3)), ps, 2) is not None:
        problems.append("terrain_body accepts tail ints that differ from each other")
    if terrain_body(good + b"\x00", ps, 2) is not None:
        problems.append("terrain_body accepts a body one byte past the colours")
    if terrain_body(_tbody()[:-9], ps, 2) is not None:
        problems.append("terrain_body accepts a body that stops before its colours")
    # The two 48-byte members are a frame and its inverse: the inverse's scale is the reciprocal of the
    # forward one and its offset is the forward offset times that scale. A pair that breaks either relation
    # is not an inverse, and the spacing test reads the vertex grid the frames sit beside.
    if not frame_inverse(terrain_body(good, ps, 2)["frames"]):
        problems.append("frame_inverse rejects the body's own frame pair")
    if frame_inverse(terrain_body(_tbody(inverse=False), ps, 2)["frames"]):
        problems.append("frame_inverse accepts a second frame that is not the first one's inverse")
    flipped = terrain_body(_tbody(inverse=False), ps, 2)["frames"]
    if frame_inverse((flipped[0], flipped[0])):
        problems.append("frame_inverse accepts a frame paired with itself")
    def _row(xs):
        return b"\x00" + b"".join(struct.pack("<3f", x, 0.0, 0.0) for x in xs)
    grid_x = [float(v) for v in (0, 10, 20, 0, 10, 20, 0, 10, 20)]
    if vertex_step(_row(grid_x), 1, 9, 10.0) != 1.0:
        problems.append("vertex_step does not read a grid whose spacing is the frame's own scale")
    if vertex_step(_row([v / 2 for v in grid_x]), 1, 9, 10.0) != 0.5:
        problems.append("vertex_step does not read a grid at half the frame's scale")
    if vertex_step(_row(grid_x), 1, 9, 0.0) is not None:
        problems.append("vertex_step divides by a zero scale")
    if vertex_step(_row([0.0, 10.0, 21.0] * 3), 1, 9, 10.0) is not None:
        problems.append("vertex_step reads a grid whose spacing is not even")
    # A sector's header names its terrain and gives its size and origin in quads: 8, 16 or 32, with the
    # origin a multiple of eight. A payload that opens another way is not a sector record.
    sector = b"\x00\x04" + struct.pack("<4i", 16, 15, 0, 48) + b"\x00" * 8
    if sector_record(sector, ps) != (4, 16, 15, 0, 48):
        problems.append("sector_record does not read a sector header")
    if sector_record(b"\x01" + sector[1:], ps) is not None:
        problems.append("sector_record accepts a payload that does not open on a zero")
    if sector_record(b"\x00\x04" + struct.pack("<4i", 33, 16, 0, 0) + b"\x00" * 8, ps) is not None:
        problems.append("sector_record accepts more quads than a sector can hold")
    if sector_record(b"\x00\x04" + struct.pack("<4i", 16, 16, 7, 0) + b"\x00" * 8, ps) is not None:
        problems.append("sector_record accepts an origin that is not a multiple of eight")
    # The tail ints are one more than the furthest sector edge, which is how the terrain body and the
    # sector headers agree with each other.
    if terrain_span([1, 2], {1: (1, 16, 16, 0, 0), 2: (1, 15, 15, 16, 16)}) != (32, 32):
        problems.append("terrain_span does not take the furthest sector edge and add one")
    if terrain_span([3], {1: (1, 16, 16, 0, 0)}) is not None:
        problems.append("terrain_span reads a span for a sector list it does not know")
    if fmt_struct([{"name": "Zone", "type": "Object", "value": "LevelInfo'LevelInfo0'"},
                   {"name": "iLeaf", "type": "Int", "value": -1}]) != "(Zone=\"LevelInfo'LevelInfo0'\",iLeaf=-1)":
        problems.append("fmt_struct does not spell a tagged struct's fields")
    if fmt_struct([{"name": "Points", "type": "Array", "value": b"\x00\x00"}]) is not None:
        problems.append("fmt_struct prints a struct whose field is an array")
    if struct_shape([{"name": "X", "type": "Struct", "struct": "Range"},
                     {"name": "Min", "type": "Float"}]) != "X:Struct<Range>+Min:Float":
        problems.append("struct_shape does not name a nested struct field")
    # The locator is the reader's, so these assertions are the delegation: the start `find_block` returned is
    # the one written, wherever in the prefix it sits, and no start is invented when it returns nothing. The
    # value test's own bite is `prop-stream`'s; `t3d_coverage.csv` counts the references a wrong start would
    # leave, so this file does not need a second rule to test.
    data = b"\x00\x01" + b"\x00" * 8
    clean = [{"name": "A", "type": "Object", "ref": 1}, {"name": "B", "type": "Int"}]
    stub = _Ps({0: clean}, 0)
    got = locate(data, _Pkg(2), stub, 17)
    if got is None or got[1] != 0 or len(got[2]) != len(clean):
        problems.append("locate does not write the block find_block chose")
    if stub.seen.get("preferred") != 17 or stub.seen.get("pad_tail") != 1 or stub.seen.get("window") != 64:
        problems.append("locate does not hand the prefix record and the tail to the reader: %s" % stub.seen)
    if got[3] != len(data) - len(data):
        problems.append("locate reports a tail where the block spends the export")
    got = locate(data, _Pkg(2), _Ps({1: clean}, 1))
    if got is None or got[1] != 1:
        problems.append("locate does not take a start the reader found past the first byte")
    if locate(data, _Pkg(2), _NoBlock()) is not None:
        problems.append("locate invents a block when the reader finds none")

    # A class whose block is followed by a native record: no candidate ends at the export's end, so the block
    # comes from the prefix record's end, the value test has to accept it, and the tail is what follows it.
    class _TailPs(_NoBlock):
        def read_block(self, buf, pkg, want_values=False):
            return ([{"name": "TerrainMap", "type": "Object", "ref": 1},
                     {"name": "TerrainScale", "type": "Struct"}], 3, "None")

        def block_bad(self, props, data, start, native_width=None):
            return 0, 1

    native = b"\x00" * 17 + b"\x01\x02\x03" + b"\x09" * 6
    got = locate(native, _Pkg(2), _TailPs(), 17, "TerrainInfo")
    if got is None or got[1] != 17 or got[3] != 6:
        problems.append("locate does not read a TerrainInfo block at the prefix record's end")
    if locate(native, _Pkg(2), _TailPs(), 17, "SomeOtherClass") is not None:
        problems.append("locate reads a native tail for a class that declares none")
    # The tail the writer counts is what `padded_blocks` sums, so drive the reader's own padded rule here: a
    # block at the first byte of an export one byte longer than it must come back with tail 1, and a second
    # zero-free byte must not.
    pkg = _Pkg(2)
    pkg.name = lambda i: ["None", "A", "B"][i] if i < 3 else "?"
    body = b"\x01\x22\x04\x00\x00\x00" + b"\x02\x22\x04\x00\x00\x00" + b"\x00"
    block = ps.padded_block(body + b"\x00", pkg, 2, 1)
    if not block or block[1] != 0 or len(block[2]) != 2:
        problems.append("padded_block does not read a block one zero byte short of the export")
    if ps.padded_block(body + b"\x07", pkg, 2, 1) is not None:
        problems.append("padded_block accepts a nonzero tail")
    if ps.padded_block(body, pkg, 2, 1) is not None:
        problems.append("padded_block claims a block that spends the whole export")
    if problems:
        for p in problems:
            print("FAIL %s" % p)
        return 1
    print("ok   t3d-values: %d maps, %d arrays (%d object with %d elements, %d refused), %d struct properties "
          "over %d struct names (%d declared), %d decoded"
          % (len(vrows), tot["arrays"], tot["object_arrays"], tot["object_elements"], tot["arrays_refused"],
             tot["struct_props"], len(srows), sum(1 for r in srows if r["struct"] in struct_types()),
             tot["struct_decoded"]))
    return 0


def render_doc(values, structs, arrays=ARRAYS, stats=COVERAGE):
    """The page's generated block: the array, struct and palette censuses, from the artifacts only."""
    vrows = read_csv(values)
    srows = read_csv(structs)
    arows = read_csv(arrays)
    crows = read_csv(stats)
    tot = {k: sum(int(r[k]) for r in vrows) for k in
           ("arrays", "object_arrays", "object_elements", "arrays_refused", "struct_arrays",
            "struct_array_records", "struct_props", "struct_decoded", "struct_raw", "struct_tagged",
            "struct_native", "compact_arrays", "compact_elements", "string_arrays", "string_elements",
            "struct_element_arrays", "struct_element_records")}
    ptot = {k: sum(int(r[k]) for r in crows) for k in
            ("palette_payloads", "palette_colours", "palette_clear", "palette_refused",
             "palette_tail_bytes", "palette_tails", "palette_head2", "palette_records",
             "palette_records_flag0", "palette_refs", "palette_ref_light", "palette_ref_other",
             "palette_closed", "palette_masks", "palette_mask_bytes", "palette_mask_bad",
             "palette_pad_bits", "level_actors", "level_strings", "level_tail_bytes", "level_unwritten",
             "level_tail_port", "level_seconds_match", "level_seconds_off", "level_tail_zero",
             "native_tails", "terrain_bytes", "terrain_colours", "terrain_colour_exports",
             "terrain_body", "terrain_sectors", "terrain_sector_refs", "terrain_verts",
             "terrain_records", "terrain_span", "sector_payloads", "sector_named", "sector_quads",
             "level_model_biggest")}
    # Two of the new columns carry a histogram rather than a number, so they are summed here rather than in
    # the comprehension above: one palette head value per map is the shape the census pins.
    ptot["palette_heads"], ptot["palette_other"], ptot["palette_head_single"] = {}, {}, 0
    for r in crows:
        hist = {}
        for part in (r.get("palette_heads") or "").split(";"):
            if part:
                val, _sep, n = part.rpartition(":")
                hist[int(val)] = int(n)
                ptot["palette_heads"][int(val)] = ptot["palette_heads"].get(int(val), 0) + int(n)
        if len(hist) == 1:
            ptot["palette_head_single"] += 1
        for part in (r.get("palette_other_classes") or "").split(";"):
            if part:
                cls, _sep, n = part.rpartition(":")
                ptot["palette_other"][cls] = ptot["palette_other"].get(cls, 0) + int(n)
    out = []
    out.append("| measure | value |")
    out.append("|---|---:|")
    out.append("| maps | %d |" % len(vrows))
    out.append("| `Array` properties | %d |" % tot["arrays"])
    out.append("| of those, object arrays | **%d** |" % tot["object_arrays"])
    out.append("| object references inside them | **%d** |" % tot["object_elements"])
    out.append("| arrays refused a (count, width) layout | %d (%d struct arrays holding %d records) |"
               % (tot["arrays_refused"], tot["struct_arrays"], tot["struct_array_records"]))
    out.append("| -- of those, a compact-index array | **%d** (%d element(s)) |"
               % (tot["compact_arrays"], tot["compact_elements"]))
    out.append("| -- of those, a string array | **%d** (%d element(s) decoded) |"
               % (tot["string_arrays"], tot["string_elements"]))
    out.append("| -- of the struct arrays, element type declared | **%d** |"
               % sum(int(r["arrays"]) for r in arows if r["element_type"] != "?"))
    out.append("| -- of those, elements decoded by a fixed width | **%d** |"
               % sum(int(r["records"]) for r in arows if r["element_type"] in ELEMENT))
    out.append("| -- of those, elements decoded as tagged blocks | **%d** arrays, **%d** records |"
               % (tot["struct_element_arrays"], tot["struct_element_records"]))
    out.append("| -- arrays no rule reaches | **%d** |" % (tot["arrays_refused"] - tot["struct_arrays"]))
    out.append("| struct-typed properties | %d |" % tot["struct_props"])
    out.append("| struct properties decoded | **%d** |" % tot["struct_decoded"])
    out.append("| -- native bodies (`Vector`, `Rotator`, `Color`) | %d |" % tot["struct_native"])
    out.append("| -- tagged blocks (fields read from the payload) | **%d** |" % tot["struct_tagged"])
    out.append("| struct property names | %d |" % len(srows))
    out.append("| `StaticMeshInstance` payloads | %d |" % ptot["palette_payloads"])
    out.append("| palette colours inside them | **%d** |" % ptot["palette_colours"])
    out.append("| records whose alpha is zero | %d |" % ptot["palette_clear"])
    out.append("| payloads the palette rule refuses | %d |" % ptot["palette_refused"])
    out.append("| native bytes after the palette | %d |" % ptot["palette_tail_bytes"])
    out.append("| exports whose block is followed by a native record | %d |" % ptot["native_tails"])
    out.append("| tails whose head dword is 2 | **%d** of %d |" % (ptot["palette_head2"], ptot["palette_tails"]))
    out.append("| records the tails count | **%d** |" % ptot["palette_records"])
    out.append("| -- records whose flag dword is 0 | %d |" % ptot["palette_records_flag0"])
    out.append("| -- records whose first field is an export index | **%d** of %d |"
               % (ptot["palette_refs"], ptot["palette_records"]))
    out.append("| -- of those, naming a light actor | **%d** (%d other) |"
               % (ptot["palette_ref_light"], ptot["palette_ref_other"]))
    out.append("| tails whose record list closes exactly | **%d** of %d |"
               % (ptot["palette_closed"], ptot["palette_payloads"]))
    out.append("| -- records whose mask is `ceil(colours/8)` bytes | **%d** of %d |"
               % (ptot["palette_masks"] - ptot["palette_mask_bad"], ptot["palette_masks"]))
    out.append("| -- mask bytes inside them | **%d** |" % ptot["palette_mask_bytes"])
    out.append("| -- masks with a set bit in the last byte's padding | %d |" % ptot["palette_pad_bits"])
    out.append("| `Level` exports read | **%d** |" % len(crows))
    out.append("| actors the `Level` exports name | **%d** |" % ptot["level_actors"])
    out.append("| strings in the `Level` trailer | **%d** over %d maps |"
               % (ptot["level_strings"], len(crows)))
    out.append("| bytes after them | %d |" % ptot["level_tail_bytes"])
    out.append("| -- of those, the `FURL` frame (`7777` and `1`) | **%d** maps |"
               % sum(1 for r in crows if int(r.get("level_tail_port") or 0) == LEVEL_TAIL_PORT))
    out.append("| -- the trailer float equals `LevelInfo.TimeSeconds` | **%d** of %d maps |"
               % (ptot["level_seconds_match"], len(crows)))
    out.append("| -- at the two-byte field's end / one byte earlier | **%d** / %d |"
               % (sum(1 for r in crows if int(r.get("level_seconds_off") or 0) == 2),
                  sum(1 for r in crows if int(r.get("level_seconds_off") or 0) == 1)))
    out.append("| -- zero bytes after the float | %d |" % ptot["level_tail_zero"])
    out.append("| -- the compact reference before the float names the largest `Model` export | **%d** of %d maps |"
               % (ptot["level_model_biggest"], len(crows)))
    out.append("| palette head dword: maps carrying one value / values it takes | **%d** of %d / %d |"
               % (ptot["palette_head_single"], len(crows), len(ptot["palette_heads"])))
    out.append("| -- palette references that name something other than a light | **%d** |"
               % ptot["palette_ref_other"])
    out.append("| -- their classes | %s |"
               % ", ".join("`%s` %d" % (c, n) for c, n in sorted(ptot["palette_other"].items(),
                                                                 key=lambda kv: -kv[1])))
    out.append("| actors a `Level` names that the writer does not write | **%d** |" % ptot["level_unwritten"])
    out.append("| native terrain record bytes after a `TerrainInfo` block | **%d** |" % ptot["terrain_bytes"])
    out.append("| -- exports whose record ends on a vertex-colour array | **%d** of %d |"
               % (ptot["terrain_colour_exports"], ptot["native_tails"]))
    out.append("| -- vertex colours inside them | **%d** |" % ptot["terrain_colours"])
    out.append("| terrain records read member by member | **%d** of %d |"
               % (ptot["terrain_body"], ptot["native_tails"]))
    out.append("| -- sector references, all naming a `TerrainSector` | **%d** (%d) |"
               % (ptot["terrain_sectors"], ptot["terrain_sector_refs"]))
    out.append("| -- vertex positions (12 bytes each) | **%d** |" % ptot["terrain_verts"])
    out.append("| -- per-vertex records (24 bytes each) | **%d** |" % ptot["terrain_records"])
    out.append("| -- records whose tail ints are the sector list's furthest edge | **%d** of %d |"
               % (ptot["terrain_span"], ptot["terrain_body"]))
    out.append("| `TerrainSector` payloads read | **%d** |" % ptot["sector_payloads"])
    out.append("| -- naming their `TerrainInfo` | **%d** |" % ptot["sector_named"])
    out.append("| -- quads they cover | **%d** |" % ptot["sector_quads"])
    out.append("")
    out.append("| struct | properties | decoded | tagged | native | sizes (bytes:instances) | fields |")
    out.append("|---|---:|---:|---:|---:|---|---|")
    for r in srows[:12]:
        fields = r["fields"].split(";")[0].rsplit(":", 1)[0] if r["fields"] else "--"
        out.append("| `%s` | %s | %s | %s | %s | %s | `%s` |"
                   % (r["struct"], r["properties"], r["decoded"], r["tagged"], r["native"],
                      r["sizes"].replace(";", " / "), fields))
    out.append("")
    out.append("| array element type | declared on | arrays | records | tagged arrays | "
               "element widths (bytes:records) | payload fields |")
    out.append("|---|---|---:|---:|---:|---|---|")
    for r in arows[:12]:
        et = "*(not declared)*" if r["element_type"] == "?" else "`%s`" % r["element_type"]
        widths = r["element_widths"] or r["widths"]
        if r["widths"]:
            widths = "%s (arrays)" % r["widths"].replace(";", " / ")
        else:
            widths = widths.replace(";", " / ")
        shape = r["shapes"].split(";")[0].rsplit(":", 1)[0] if r["shapes"] else "--"
        out.append("| %s | %s | %s | %s | %s | %s | `%s` |"
                   % (et, ("`%s`" % r["declared_by"]) if r["declared_by"] else "--", r["arrays"],
                      r["records"], r["tagged_elements"], widths, shape))
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
    ap.add_argument("--stats", metavar="CSV", default=COVERAGE,
                    help="per-map counts (default: inventory/t3d_coverage.csv); counts only, so it may live in "
                         "inventory/")
    ap.add_argument("--values", metavar="CSV", default=VALUES,
                    help="per-map array/struct census (default: inventory/t3d_values.csv)")
    ap.add_argument("--structs", metavar="CSV", default=STRUCTS,
                    help="per-struct census (default: inventory/t3d_structs.csv)")
    ap.add_argument("--arrays", metavar="CSV", default=ARRAYS,
                    help="per-element-type census (default: inventory/t3d_arrays.csv)")
    ap.add_argument("--check", action="store_true",
                    help="assert the two censuses agree with each other and drive the reader (no dump)")
    ap.add_argument("--doc", default=os.path.normpath(os.path.join(HERE, "..", "docs", "T3D.md")),
                    help="the page whose generated block this renders (default docs/T3D.md)")
    ap.add_argument("--write-doc", action="store_true", help="render the block into --doc")
    ap.add_argument("--check-doc", action="store_true", help="assert --doc's block equals the artifacts'")
    args = ap.parse_args()

    if args.check_doc:
        return write_doc(args.doc, render_doc(args.values, args.structs, args.arrays), check_only=True)
    if args.write_doc:
        return write_doc(args.doc, render_doc(args.values, args.structs, args.arrays))
    if args.check:
        return check(args.values, args.structs, args.stats, args.arrays)

    if not args.path:
        sys.exit("a .unr file or a directory is required to write T3D")
    if not args.out:
        sys.exit("--out is required to write T3D")
    if "/work/" not in args.out and not args.out.startswith(os.path.expanduser("~/sg1/work")):
        print("note: writing into %s — this is game content, keep it under $WORK" % args.out)

    ps = load_prop_stream()
    prec = load_prefix_record()
    paths = ([os.path.join(args.path, f) for f in sorted(os.listdir(args.path)) if f.lower().endswith(".unr")]
             if os.path.isdir(args.path) else [args.path])
    if args.limit:
        paths = paths[: args.limit]

    total = {"actors": 0, "bytes": 0, "props": 0, "unresolved": 0}
    rows, vrows, arrays = [], [], {}
    structs = {}
    parent, types = load_declarations()
    for path in paths:
        got = drive_map(path, args.out, ps, parent, types, args.verbose, prec)
        for key in total:
            total[key] += got[key]
        rows.append((got["map"], got["actors"], got["props"], got["unresolved"], got["bytes"],
                     got["values"]["record_starts"], got["values"]["padded_blocks"],
                     got["values"]["palette_payloads"], got["values"]["palette_colours"],
                     got["values"]["palette_clear"], got["values"]["palette_refused"],
                     got["values"]["palette_tail"],
                     got["values"]["palette_tails"], got["values"]["palette_head2"],
                     got["values"]["palette_records"], got["values"]["palette_records_flag0"],
                     got["values"]["palette_refs"], got["values"]["palette_ref_light"],
                     got["values"]["palette_ref_other"], got["values"]["palette_closed"],
                     got["values"]["level_actors"], got["values"]["level_strings"],
                     got["values"]["level_tail_bytes"], got["values"]["level_unwritten"],
                     got["values"]["native_tails"], got["values"]["terrain_bytes"],
                     got["values"]["terrain_colours"], got["values"]["terrain_colour_exports"],
                     got["values"]["terrain_body"], got["values"]["terrain_sectors"],
                     got["values"]["terrain_sector_refs"], got["values"]["terrain_verts"],
                     got["values"]["terrain_records"], got["values"]["terrain_span"],
                     got["values"]["sector_payloads"], got["values"]["sector_named"],
                     got["values"]["sector_quads"], got["values"]["terrain_grids"],
                     got["values"]["terrain_frames"], got["values"]["terrain_half_grid"],
                     ";".join(got["values"]["terrain_steps"]),
                     got["values"]["palette_masks"], got["values"]["palette_mask_bytes"],
                     got["values"]["palette_mask_bad"], got["values"]["palette_pad_bits"],
                     got["values"]["level_tail_port"], got["values"]["level_tail_prefix"],
                     got["values"]["level_tail_zero"], got["values"]["level_seconds_match"],
                     got["values"]["level_seconds_off"],
                     ";".join("%d:%d" % (h, n) for h, n in sorted(got["values"]["palette_heads"].items())),
                     ";".join("%s:%d" % (c, n) for c, n in sorted(got["values"]["palette_other"].items())),
                     len(got["values"]["palette_heads"]), got["values"]["level_model_class"],
                     got["values"]["level_model_biggest"], got["values"]["level_model_width"]))
        v = got["values"]
        vrows.append((got["map"], v["arrays"], v["object_arrays"], v["object_elements"],
                      v["struct_arrays"] + v["unshaped"], v["struct_arrays"], v["struct_records"],
                      v["struct_props"], v["struct_decoded"], v["struct_raw"], v["struct_tagged"],
                      v["struct_native"], v["compact_arrays"], v["compact_elements"],
                      v["string_arrays"], v["string_elements"],
                      v["struct_element_arrays"], v["struct_element_records"]))
        for key, row in got["arrays"].items():
            agg = array_row(arrays, key[0], key[1])
            agg["arrays"] += row["arrays"]
            agg["records"] += row["records"]
            agg["tagged"] += row["tagged"]
            for width, n in row["widths"].items():
                agg["widths"][width] = agg["widths"].get(width, 0) + n
            for width, n in row["tagged_widths"].items():
                agg["tagged_widths"][width] = agg["tagged_widths"].get(width, 0) + n
            for shape, n in row["shapes"].items():
                agg["shapes"][shape] = agg["shapes"].get(shape, 0) + n
        for sn, row in got["structs"].items():
            agg = structs.setdefault(sn, {"n": 0, "sizes": {}, "decoded": 0, "tagged": 0,
                                          "native": 0, "shapes": {}})
            agg["n"] += row["n"]
            agg["decoded"] += row["decoded"]
            agg["tagged"] += row["tagged"]
            agg["native"] += row["native"]
            for size, n in row["sizes"].items():
                agg["sizes"][size] = agg["sizes"].get(size, 0) + n
            for shape, n in row["shapes"].items():
                agg["shapes"][shape] = agg["shapes"].get(shape, 0) + n
        print("%-22s actors=%-6d props=%-6d unresolved=%-4d arrays=%-5d obj=%-5d elems=%-6d struct=%-5d"
              % (got["map"], got["actors"], got["props"], got["unresolved"], v["arrays"], v["object_arrays"],
                 v["object_elements"], v["struct_props"]))
    if args.stats:
        with open(args.stats, "w", encoding="utf-8", newline="") as fh:
            fh.write("map,actors,properties,unresolved_refs,t3d_bytes,record_starts,padded_blocks,"
                     "palette_payloads,palette_colours,palette_clear,palette_refused,palette_tail_bytes,"
                     "palette_tails,palette_head2,palette_records,palette_records_flag0,"
                     "palette_refs,palette_ref_light,palette_ref_other,palette_closed,"
                     "level_actors,level_strings,level_tail_bytes,level_unwritten,native_tails,"
                     "terrain_bytes,terrain_colours,terrain_colour_exports,"
                     "terrain_body,terrain_sectors,terrain_sector_refs,terrain_verts,terrain_records,"
                     "terrain_span,sector_payloads,sector_named,sector_quads,terrain_grids,"
                     "terrain_frames,terrain_half_grid,terrain_steps,"
                     "palette_masks,palette_mask_bytes,palette_mask_bad,palette_pad_bits,"
                     "level_tail_port,level_tail_prefix,level_tail_zero,level_seconds_match,"
                     "level_seconds_off,palette_heads,palette_other_classes,palette_head_values,"
                     "level_model_class,level_model_biggest,level_model_width\n")
            for row in rows:
                fh.write(("%s" + ",%d" * 36 + ",%s" + ",%d,%d,%s" + ",%d" * 9 + ",%s,%s,%d,%s,%d,%d\n")
                         % row)
        print("stats: %s (%d rows)" % (args.stats, len(rows)))
    with open(args.values, "w", encoding="utf-8", newline="") as fh:
        fh.write("map,arrays,object_arrays,object_elements,arrays_refused,struct_arrays,"
                 "struct_array_records,struct_props,struct_decoded,struct_raw,struct_tagged,"
                 "struct_native,compact_arrays,compact_elements,string_arrays,string_elements,"
                 "struct_element_arrays,struct_element_records\n")
        for r in vrows:
            fh.write("%s,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d\n" % r)
    print("values: %s (%d rows)" % (args.values, len(vrows)))
    with open(args.structs, "w", encoding="utf-8", newline="") as fh:
        fh.write("struct,properties,decoded,tagged,native,sizes,fields\n")
        for sn in sorted(structs, key=lambda k: -structs[k]["n"]):
            row = structs[sn]
            sizes = ";".join("%d:%d" % (s, n) for s, n in sorted(row["sizes"].items(), key=lambda kv: -kv[1]))
            shapes = ";".join("%s:%d" % (sh, n) for sh, n in sorted(row["shapes"].items(),
                                                                    key=lambda kv: -kv[1]))
            fh.write("%s,%d,%d,%d,%d,%s,%s\n" % (sn, row["n"], row["decoded"], row["tagged"], row["native"],
                                                 sizes, shapes))
    print("structs: %s (%d rows)" % (args.structs, len(structs)))
    with open(args.arrays, "w", encoding="utf-8", newline="") as fh:
        fh.write("element_type,declared_by,arrays,records,widths,tagged_elements,element_widths,"
                 "shapes\n")
        for key in sorted(arrays, key=lambda k: (-arrays[k]["arrays"], k[0], k[1])):
            row = arrays[key]
            widths = ";".join("%d:%d" % (w, n) for w, n in sorted(row["widths"].items(), key=lambda kv: -kv[1]))
            ewidths = ";".join("%d:%d" % (w, n) for w, n in sorted(row["tagged_widths"].items(),
                                                                   key=lambda kv: -kv[1]))
            shapes = ";".join("%s:%d" % (sh, n) for sh, n in sorted(row["shapes"].items(),
                                                                    key=lambda kv: -kv[1]))
            fh.write("%s,%s,%d,%d,%s,%d,%s,%s\n"
                     % (key[0] or "?", key[1], row["arrays"], row["records"], widths, row["tagged"],
                        ewidths, shapes))
    print("arrays: %s (%d rows)" % (args.arrays, len(arrays)))
    print("\nmaps: %d, actors: %d, properties: %d, unresolved refs: %d, t3d bytes: %d, out: %s"
          % (len(paths), total["actors"], total["props"], total["unresolved"], total["bytes"], args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
