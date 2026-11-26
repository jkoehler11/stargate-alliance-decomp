#!/usr/bin/env python3
"""Export the BGRA textures that `umodel` refuses, as PNG.

Four `Textures/*.utx` packages fail in `umodel` with `assertion failed: Colors.Num() == 256` and
`Palette: Property read error: 3`. The payload is not malformed: it is a tagged property block, then a 16-byte
header, then BGRA pixels whose size the properties themselves state.

    python3 tools/texture-export.py --dump <dump/Textures> --out <dir> [--inventory out.csv]
    python3 tools/texture-export.py --check

Measured on `Tutorial_Tex` and `M07_fresco_tex` (this session):

- `tools/prop-stream.py` reads the block; its end moves per object (7 / 19 / 57 bytes).
- The properties name `USize`, `VSize` and `Format`. For `Format == 5`, the payload is `USize * VSize * 4` bytes.
- At **16 bytes** past the block, byte 3 is 255 in 20,000 of 20,000 sampled pixels — BGRA. At 17, 18 or 19 the alpha
  channel lands in the wrong byte, so 16 is measured, not assumed.

Objects without `USize`/`VSize` in their properties are skipped and reported: their size is inherited from somewhere this
tool does not model, and guessing it would produce noise.
"""

import argparse
import csv
import glob
import importlib.util
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

HEADER = 16          # bytes between the property block and the first pixel, measured
FORMAT_BGRA = 5      # the format whose payload is USize * VSize * 4


def load_prop_stream():
    spec = importlib.util.spec_from_file_location("ps", os.path.join(HERE, "prop-stream.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ps = load_prop_stream()


def png(width, height, bgra):
    """A minimal 8-bit RGBA PNG from BGRA bytes."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)                                     # filter: none
        row = bgra[y * width * 4:(y + 1) * width * 4]
        for x in range(width):
            b, g, r, a = row[x * 4:x * 4 + 4]
            rows += bytes((r, g, b, a))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


def props_of(pkg, export):
    blob = pkg.data[export["offset"]:export["offset"] + export["size"]]
    parsed = ps.read_block(blob, pkg, want_values=True)
    block, end = parsed[0], parsed[1]
    values = {}
    for tag in block:
        if isinstance(tag, dict) and tag.get("name"):
            values[tag["name"]] = tag.get("value")
    return blob, end, values


def run(dump_dir, out_dir, inventory):
    files = sorted(glob.glob(os.path.join(dump_dir, "*.utx")))
    if not files:
        print("FAIL texture-export: no .utx under %s" % dump_dir)
        return 1
    rows, written, skipped = [], 0, 0
    for path in files:
        pkg_name = os.path.splitext(os.path.basename(path))[0]
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_dir = os.path.join(out_dir, pkg_name)
        for export in pkg.exports:
            if pkg.class_name_of(export) != "Texture":
                continue
            blob, end, values = props_of(pkg, export)
            usize, vsize, fmt = values.get("USize"), values.get("VSize"), values.get("Format")
            name = pkg.name(export["name"])
            if not (isinstance(usize, int) and isinstance(vsize, int)):
                skipped += 1
                rows.append([pkg_name, name, export["index"], export["size"], usize, vsize, fmt, "", "",
                             "size not stated"])
                continue
            need = usize * vsize * 4
            body = blob[end + HEADER:end + HEADER + need]
            if len(body) != need:
                skipped += 1
                rows.append([pkg_name, name, export["index"], export["size"], usize, vsize, fmt, "", "",
                             "truncated: %d of %d" % (len(body), need)])
                continue
            # `Format` is absent on a minority of textures. Rather than skip them or trust them, validate the offset:
            # if the pixels really are BGRA at +16, byte 3 is the alpha channel and is 255 almost everywhere. A texture
            # with genuine transparency can fail this and is then skipped -- a false negative, reported, not a wrong image.
            if fmt != FORMAT_BGRA:
                sample = [body[i * 4 + 3] for i in range(0, min(usize * vsize, 20000))]
                opaque = sum(1 for a in sample if a == 255)
                if not sample or opaque < 0.90 * len(sample):
                    skipped += 1
                    rows.append([pkg_name, name, export["index"], export["size"], usize, vsize, fmt, "", "",
                                 "alpha validates %d of %d at +16" % (opaque, len(sample))])
                    continue
            os.makedirs(pkg_dir, exist_ok=True)
            with open(os.path.join(pkg_dir, name + ".png"), "wb") as fh:
                fh.write(png(usize, vsize, body))
            written += 1
            rows.append([pkg_name, name, export["index"], export["size"], usize, vsize, fmt, usize, vsize, ""])
    if inventory:
        os.makedirs(os.path.dirname(os.path.abspath(inventory)), exist_ok=True)
        with open(inventory, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["package", "texture", "export", "payload", "USize", "VSize", "format", "width", "height",
                        "note"])
            w.writerows(rows)
    print("texture-export: %d package(s), %d png(s) written, %d skipped, %d row(s)" % (
        len(files), written, skipped, len(rows)))
    return 0


def check():
    """A synthetic payload: 2x1 BGRA after a 16-byte header must produce a PNG that decodes back to those pixels."""
    width, height = 2, 1
    pixels = bytes((0, 0, 255, 255, 0, 255, 0, 255))     # BGRA: red, green
    blob = b"\x00" * HEADER + pixels
    body = blob[HEADER:HEADER + width * height * 4]
    if body != pixels:
        print("FAIL texture-export: the header offset does not land on the pixels")
        return 1
    data = png(width, height, body)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        print("FAIL texture-export: not a PNG")
        return 1
    # the IDAT must decode back to filter byte + RGBA rows
    import io
    idat = data[data.find(b"IDAT") + 4:data.find(b"IDAT") + 4 + struct.unpack(">I", data[data.find(b"IDAT") - 4:data.find(b"IDAT")])[0]]
    raw = zlib.decompress(idat)
    if raw != bytes((0, 255, 0, 0, 255, 0, 255, 0, 255)):
        print("FAIL texture-export: decoded rows %r" % (raw,))
        return 1
    print("ok texture-export: 16-byte header, BGRA in, RGBA PNG out")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dump", help="a directory of .utx packages")
    ap.add_argument("--out", help="where to write the .png files")
    ap.add_argument("--inventory", help="write a CSV of what was extracted")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.dump or not args.out:
        print("FAIL texture-export: --dump and --out are required")
        return 1
    return run(args.dump, args.out, args.inventory)


if __name__ == "__main__":
    sys.exit(main())
