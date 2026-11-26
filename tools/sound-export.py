#!/usr/bin/env python3
"""Export the WAV files that are embedded in the game's Sound exports.

`umodel` reads UE2 `Sound` payloads as a Format struct followed by a data array. These packages do not do that: every one
of the 3,047 `Sound` exports in the 55 `Sounds/*.uax` packages carries a **complete RIFF WAVE file** inside its payload,
after a short fixed prefix. umodel therefore refuses every one of those packages, and 53 of them sat unexported.

This extracts the RIFF chunk — from `RIFF` to the length its own header declares — and writes one `.wav` per export, plus
an inventory row with the facts a consumer needs (sample rate, channels, bits, byte rate).

    python3 tools/sound-export.py --dump <dump/Sounds> --out <dir> [--inventory out.csv]
    python3 tools/sound-export.py --check

Nothing derived from the game is written into the repo: `--out` is caller-chosen, and the inventory holds names, offsets
and header fields only.
"""

import argparse
import csv
import glob
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402


def riff_of(blob):
    """The embedded RIFF chunk, or None.

    The chunk is the file from `RIFF` for `8 + size` bytes; the size the header declares is authoritative, because one
    payload (`GeneralSounds.uax:Ughh`) carries 16 bytes after the chunk that are not part of it.
    """
    at = blob.find(b"RIFF")
    if at < 0 or at + 12 > len(blob) or blob[at + 8:at + 12] != b"WAVE":
        return None
    size = struct.unpack("<I", blob[at + 4:at + 8])[0]
    end = at + 8 + size
    if end > len(blob):
        return None
    return at, blob[at:end]


def wav_facts(chunk):
    """(format tag, channels, rate, byte rate, block align, bits) from the `fmt ` chunk, or None."""
    at = chunk.find(b"fmt ")
    if at < 0 or at + 24 > len(chunk):
        return None
    # `fmt ` is id(4) + size(4) + WAVEFORMATEX's first 16 bytes
    tag, channels, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", chunk[at + 8:at + 24])
    return tag, channels, rate, byte_rate, align, bits


def safe(name):
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in name)


def run(dump_dir, out_dir, inventory, exts=(".uax",)):
    # walk: `.uax` packages do not all sit in `Sounds/` -- `eSFX`, `weaponsounds`, `jaffasounds` and others live elsewhere,
    # and a single-directory glob left every one of them unexported while their neighbours were being written.
    files = []
    for root, _dirs, names in os.walk(dump_dir):
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() in exts:
                files.append(os.path.join(root, name))
    files.sort()
    if not files:
        print("FAIL sound-export: no %s under %s" % (exts, dump_dir))
        return 1
    rows, written, skipped, seen = [], 0, 0, {}
    for path in files:
        # the directory is part of the identity: `weaponsounds` in one folder is not the same package as another of the
        # same name elsewhere, and a bare basename would merge their sounds silently
        rel = os.path.relpath(path, dump_dir)
        pkg_name = os.path.splitext(rel)[0].replace(os.sep, "_")
        p = Package(path)
        p.read_names()
        p.read_imports()
        p.read_exports()
        pkg_dir = os.path.join(out_dir, safe(pkg_name))
        for e in p.exports:
            if p.class_name_of(e) != "Sound":
                continue
            blob = p.data[e["offset"]:e["offset"] + e["size"]]
            found = riff_of(blob)
            if found is None:
                skipped += 1
                rows.append({"package": pkg_name, "sound": p.name(e["name"]), "export": e["index"],
                             "payload": e["size"], "riff_at": "", "riff_bytes": "", "format_tag": "",
                             "channels": "", "sample_rate": "", "byte_rate": "", "block_align": "", "bits": "",
                             "note": "no RIFF chunk"})
                continue
            at, chunk = found
            facts = wav_facts(chunk)
            name = safe(p.name(e["name"]))
            # a name can repeat inside one package; keep every export rather than overwrite one with another
            seen[(pkg_name, name)] = seen.get((pkg_name, name), 0) + 1
            if seen[(pkg_name, name)] > 1:
                name = "%s_%d" % (name, seen[(pkg_name, name)])
            os.makedirs(pkg_dir, exist_ok=True)
            with open(os.path.join(pkg_dir, name + ".wav"), "wb") as fh:
                fh.write(chunk)
            written += 1
            tag, channels, rate, byte_rate, align, bits = facts if facts else ("",) * 6
            rows.append({"package": pkg_name, "sound": p.name(e["name"]), "export": e["index"],
                         "payload": e["size"], "riff_at": at, "riff_bytes": len(chunk), "format_tag": tag,
                         "channels": channels, "sample_rate": rate, "byte_rate": byte_rate, "block_align": align,
                         "bits": bits, "note": "" if facts else "no fmt chunk"})
    if inventory:
        os.makedirs(os.path.dirname(os.path.abspath(inventory)), exist_ok=True)
        cols = ["package", "sound", "export", "payload", "riff_at", "riff_bytes", "format_tag", "channels",
                "sample_rate", "byte_rate", "block_align", "bits", "note"]
        with open(inventory, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow(r)
    print("sound-export: %d package(s), %d wav(s) written, %d without a chunk, %d row(s)" % (
        len(files), written, skipped, len(rows)))
    return 0


def check():
    """A synthetic payload: the extractor must find the chunk, ignore a prefix and a trailing pad, and read the header."""
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 22050, 44100, 2, 16)
    data = struct.pack("<4sI", b"data", 8) + b"\x01\x02\x03\x04\x05\x06\x07\x08"
    body = fmt + data
    chunk = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body
    blob = b"\x00\x01\x00\x00\x80\x3f\x80\x4f\x00\x00" + chunk + b"\x00" * 16   # prefix + chunk + trailing pad
    found = riff_of(blob)
    if found is None:
        print("FAIL sound-export: the synthetic chunk was not found")
        return 1
    at, got = found
    if got != chunk:
        print("FAIL sound-export: extracted %d bytes, expected %d (at %d)" % (len(got), len(chunk), at))
        return 1
    facts = wav_facts(got)
    if facts != (1, 1, 22050, 44100, 2, 16):
        print("FAIL sound-export: header facts %r" % (facts,))
        return 1
    # a payload with no RIFF must be reported, not guessed at
    if riff_of(b"\x00" * 64) is not None:
        print("FAIL sound-export: a payload without a chunk produced one")
        return 1
    print("ok sound-export: chunk located after a prefix, trailing pad ignored, header read")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dump", help="a directory of .uax packages")
    ap.add_argument("--out", help="where to write the .wav files")
    ap.add_argument("--inventory", help="write a CSV of what was extracted")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.dump or not args.out:
        print("FAIL sound-export: --dump and --out are required")
        return 1
    return run(args.dump, args.out, args.inventory)


if __name__ == "__main__":
    sys.exit(main())
