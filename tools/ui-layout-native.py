#!/usr/bin/env python3
"""What `WinLeft`/`WinTop`/`WinWidth`/`WinHeight` mean: the native conversion, and the corpus's values.

`GUIComponent` is a `native` class: the four `Win*` floats are script data, and the code that turns
them into pixels is C++.  `docs/UNKNOWNS.md` U27 left the composition open -- whether `WinLeft` scales
by the parent's width, whether a value above 1 is absolute, and where the owner's offset enters.  This
tool answers it twice, from the two halves that can be re-derived:

* **the image** (`--xbe`): the eight conversions are `native final` declarations with token 0, so each
  is bound by name to an `intUGUIComponentexec<Name>` thunk.  The thunk parses the optional float and
  tail-calls the *class-table slot*, which is where the arithmetic is.  Every row records the slot, the
  `Win*` field the body defaults to, the `-1.0f` "argument omitted" sentinel, the band constant that
  separates a fraction from a pixel, the type byte and the values that opt an axis into the parent's
  box, the convert bit, and the parent slot the body calls for the composed origin.

* **the corpus** (`--defaults`): every `Begin Object` block `defaults-emit` renders -- 135 page
  components and the panel classes' own children -- with its four `Win*` values, the band each value
  falls in, and whether its class chain opts that axis into the parent's box (`BoundingType` /
  `ScalingType`, whose default is `SCALE_None`, i.e. the screen).

The two artifacts are `inventory/ui_layout_native.csv` and `inventory/ui_layout_values.csv`;
`--write-doc` renders the page's generated block from them.

    python3 tools/ui-layout-native.py DUMPDIR --defaults $WORK/ui-def -o inventory
    python3 tools/ui-layout-native.py --write-doc docs/systems/UI.md
    python3 tools/ui-layout-native.py --check          # synthetic, no dump, writes nothing
"""
import argparse
import csv
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import xbe as X  # noqa: E402

# GUIComponent's own class table (`inventory/xbe_class_table_names.csv`); slots 35-42 hold the
# eight conversions.  The name of a slot is *derived from its body*, not assumed from its position:
# the body names the `Win*` field it defaults to, and whether it recurses into its own slot on the
# owner (the `Actual*` family) or calls the owner's `Actual*` pair (the `Relative*` family).
TABLE_VA = 0x536648
TABLE_SLOTS = 58
FIRST_SLOT = 35
FUNCS = ("ActualLeft", "ActualTop", "ActualWidth", "ActualHeight",
         "RelativeLeft", "RelativeTop", "RelativeWidth", "RelativeHeight")
# `Win*` field -> (name suffix, kind, axis).  Measured from the `movss` a body substitutes for an
# omitted float argument, i.e. at the `-1.0f` sentinel.
AXIS = {
    0x110: ("Left", "position", "x"),
    0x10C: ("Top", "position", "y"),
    0x114: ("Width", "size", "w"),
    0x118: ("Height", "size", "h"),
}
WIN_FIELD = {name: off for off, (suffix, _k, _a) in sorted(AXIS.items()) for name in
             ("Actual" + suffix, "Relative" + suffix)}
# the two BoundingType/ScalingType values that opt an axis into the parent's box (SCALE_* 1..3)
BOUND_VALUES = {"position": {"x": (1, 3), "y": (2, 3)}, "size": {"w": (1, 3), "h": (2, 3)}}
BODY_WINDOW = 0x180
ARTIFACT = os.path.join(ROOT, "inventory", "ui_layout_native.csv")
VALUES = os.path.join(ROOT, "inventory", "ui_layout_values.csv")
DOC_START = "<!-- generated:ui-layout -->"
DOC_END = "<!-- /generated:ui-layout -->"

NATIVE_FIELDS = ("function", "kind", "direction", "thunk_va", "slot", "slot_va", "body_span",
                 "win_offset", "sentinel", "band", "type_offset", "type_values", "convert_mask",
                 "parent_slots", "resolution_offset", "controller_offset", "owner_offset")
VALUE_FIELDS = ("kind", "package", "owner", "block", "block_class", "left", "top", "width", "height",
                "left_band", "top_band", "width_band", "height_band",
                "bounding", "scaling", "x_space", "y_space", "w_space", "h_space")
