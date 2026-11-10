#!/usr/bin/env python3
"""Decode the D3DCAPS8 the D3D8 runtime reports, out of a `tools/xemu/bp-args.py` memory window.

U11 asked which D3D8 caps this build uses. The *request* is arguments at `CreateDevice` (PLAN 2.6, measured);
what the device *reports* is a 53-dword `D3DCAPS8` the render device keeps as a member. `bp-args.py` can already
record that window (`--dump "esi+0x4000:0x300"`); this tool gives the window its meaning, because every field's
identity is positional — a dword at `+0x58` is `MaxTextureWidth` only because the 22 dwords before it are what
they are.

    python3 tools/xemu/d3d-caps.py --log $WORK/bp1.log --base 0x02384000 --caps 0x19c
    python3 tools/xemu/d3d-caps.py --log $WORK/bp1.log --base 0x02384000 --caps 0x19c --markdown

`--caps` is the offset of the struct **inside** the dumped window (the window above is `esi+0x4000`, and the
struct sits at `esi+0x419c`), so the two numbers together name the same bytes from both sides: `--base` is the
address `bp-args.py` printed after the `@`, `--caps` is what the disassembly's `test BYTE PTR [esi+0x4238],0x2`
says about where the struct sits. A window that does not look
like caps fails loudly instead of decoding into plausible nonsense: `DeviceType` must be a valid `D3DDEVTYPE`
and `MaxTextureWidth`/`MaxTextureHeight` must be non-zero powers of two.

Only the public D3D8 bit names are expanded (`D3DVTXPCAPS_*`); every other field is printed raw, because naming a
bit this repository cannot source would be a guess dressed as a measurement.
"""
import argparse
import json
import os
import re
import struct
import sys

DUMP = re.compile(r"dump\s+\S+\s+@0x([0-9a-fA-F]+):\s*(.*)$")

# D3DCAPS8, in SDK order. `f` marks the float fields, `x` the ones printed as raw bits only.
FIELDS = [
    ("DeviceType", "u"), ("AdapterOrdinal", "u"), ("Caps", "x"), ("Caps2", "x"), ("Caps3", "x"),
    ("PresentationIntervals", "x"), ("CursorCaps", "x"), ("DevCaps", "x"), ("PrimitiveMiscCaps", "x"),
    ("RasterCaps", "x"), ("ZCmpCaps", "x"), ("SrcBlendCaps", "x"), ("DestBlendCaps", "x"),
    ("AlphaCmpCaps", "x"), ("ShadeCaps", "x"), ("TextureCaps", "x"), ("TextureFilterCaps", "x"),
    ("CubeTextureFilterCaps", "x"), ("VolumeTextureFilterCaps", "x"), ("TextureAddressCaps", "x"),
    ("VolumeTextureAddressCaps", "x"), ("LineCaps", "x"), ("MaxTextureWidth", "u"), ("MaxTextureHeight", "u"),
    ("MaxVolumeExtent", "u"), ("MaxTextureRepeat", "u"), ("MaxTextureAspectRatio", "u"),
    ("MaxAnisotropy", "u"), ("MaxVertexW", "f"), ("GuardBandLeft", "f"), ("GuardBandTop", "f"),
    ("GuardBandRight", "f"), ("GuardBandBottom", "f"), ("ExtentsAdjust", "f"), ("StencilCaps", "x"),
    ("FVFCaps", "x"), ("TextureOpCaps", "x"), ("MaxTextureBlendStages", "u"),
    ("MaxSimultaneousTextures", "u"), ("VertexProcessingCaps", "v"), ("MaxActiveLights", "u"),
    ("MaxUserClipPlanes", "u"), ("MaxVertexBlendMatrices", "u"), ("MaxVertexBlendMatrixIndex", "u"),
    ("MaxPointSize", "f"), ("MaxPrimitiveCount", "u"), ("MaxVertexIndex", "u"), ("MaxStreams", "u"),
    ("MaxStreamStride", "u"), ("VertexShaderVersion", "x"), ("MaxVertexShaderConst", "u"),
    ("PixelShaderVersion", "x"), ("MaxPixelShaderValue", "f"),
]
SIZE = 4 * len(FIELDS)

