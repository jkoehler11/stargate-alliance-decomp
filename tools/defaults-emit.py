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
import struct
import sys

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


def render_ref(pkg, ref, meta):
    got = ref_name(pkg, ref)
    if got is None:
        return "None"
    cls, package, name = got
    if not cls or cls == "?":
        return None
    object_name = "%s.%s" % (package, name) if package else name
    if meta and meta[1].lower() in ("class",) or (meta and meta[0] == "UClassProperty"):
        return "class'%s'" % object_name
    return "%s'%s'" % (cls, object_name)


def render_value(pkg, entry, meta, buf):
    """(text, note) for one decoded property; text is None when the type is not rendered."""
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
        if sn in ("Vector",) and isinstance(v, tuple) and len(v) == 3:
            return "(X=%s,Y=%s,Z=%s)" % tuple(format_float(x) for x in v), "vector"
        if sn == "Rotator" and isinstance(v, tuple) and len(v) == 3:
            return "(Pitch=%d,Yaw=%d,Roll=%d)" % v, "rotator"
        if sn == "Color" and isinstance(v, tuple) and len(v) == 4:
            return "(R=%d,G=%d,B=%d,A=%d)" % v, "color"
        if sn == "Plane" and isinstance(v, tuple) and len(v) == 4:
            return "(X=%s,Y=%s,Z=%s,W=%s)" % tuple(format_float(x) for x in v), "plane"
        return None, "struct"
    if t in ("Vector",):
        return None, "vector"
    if t == "Array":
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

def block_of(pkg, cls, supers, declared_all, limit=512, want_score=False):
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
           "arrays", "structs", "other", "bytes", "bytes_resolved",
           "floats", "ints", "bools", "names", "strs", "objects", "geometry"]


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


def render_class(pkg, cls, exp, supers, declared_all, props_meta, enums, row, notes):
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
        text, note = render_value(pkg, p, meta, buf)
        if text is not None and note == "byte":
            row["bytes"] += 1
            enum_name = meta[1].split(".")[-1] if meta and "." in (meta[1] or "") else None
            elems = enums.get(enum_name) if enum_name else None
            if elems and 0 <= p["value"] < len(elems):
                text = elems[p["value"]]
                row["bytes_resolved"] += 1
        if text is None:
            key = {"array": "arrays", "struct": "structs"}.get(note, "other")
            row[key] += 1
            notes[note] += 1
            lines.append("// unrendered %s (%s)" % (p["name"], note))
            continue
        row["rendered"] += 1
        key = {"Float": "floats", "Int": "ints", "Bool": "bools", "Name": "names", "Str": "strs",
               "String": "strs", "Object": "objects", "Class": "objects"}.get(p["type"])
        if key is None and note in ("vector", "rotator", "color", "plane"):
            key = "geometry"
        if key:
            row[key] += 1
        if p.get("array_index"):
            lines.append("%s(%d)=%s" % (p["name"], p["array_index"], text))
        else:
            lines.append("%s=%s" % (p["name"], text))
    return lines, "clean"


def sweep(system_dir, outdir, emit_dir=None, show_class=None, merge_dir=None):
    supers = cd.super_map()
    declared_all = cd.declared_properties()
    props_meta = property_meta()
    enums, enum_conflicts = global_enums(system_dir)
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
            lines, status = render_class(pkg, cls, exp, supers, declared_all, props_meta, enums, row, notes)
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
          "%d rendered (%.1f%%); %d arrays, %d structs, %d other; %d of %d byte values named from an enum"
          % (tot["classes"], tot["blocks"], tot["clean_blocks"], tot["dirty_properties"], tot["properties"],
             tot["rendered"], 100.0 * tot["rendered"] / tot["properties"] if tot["properties"] else 0.0,
             tot["arrays"], tot["structs"], tot["other"], tot["bytes_resolved"], tot["bytes"]))
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

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="the dump's System directory")
    ap.add_argument("--sweep", action="store_true", help="measure every package")
    ap.add_argument("-o", "--out", help="directory for class_defaults_emit.csv (usually inventory)")
    ap.add_argument("--emit", metavar="DIR", help="write one .uc snippet per class into DIR ($WORK only)")
    ap.add_argument("--merge", metavar="TREE",
                    help="append each block to TREE/<Pkg>/<Class>.uc (the tree `make extract-script` writes)")
    ap.add_argument("--verify", action="store_true", help="compare against the shipped text blocks")
    ap.add_argument("--check", action="store_true", help="recompute and diff against the committed CSV")
    ap.add_argument("--class", dest="cls", help="limit --show to this class")
    ap.add_argument("--package", dest="pkg", help="the class's package (with --class)")
    ap.add_argument("--show", action="store_true", help="print one class's block (game data: explicit)")
    args = ap.parse_args()

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