BAND = 2.0          # |value| < BAND is a fraction; >= BAND is pixels
SENTINEL = -1.0     # the exec thunk's default for an omitted float argument


# ------------------------------------------------------------ instruction shapes

def modrm_ops(buf, prefix, reg=None):
    """`(offset, modrm, disp)` for every `prefix <modrm> [disp]` in `buf`, decodable forms only.

    Only the displacement forms this image's compiler emits are accepted -- `mod=01` (disp8) and
    `mod=10` (disp32) -- because every pattern here is a memory operand with a base register.  A
    `reg` filter selects one of the eight opcode extensions (`/0` = `test`, `/2` = `call`).
    """
    out, i = [], 0
    while True:
        i = buf.find(prefix, i)
        if i < 0:
            return out
        m = i + len(prefix)
        if m >= len(buf):
            return out
        modrm = buf[m]
        mod, rm = modrm >> 6, modrm & 7
        if reg is not None and ((modrm >> 3) & 7) != reg:
            i += 1
            continue
        if mod == 1 and m + 2 <= len(buf):
            out.append((i, modrm, buf[m + 1]))
        elif mod == 2 and m + 5 <= len(buf):
            out.append((i, modrm, struct.unpack("<I", buf[m + 1:m + 5])[0]))
        elif mod == 0 and rm == 5 and m + 5 <= len(buf):
            out.append((i, modrm, struct.unpack("<I", buf[m + 1:m + 5])[0]))
        i += 1


def imm_after(buf, offset, prefix_len, modrm):
    """The offset of the immediate that follows a `prefix <modrm> disp` operand."""
    return offset + prefix_len + 1 + (1 if (modrm >> 6) == 1 else 4)


def u32(buf, off):
    if off + 4 > len(buf):
        return None
    return struct.unpack("<I", buf[off:off + 4])[0]


def u8(buf, off):
    return buf[off] if off < len(buf) else None


def pattern_cmp(buf, opcode):
    """Immediates of `cmp al,imm8` (`3c XX`) in a body."""
    return sorted({buf[i + 1] for i, b in enumerate(buf[:-1]) if b == opcode})


def route_table_slot(buf):
    """`call [reg*4+disp]` in an exec thunk -- the opcode-route table, not a class-table slot.

    The eight `intUGUIComponentexec<Name>` thunks are byte-identical in shape: they store the
    `-1.0f` sentinel, read the frame's opcode byte and tail-call `[byte*4 + 0x6313e8]`.  So a thunk
    cannot name its class-table slot, and the slot has to be read from the table instead.  The form
    is `ff 14 95 <disp32>` -- a SIB byte with no base, which the displacement reader above does not
    model, so this is a plain byte scan.
    """
    i = buf.find(bytes((0xFF, 0x14, 0x95)))
    if i < 0 or i + 7 > len(buf):
        return None
    return struct.unpack("<I", buf[i + 3:i + 7])[0]


def floats_at(img, abs_va):
    buf = img.read_va(abs_va, 4)
    return struct.unpack("<f", buf[:4])[0] if len(buf) >= 4 else None


def modrm_disp(buf, prefix, reg=None):
    """Displacements of `prefix <modrm> [disp]` where the displacement is a 32-bit constant.

    `modrm_ops` accepts the disp8 form too; every pattern here is looked up by a known 32-bit
    displacement, and a disp8 would read one byte of the displacement's low half as a value.
    """
    return [d for _o, modrm, d in modrm_ops(buf, prefix, reg=reg) if (modrm >> 6) in (2, 0)]


def convert_mask(body):
    """The `bConvert*` bit: `test dword [reg+0x7c],imm32` or `test byte [reg+0x7f],imm8`."""
    for off, modrm, disp in modrm_ops(body, b"\xf7", reg=0):
        if disp == 0x7C:
            imm = u32(body, imm_after(body, off, 1, modrm))
            if imm:
                return imm
    for off, modrm, disp in modrm_ops(body, b"\xf6", reg=0):
        if disp == 0x7F:
            v = u8(body, imm_after(body, off, 1, modrm))
            if v:
                return v << 24
    return 0