VTXPCAPS = [
    (0x0001, "D3DVTXPCAPS_TEXGEN"), (0x0002, "D3DVTXPCAPS_MATERIALSOURCE7"),
    (0x0008, "D3DVTXPCAPS_DIRECTIONALLIGHTS"), (0x0010, "D3DVTXPCAPS_POSITIONALLIGHTS"),
    (0x0020, "D3DVTXPCAPS_LOCALVIEWER"), (0x0040, "D3DVTXPCAPS_TWEENING"),
    (0x0100, "D3DVTXPCAPS_TEXGEN_SPHEREMAP"), (0x0200, "D3DVTXPCAPS_NO_TEXGEN_NONLOCALVIEWER"),
]


def read_log(path, base=None):
    """Last dumped window in a bp-args log (optionally the one printed at `base`)."""
    best = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = DUMP.search(line)
            if not m:
                continue
            addr = int(m.group(1), 16)
            try:
                words = [int(w, 16) for w in m.group(2).split()]
            except ValueError:
                continue
            if base is not None and addr != base:
                continue
            best = (addr, words)
    if best is None:
        sys.exit("no dump window in %s%s" % (path, "" if base is None else " at 0x%08x" % base))
    return best


def decode(words, caps_off=0):
    """Field list from the 53 dwords of a D3DCAPS8 starting at `caps_off` in `words`."""
    if caps_off % 4 or caps_off + SIZE > 4 * len(words):
        sys.exit("caps window of %d bytes does not fit at +0x%x in a %d-byte dump"
                 % (SIZE, caps_off, 4 * len(words)))
    start = caps_off // 4
    out = []
    for i, (name, kind) in enumerate(FIELDS):
        raw = words[start + i]
        val = struct.unpack("<f", struct.pack("<I", raw))[0] if kind == "f" else raw
        out.append({"name": name, "kind": kind, "raw": raw, "value": val, "offset": 4 * i})
    return out


def plausible(fields):
    """A D3DCAPS8, or something that merely has 53 dwords in it."""
    by = {f["name"]: f["raw"] for f in fields}
    if by["DeviceType"] not in (1, 2, 3):          # HAL, REF, SW
        return "DeviceType 0x%x is not a D3DDEVTYPE" % by["DeviceType"]
    for name in ("MaxTextureWidth", "MaxTextureHeight"):
        v = by[name]
        if v == 0 or v & (v - 1):
            return "%s 0x%x is not a power of two" % (name, v)
    if by["MaxPointSize"] and not (0 < struct.unpack("<f", struct.pack("<I", by["MaxPointSize"]))[0] <= 8192):
        return "MaxPointSize is not a plausible float"
    return None


def describe_bits(value):
    names = [name for bit, name in VTXPCAPS if value & bit]
    known = 0
    for bit, _ in VTXPCAPS:
        known |= bit
    rest = value & ~known
    if rest:
        names.append("0x%x unnamed" % rest)
    return ", ".join(names) if names else "none"


def render(fields, caps_addr, words, caps_off):
    lines = ["caps at 0x%08x (window 0x%08x + 0x%x, %d dwords)"
             % (caps_addr, caps_addr - caps_off, caps_off, len(FIELDS)), ""]
    for f in fields:
        if f["kind"] == "f":
            shown = "%10d  %g" % (f["raw"], f["value"])
        elif f["kind"] == "v":
            shown = "%10d  %s" % (f["raw"], describe_bits(f["raw"]))
        else:
            shown = "%10d  0x%08x" % (f["raw"], f["raw"])
        lines.append("+0x%03x  %-26s %s" % (f["offset"], f["name"], shown))
    tail = caps_addr + SIZE
    after = (tail - (caps_addr - caps_off)) // 4
    text = b"".join(struct.pack("<I", w) for w in words[after:after + 4]) if after < len(words) else b""
    text = text.split(b"\0")[0]
    if text and all(0x20 <= c < 0x7f for c in text):
        lines.append("+0x%03x  %-26s %r  (the driver name the runtime writes after the caps)"
                     % (SIZE, "(string)", text.decode("ascii")))
    return "\n".join(lines)


def markdown(fields, caps_addr):
    out = ["| offset | field | value |", "|---|---|---|"]
    for f in fields:
        if f["kind"] == "f":
            val = "`%g`" % f["value"] if f["value"] == int(f["value"]) or abs(f["value"]) >= 1 else "`%g`" % f["value"]
            val = "`0x%08x` = %g" % (f["raw"], f["value"])
        elif f["kind"] == "v":
            val = "`0x%08x` = %s" % (f["raw"], describe_bits(f["raw"]).replace("D3DVTXPCAPS_", ""))
        else:
            val = "`0x%08x` (%d)" % (f["raw"], f["raw"])
        out.append("| `+0x%03x` | `%s` | %s |" % (f["offset"], f["name"], val))
    return "\n".join(out)


def selfchk():
    """A synthetic window that is a D3DCAPS8 by construction: no dump, no log, writes nothing."""
    words = [0] * (0x120 // 4)
    start = 0x20 // 4
    for i, (name, kind) in enumerate(FIELDS):
        words[start + i] = 1 if name == "DeviceType" else 0
    raw = {"MaxTextureWidth": 4096, "MaxTextureHeight": 4096, "MaxPointSize": 0x42800000,
           "VertexProcessingCaps": 0xbb, "MaxTextureBlendStages": 4, "MaxSimultaneousTextures": 4,
           "VertexShaderVersion": 0xffff0101, "PixelShaderVersion": 0xffff0101,
           "MaxVertexIndex": 0xffff, "MaxVertexShaderConst": 96, "MaxPixelShaderValue": 0x3f800000}
    for i, (name, _) in enumerate(FIELDS):
        if name in raw:
            words[start + i] = raw[name]
    fields = decode(words, 0x20)
    bad = plausible(fields)
    assert bad is None, bad
    by = {f["name"]: f for f in fields}
    assert by["MaxTextureWidth"]["raw"] == 4096
    assert by["VertexProcessingCaps"]["offset"] == 0x9c
    assert "D3DVTXPCAPS_MATERIALSOURCE7" in describe_bits(by["VertexProcessingCaps"]["raw"])
    assert by["MaxPointSize"]["value"] == 64.0
    assert by["PixelShaderVersion"]["offset"] == 0xcc
    # a window that is not caps must be refused, not decoded
    words[start] = 0
    assert plausible(decode(words, 0x20)) is not None
    print("ok d3d-caps: 53 fields, VertexProcessingCaps at +0x9c, bytes/fields disagree detected")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", help="a tools/xemu/bp-args.py log")
    ap.add_argument("--base", type=lambda v: int(v, 0), help="the address the dump window was read at")
    ap.add_argument("--caps", type=lambda v: int(v, 0), help="offset of the D3DCAPS8 inside that window")
    ap.add_argument("--words", help="hex words (comma or space separated) instead of a log")
    ap.add_argument("--markdown", action="store_true", help="emit a markdown table instead of the listing")
    ap.add_argument("--json", metavar="PATH", help="also write the decoded fields as JSON")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump and writes nothing")
    args = ap.parse_args()
    if args.check:
        selfchk()
        return
    if args.words:
        words = [int(w, 16) for w in re.split(r"[,\s]+", args.words.strip()) if w]
        base = args.base or 0
    elif args.log:
        if args.caps is None:
            sys.exit("--log needs --caps (the struct's offset inside the dumped window)")
        base, words = read_log(args.log, args.base)
    else:
        sys.exit("give --log with --caps, or --words")
    caps_off = args.caps or 0
    fields = decode(words, caps_off)
    bad = plausible(fields)
    if bad:
        sys.exit("not a D3DCAPS8 at 0x%08x + 0x%x: %s" % (base, caps_off, bad))
    caps_addr = base + caps_off
    print(markdown(fields, caps_addr) if args.markdown else render(fields, caps_addr, words, caps_off))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"caps_address": caps_addr, "fields": fields}, fh, indent=1)
            fh.write("\n")


if __name__ == "__main__":
    main()