def body_at(img, slots, slot):
    """One slot body: bounded by the next-higher body start and by its `int3` pad.

    A fixed window is not a body -- these eight bodies are 149-234 bytes, so 0x180 of the next one's
    `cmp al,...` and `call [reg+disp]` bytes land in the read and every count comes out inflated.
    """
    start = slots[slot]
    higher = [slots[s] for s in range(FIRST_SLOT, FIRST_SLOT + len(FUNCS))
              if s != slot and slots[s] > start]
    end = min(higher) if higher else start + BODY_WINDOW
    raw = img.read_va(start, min(end - start, BODY_WINDOW))
    i = raw.find(b"\xcc\xcc")
    if i >= 0:
        j = i
        while j < len(raw) and raw[j] == 0xCC:
            j += 1
        raw = raw[:i]
    return raw


def read_native(img, slots, slot, thunks):
    """Measure one conversion from its class-table slot: the field it defaults to, its constants."""
    slot_va = slots[slot]
    body = body_at(img, slots, slot)
    row = {"slot": slot, "slot_va": "0x%06x" % slot_va, "body_span": len(body)}

    # the field the body substitutes for an omitted argument (`movss xmm?, [reg+disp]`)
    wins = [d for d in modrm_disp(body, b"\xf3\x0f\x10") if d in AXIS]
    if not wins:
        row.update({"function": "", "kind": "", "thunk_va": "", "win_offset": "",
                    "error": "no Win* field"})
        return row
    suffix, kind, axis = AXIS[wins[0]]
    row.update({"kind": kind, "win_offset": "0x%x" % wins[0]})

    # the parent's own slot(s) the body calls for the composed origin or extent
    calls = sorted({d // 4 for d in modrm_disp(body, b"\xff", reg=2) if 0x88 <= d <= 0xa0})
    family = "Actual" if slot in calls else "Relative"
    name = family + suffix
    row.update({"function": name, "parent_slots": ",".join(str(s) for s in calls),
                "direction": "fraction->pixel" if family == "Actual" else "pixel->fraction",
                "thunk_va": ("0x%06x" % thunks[name]) if name in thunks else ""})

    # the sentinel and the band constant, both absolute floats the body compares against
    sent = [d for d in modrm_disp(body, b"\x0f\x2e") if d >= 0x400000]
    row["sentinel"] = ("%g" % floats_at(img, sent[0])) if sent else ""
    band = ([d for d in modrm_disp(body, b"\x0f\x2f") if 0x4c0000 <= d <= 0x600000] or
            [d for d in modrm_disp(body, b"\xf3\x0f\x10") if 0x4c0000 <= d <= 0x600000])
    row["band"] = ("%g" % floats_at(img, band[0])) if band else ""

    # the type byte (`mov al,byte ptr [reg+disp]`) and the values compared against it
    typ = [d for d in modrm_disp(body, b"\x8a", reg=0) if 0x80 <= d <= 0xa0]
    row["type_offset"] = "0x%x" % typ[0] if typ else ""
    row["type_values"] = ",".join(str(v) for v in pattern_cmp(body, 0x3C) if 1 <= v <= 3)

    mask = convert_mask(body)
    row["convert_mask"] = ("0x%x" % mask) if mask else ""

    # the controller field, the owner field, and the resolution ints on the controller
    ctrl = [d for _o, _m, d in modrm_ops(body, b"\x8b") if d == 0x28]
    own = [d for _o, _m, d in modrm_ops(body, b"\x8b") if d == 0x30]
    row["controller_offset"] = "0x%x" % ctrl[0] if ctrl else ""
    row["owner_offset"] = "0x%x" % own[0] if own else ""
    res = [d for d in modrm_disp(body, b"\xdb", reg=0) if 0x200 <= d <= 0x240] or \
          [d for d in modrm_disp(body, b"\xf3\x0f\x2a") if 0x200 <= d <= 0x240]
    row["resolution_offset"] = "0x%x" % res[0] if res else ""
    return row


def native_half(xbe_path):
    img = X.Xbe(xbe_path)
    raw = img.read_va(TABLE_VA, TABLE_SLOTS * 4)
    slots = list(struct.unpack("<%dI" % TABLE_SLOTS, raw[:TABLE_SLOTS * 4]))
    thunks = {}
    path = os.path.join(ROOT, "inventory", "native_bodies.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("class") == "GUIComponent" and r.get("function") in FUNCS:
                    thunks[r["function"]] = int(r["body_va"], 16)
    rows = [read_native(img, slots, slot, thunks)
            for slot in range(FIRST_SLOT, FIRST_SLOT + len(FUNCS))]
    got = sorted(r["function"] for r in rows if r["function"])
    if thunks and got != sorted(thunks):
        sys.exit("FAIL ui-layout: the class table derives %s, the package declares %s"
                 % (",".join(got) or "nothing", ",".join(sorted(thunks))))
    return sorted(rows, key=lambda r: (r["function"] not in FUNCS, FUNCS.index(r["function"])
                                       if r["function"] in FUNCS else 0, r["slot"]))


def check_native(rows):
    """Every row has to show the whole shape, or the page's formula no longer holds."""
    bad = []
    names = [r["function"] for r in rows if r["function"]]
    if sorted(names) != sorted(FUNCS):
        bad.append("%d of %d conversions derived (%s)" % (len(names), len(FUNCS),
                                                          ",".join(names) or "none"))
    for r in rows:
        if r.get("error"):
            bad.append("slot %s: %s" % (r.get("slot"), r["error"]))
            continue
        name = r["function"]
        if name not in FUNCS:
            continue
        _suffix, kind, axis = AXIS[WIN_FIELD[name]]
        if r.get("win_offset") != "0x%x" % WIN_FIELD[name]:
            bad.append("%s: win_offset %s" % (name, r.get("win_offset")))
        if r.get("sentinel") != "%g" % SENTINEL:
            bad.append("%s: sentinel %s" % (name, r.get("sentinel")))
        if r.get("band") != "%g" % BAND:
            bad.append("%s: band %s" % (name, r.get("band")))
        vals = [int(v) for v in (r.get("type_values") or "").split(",") if v]
        if not set(BOUND_VALUES[kind][axis]) <= set(vals):
            bad.append("%s: type values %s" % (name, r.get("type_values")))
        if not r.get("convert_mask"):
            bad.append("%s: no convert bit" % name)
        calls = [int(s) for s in (r.get("parent_slots") or "").split(",") if s]
        if not calls:
            bad.append("%s: no parent slot" % name)
        elif (name.startswith("Actual") and int(r["slot"]) not in calls) or \
                (name.startswith("Relative") and int(r["slot"]) in calls):
            bad.append("%s: family does not match its calls %s" % (name, r.get("parent_slots")))
    return bad


# --------------------------------------------------------------- corpus half

BLOCK = re.compile(r"^\s*Begin Object Class=(\w+) Name=(\w+)\s*$")
END = re.compile(r"^\s*End Object\s*$")
WIN = re.compile(r"^\s*(WinLeft|WinTop|WinWidth|WinHeight)=(-?[0-9.]+)\s*$")
NAMES = {"SCALE_None": 0, "SCALE_X": 1, "SCALE_Y": 2, "SCALE_All": 3}


def band(v):
    if v is None:
        return ""
    return "fraction" if abs(v) < BAND else "pixel"


def enum_value(text):
    text = text.strip()
    if text in NAMES:
        return NAMES[text]
    try:
        return int(text, 0)
    except ValueError:
        return 0


def defaults_files(defaults):
    for base, _dirs, files in os.walk(defaults):
        for f in sorted(files):
            if f.endswith(".defaultproperties.uc"):
                yield os.path.join(base, f)


def class_types(defaults):
    """`{class: (BoundingType, ScalingType)}` for the classes whose block states one."""
    out = {}
    for path in defaults_files(defaults):
        cls = os.path.basename(path).split(".defaultproperties.uc")[0].lower()
        b = s = None
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                t = line.strip()
                if t.startswith("BoundingType="):
                    b = enum_value(t.split("=", 1)[1])
                elif t.startswith("ScalingType="):
                    s = enum_value(t.split("=", 1)[1])
        if b is not None or s is not None:
            out[cls] = (b, s)
    return out


def supers():
    path = os.path.join(ROOT, "inventory", "classes.csv")
    out = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out[r["class"].lower()] = (r.get("super") or "").lower()
    return out


def resolve(cls, types, sup):
    """Walk the chain for the first class that states a type; the unstated default is SCALE_None."""
    seen = set()
    cur = cls.lower()
    b = s = None
    while cur and cur not in seen:
        seen.add(cur)
        if cur in types:
            tb, ts = types[cur]
            b = tb if b is None else b
            s = ts if s is None else s
        if b is not None and s is not None:
            break
        cur = sup.get(cur, "")
    return (0 if b is None else b, 0 if s is None else s)


def space(kind, t, axis):
    """`parent` when the axis's type includes it, `screen` otherwise."""
    if kind == "position":
        vals = (1, 3) if axis == "x" else (2, 3)
    else:
        vals = (1, 3) if axis == "w" else (2, 3)
    return "parent" if t in vals else "screen"


def fmt(v):
    return "" if v is None else ("%g" % v)


def value_row(pkg, owner, block, cls, vals, types, sup):
    b, s = resolve(cls, types, sup)
    l, t = vals.get("WinLeft"), vals.get("WinTop")
    w, h = vals.get("WinWidth"), vals.get("WinHeight")
    return {"kind": "block", "package": pkg, "owner": owner, "block": block, "block_class": cls,
            "left": fmt(l), "top": fmt(t), "width": fmt(w), "height": fmt(h),
            "left_band": band(l), "top_band": band(t), "width_band": band(w), "height_band": band(h),
            "bounding": b, "scaling": s,
            "x_space": space("position", b, "x"), "y_space": space("position", b, "y"),
            "w_space": space("size", s, "w"), "h_space": space("size", s, "h")}


def corpus_half(defaults):
    types, sup = class_types(defaults), supers()
    rows = []
    for path in defaults_files(defaults):
        pkg = os.path.basename(os.path.dirname(path))
        owner = os.path.basename(path).split(".defaultproperties.uc")[0]
        block = cls = None
        vals = {}
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = BLOCK.match(line)
                if m:
                    block, cls, vals = m.group(2), m.group(1), {}
                    continue
                if END.match(line):
                    if block:
                        rows.append(value_row(pkg, owner, block, cls, vals, types, sup))
                    block = None
                    continue
                if block:
                    w = WIN.match(line)
                    if w:
                        vals[w.group(1)] = float(w.group(2))
    return rows


def census_row(rows):
    def n(pred):
        return sum(1 for r in rows if pred(r))
    return {
        "kind": "census", "package": "", "owner": "", "block": "", "block_class": "",
        "left": n(lambda r: r["left"]), "top": n(lambda r: r["top"]),
        "width": n(lambda r: r["width"]), "height": n(lambda r: r["height"]),
        "left_band": n(lambda r: r["left_band"] == "fraction"),
        "top_band": n(lambda r: r["top_band"] == "fraction"),
        "width_band": n(lambda r: r["width_band"] == "fraction"),
        "height_band": n(lambda r: r["height_band"] == "fraction"),
        "bounding": n(lambda r: r["bounding"] != 0),
        "scaling": n(lambda r: r["scaling"] != 0),
        "x_space": n(lambda r: r["x_space"] == "parent"),
        "y_space": n(lambda r: r["y_space"] == "parent"),
        "w_space": n(lambda r: r["w_space"] == "parent"),
        "h_space": n(lambda r: r["h_space"] == "parent"),
    }


# -------------------------------------------------------------------- report

def summarize(native, values):
    body = [r for r in values if r["kind"] == "block"]
    tot = {}
    for axis in ("left", "top", "width", "height"):
        tot[axis] = (sum(1 for r in body if r[axis]),
                     sum(1 for r in body if r[axis + "_band"] == "fraction"),
                     sum(1 for r in body if r[axis + "_band"] == "pixel"))
    in12 = 0
    for r in body:
        for axis in ("left", "top", "width", "height"):
            if r[axis] and r[axis + "_band"] == "fraction" and abs(float(r[axis])) >= 1.0:
                in12 += 1
    return {"bodies": len(native), "blocks": len(body),
            "classes": len({r["block_class"] for r in body}), "tot": tot, "in12": in12,
            "parent_x": sum(1 for r in body if r["x_space"] == "parent"),
            "parent_y": sum(1 for r in body if r["y_space"] == "parent"),
            "parent_w": sum(1 for r in body if r["w_space"] == "parent"),
            "parent_h": sum(1 for r in body if r["h_space"] == "parent"),
            "types": sum(1 for r in body if r["bounding"] or r["scaling"])}


def report(native, values):
    s = summarize(native, values)
    print("ui layout: %(bodies)d conversions over %(blocks)d blocks in %(classes)d classes" % s)
    for axis in ("left", "top", "width", "height"):
        n, fr, px = s["tot"][axis]
        print("  %-6s %4d stated: %4d fraction, %3d pixel" % (axis, n, fr, px))
    print("  values in [1,2) that still scale: %(in12)d" % s)
    print("  parent-relative axes: x %(parent_x)d, y %(parent_y)d, w %(parent_w)d, h %(parent_h)d; "
          "%(types)d blocks state a type" % s)


# ---------------------------------------------------------------- doc block

def render(native, values):
    s = summarize(native, values)
    out = ["The composition is C++ and the corpus is data; both are re-derived here.", ""]
    out.append("| conversion | slot | body | direction | `Win*` field | sentinel | fraction band | "
               "type values | convert bit | calls on the owner |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in native:
        out.append("| `%s` | %s | `%s` | %s | `%s` | %s | %s | %s | %s | %s |"
                   % (r["function"], r.get("slot", ""), r.get("slot_va", ""), r.get("direction", ""),
                      r.get("win_offset", ""), r.get("sentinel", ""), r.get("band", ""),
                      r.get("type_values", ""), r.get("convert_mask", ""),
                      (r.get("parent_slots", "") or "-")))
    out.append("")
    out.append("`%d` blocks over `%d` classes, each axis counted where the block states a value:"
               % (s["blocks"], s["classes"]))
    out.append("")
    out.append("| axis | stated | fraction (`abs < %g`) | pixel (`abs >= %g`) | parent-relative |"
               % (BAND, BAND))
    out.append("|---|---|---|---|---|")
    for axis in ("left", "top", "width", "height"):
        n, fr, px = s["tot"][axis]
        parent = s[{"left": "parent_x", "top": "parent_y", "width": "parent_w",
                   "height": "parent_h"}[axis]]
        out.append("| %s | %d | %d | %d | %d |" % (axis, n, fr, px, parent))
    out.append("")
    out.append("`%d` stated values sit in `[1, 2)` -- above the unit square, below the pixel band, so "
               "they still scale." % s["in12"])
    return "\n".join(out)


def write_doc(path, native, values):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("%s: no ui-layout markers" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    new = head + DOC_START + "\n" + render(native, values) + "\n" + DOC_END + tail
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("wrote %s" % path)


def check_doc(path, native, values):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("FAIL ui-layout: %s has no markers" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    if head + DOC_START + "\n" + render(native, values) + "\n" + DOC_END + tail != text:
        sys.exit("FAIL ui-layout: %s's block is stale (run --write-doc)" % path)
    print("ok   ui-layout: %s regenerates byte-identically" % path)


# ------------------------------------------------------------------ storage

def write_csv(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\r\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("wrote %s (%d rows)" % (path, len(rows)))


def load(path, fields):
    if not os.path.exists(path):
        sys.exit("%s: missing (run the tool against the dump first)" % path)
    with open(path, newline="", encoding="utf-8") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


# ---------------------------------------------------------------- self-test

def selftest():
    """The pattern readers over synthetic bodies: no dump, no writes."""
    body = (b"\xf3\x0f\x10\x89" + struct.pack("<I", 0x110) +          # movss xmm1,[ecx+0x110]
            b"\x0f\x2e\x0d" + struct.pack("<I", 0x4DABA0) +            # ucomiss xmm1,[-1.0]
            b"\xf3\x0f\x10\x0d" + struct.pack("<I", 0x4D21C0) +        # movss xmm1,[2.0]
            b"\xf7\x41\x7c" + struct.pack("<I", 0x200000) +            # test [ecx+0x7c],0x200000
            b"\x8a\x81" + struct.pack("<I", 0x95) +                    # mov al,[ecx+0x95]
            b"\x3c\x03\x74\x02\x3c\x01" +                              # cmp al,3 / cmp al,1
            b"\xff\x92" + struct.pack("<I", 0x8c))                     # call [edx+0x8c]
    wins = [d for _o, _m, d in modrm_ops(body, b"\xf3\x0f\x10", reg=1) if 0x100 <= d <= 0x140]
    assert wins[:1] == [0x110], wins
    assert [d for d in modrm_disp(body, b"\x0f\x2e") if d >= 0x400000] == [0x4DABA0]
    assert [d for d in modrm_disp(body, b"\xf3\x0f\x10") if 0x4c0000 <= d <= 0x600000] == [0x4D21C0]
    rel = b"\x0f\x2f\x0d" + struct.pack("<I", 0x4D21C0)
    assert [d for d in modrm_disp(rel, b"\x0f\x2f") if 0x4c0000 <= d <= 0x600000] == [0x4D21C0]
    assert [d for d in modrm_disp(body, b"\x8a", reg=0) if 0x80 <= d <= 0xa0] == [0x95]
    assert [v for v in pattern_cmp(body, 0x3C) if 1 <= v <= 3] == [1, 3]
    assert convert_mask(body) == 0x200000
    assert route_table_slot(b"\xff\x14\x95\xe8\x13\x63\x00") == 0x6313E8
    t = body + b"\xcc\xcc\xcc" + b"\x3c\x02"
    assert t.find(b"\xcc\xcc") == len(body) and t[:len(body)] == body
    assert modrm_disp(body, b"\xf3\x0f\x10", reg=None)[:1] == [0x110]
    assert convert_mask(b"\xf7\x41\x7c\x00\x00\x00\x00") == 0
    # the byte-shaped convert bit is the same dword bit 24
    b2 = b"\xf6\x41\x7f\x01"
    for off, modrm, disp in modrm_ops(b2, b"\xf6", reg=0):
        assert (u8(b2, imm_after(b2, off, 1, modrm)) or 0) << 24 == 0x1000000
    assert band(1.5) == "fraction" and band(-2.5) == "pixel" and band(None) == ""
    assert space("position", 3, "x") == "parent" and space("position", 0, "x") == "screen"
    assert space("size", 2, "h") == "parent" and space("size", 1, "w") == "parent"
    assert enum_value("SCALE_Y") == 2 and enum_value("3") == 3
    r = value_row("p", "o", "c", "GUIButton", {"WinLeft": 0.05, "WinTop": 2.5}, {}, {})
    assert r["left_band"] == "fraction" and r["top_band"] == "pixel" and r["width_band"] == ""
    assert (r["bounding"], r["scaling"]) == (0, 0) and r["x_space"] == "screen"
    r = value_row("p", "o", "c", "SGLabel", {}, {"sglabel": (3, 3)}, {"sglabel": "guilabel"})
    assert r["x_space"] == "parent" and r["h_space"] == "parent"
    r = value_row("p", "o", "c", "SGLabel2", {}, {"guilabel": (None, 3)}, {"sglabel2": "guilabel"})
    assert r["bounding"] == 0 and r["scaling"] == 3 and r["y_space"] == "screen" and r["h_space"] == "parent"
    print("ok   ui-layout: pattern readers, banding, type resolution and the doc renderer")


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", nargs="?", help="the game directory (or its parent)")
    ap.add_argument("--defaults", default="", help="the defaults-emit --emit tree")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        selftest()
        native = load(ARTIFACT, NATIVE_FIELDS)
        bad = check_native(native)
        if bad:
            sys.exit("FAIL ui-layout: " + "; ".join(bad))
        values = load(VALUES, VALUE_FIELDS)
        print("ok   ui-layout: %d conversions keep their shape, %d value rows" % (len(native), len(values)))
        return

    if args.write_doc:
        write_doc(args.write_doc, load(ARTIFACT, NATIVE_FIELDS), load(VALUES, VALUE_FIELDS))
        return
    if args.check_doc:
        check_doc(args.check_doc, load(ARTIFACT, NATIVE_FIELDS), load(VALUES, VALUE_FIELDS))
        return

    if not args.dump:
        sys.exit("a dump directory is required to measure")
    game = args.dump.rstrip("/")
    if not os.path.exists(os.path.join(game, "default.xbe")):
        game = os.path.join(game, "Stargate_SG-1_-_The_Alliance-2005-12-15")
    native = native_half(os.path.join(game, "default.xbe"))
    bad = check_native(native)
    if bad:
        sys.exit("FAIL ui-layout: the image no longer shows the composition: " + "; ".join(bad))
    values = corpus_half(args.defaults) if args.defaults else []
    if values:
        values.append(census_row(values))
    write_csv(os.path.join(args.out, "ui_layout_native.csv"), NATIVE_FIELDS, native)
    if values:
        write_csv(os.path.join(args.out, "ui_layout_values.csv"), VALUE_FIELDS, values)
    report(native, values)


if __name__ == "__main__":
    main()
