#!/usr/bin/env python3
"""Index the LIPSinc lip-sync directory: the phoneme maps decode, and the animation container is measured.

`System/LIPSincData/` holds three kinds of file and the project had decoded none of them
(`docs/systems/AUDIO.md`, `docs/UNKNOWNS.md` U4):

* `.map` — **plain text**, an INI whose `[PhonemeToSpeechTargetMapping]` section maps each phoneme to a
  `NumSpeechTargets`-long vector of weights (`Iy=0.85, 0.00, …`). That is the character's viseme set;
* `.lad` — binary: header, bone-name table, a ten-byte prelude and then a **32-byte-strided record stream** that
  ends in a block of three names. The stride is measured, not assumed (the `-1.0` marker that opens most records'
  last eight bytes recurs at exactly 32-byte steps), and the bytes of every file that do not fall on the stride are
  reported as residue instead of being rounded away;
* `.dat` — the animation's audio, left to the audio index (`inventory/sfx_index.csv`).

    python3 tools/lipsinc-index.py --dir "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
    python3 tools/lipsinc-index.py --check        # dump-free, validates the committed CSVs

Artifacts: `inventory/lipsinc_files.csv` (one row per file), `inventory/lipsinc_phonemes.csv` (one row per
phoneme × speech target) and `inventory/lipsinc_lad.csv` (one row per `.lad`, with what the container claims).
"""
import argparse
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE_COLUMNS = ["path", "kind", "bytes", "entries", "note"]
PHONEME_COLUMNS = ["map", "character", "phoneme", "target", "weight"]
LAD_COLUMNS = ["path", "bytes", "header_u32", "bones", "data_offset", "record_bytes", "records", "residue_bytes",
               "trailer_names"]
RECORD = 32


def read_name(d, off):
    """A length byte (counting the NUL) then the string."""
    n = d[off]
    if n == 0 or n > 0x40:
        raise ValueError("length %d at %#x is not a name length" % (n, off))
    raw = d[off + 1:off + 1 + n]
    if not raw.endswith(b"\0"):
        raise ValueError("name at %#x is not NUL-terminated" % off)
    text = raw[:-1].decode("latin1")
    if not all(32 <= ord(c) < 127 for c in text):
        raise ValueError("non-printable name at %#x" % off)
    return text, off + 1 + n


def parse_lad(path):
    d = open(path, "rb").read()
    a, b = struct.unpack_from("<II", d, 0)
    off = 8
    n, off = d[off], off + 1
    bones = []
    for _ in range(n):
        name, off = read_name(d, off)
        bones.append(name)
    body = len(d) - off
    # The trailer is three length-prefixed names ("BN-Head", "undefined", "undefined"); find it from the end and
    # check it round-trips, then take whatever is left as the record stream.
    tail_off, names = off, []
    probe = len(d)
    while len(names) < 3:
        # walk backwards over a name: find its byte length by trying each candidate start
        for start in range(max(off, probe - 0x40), probe):
            try:
                text, end = read_name(d, start)
            except (ValueError, IndexError):
                continue
            if end == probe:
                names.insert(0, text)
                probe = start
                break
        else:
            break
    if len(names) != 3:
        raise ValueError("%s: no three-name trailer" % path)
    stream = probe - off
    residue = stream % RECORD
    records = stream // RECORD
    return {"header_u32": "%d,%d" % (a, b), "bones": ";".join(bones), "data_offset": off,
            "record_bytes": RECORD, "records": records, "residue_bytes": residue,
            "trailer_names": ";".join(names)}, len(d)


def parse_map(path):
    character = os.path.basename(path)[:-4]
    targets = None
    rows = []
    section = None
    for line in open(path, "r", encoding="latin1"):
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "PhonemeToSpeechTargetMapping":
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "NumSpeechTargets":
            targets = int(value)
            continue
        weights = [w.strip() for w in value.split(",")]
        if targets is not None and len(weights) != targets:
            raise ValueError("%s: %s has %d weights, NumSpeechTargets=%d"
                             % (path, key, len(weights), targets))
        for i, w in enumerate(weights):
            rows.append({"map": os.path.basename(path), "character": character, "phoneme": key.strip(),
                         "target": i, "weight": w})
    return rows, targets


def build(root):
    files, phonemes, lads = [], [], []
    for dirpath, _dirs, names in os.walk(root):
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            kind = name.rsplit(".", 1)[-1].lower()
            size = os.path.getsize(path)
            if kind == "lad":
                info, size2 = parse_lad(path)
                info["path"] = rel
                info["bytes"] = size2
                lads.append(info)
                files.append({"path": rel, "kind": "lad", "bytes": size,
                              "entries": "%d bones, %d records" % (len(info["bones"].split(";")), info["records"]),
                              "note": "record %d bytes, %d residue bytes, trailer %s"
                                      % (RECORD, info["residue_bytes"], info["trailer_names"])})
            elif kind == "map":
                rows, targets = parse_map(path)
                phonemes += rows
                files.append({"path": rel, "kind": "map", "bytes": size,
                              "entries": "%d phonemes x %s targets" % (len(rows) // targets if targets else 0,
                                                                       targets or "?"),
                              "note": "text INI, [PhonemeToSpeechTargetMapping]"})
            else:
                files.append({"path": rel, "kind": kind, "bytes": size, "entries": "",
                              "note": "audio for the .lad of the same stem" if kind == "dat" else ""})
    files.sort(key=lambda r: r["path"])
    phonemes.sort(key=lambda r: (r["map"], r["phoneme"], int(r["target"])))
    lads.sort(key=lambda r: r["path"])
    return files, phonemes, lads


def write(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in columns})


def check(out):
    problems = []
    try:
        files = list(csv.DictReader(open(os.path.join(out, "lipsinc_files.csv"))))
        phonemes = list(csv.DictReader(open(os.path.join(out, "lipsinc_phonemes.csv"))))
        lads = list(csv.DictReader(open(os.path.join(out, "lipsinc_lad.csv"))))
    except FileNotFoundError as exc:
        print("FAIL lipsinc-index: %s" % exc)
        return 1
    if not files or not phonemes or not lads:
        problems.append("an artifact is empty")
    for r in files:
        if sorted(r) != sorted(FILE_COLUMNS):
            problems.append("files columns")
            break
    per_map = {}
    for r in phonemes:
        per_map.setdefault(r["map"], set()).add(r["target"])
        try:
            float(r["weight"])
        except ValueError:
            problems.append("%s/%s: weight %r" % (r["map"], r["phoneme"], r["weight"]))
    for m, targets in per_map.items():
        if targets != {str(i) for i in range(len(targets))}:
            problems.append("%s: target indices are not 0..%d" % (m, len(targets) - 1))
    for r in lads:
        if int(r["record_bytes"]) != RECORD:
            problems.append("%s: record size" % r["path"])
        if int(r["residue_bytes"]) >= RECORD:
            problems.append("%s: residue %s" % (r["path"], r["residue_bytes"]))
        if len(r["trailer_names"].split(";")) != 3:
            problems.append("%s: trailer" % r["path"])
    if problems:
        print("FAIL lipsinc-index: " + "; ".join(problems[:6]))
        return 1
    print("ok   lipsinc-index: %d files, %d phoneme weights over %d maps, %d .lad containers"
          % (len(files), len(phonemes), len(per_map), len(lads)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", help="the dump's System/LIPSincData directory")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true", help="validate the committed CSVs (no dump needed)")
    args = ap.parse_args()
    if args.check:
        return check(args.out)
    if not args.dir:
        ap.error("--dir is required unless --check")
    files, phonemes, lads = build(args.dir)
    write(os.path.join(args.out, "lipsinc_files.csv"), FILE_COLUMNS, files)
    write(os.path.join(args.out, "lipsinc_phonemes.csv"), PHONEME_COLUMNS, phonemes)
    write(os.path.join(args.out, "lipsinc_lad.csv"), LAD_COLUMNS, lads)
    for r in lads:
        print("%-34s bones=%2d data=%4d records=%5d residue=%2d trailer=%s"
              % (r["path"], len(r["bones"].split(";")), r["data_offset"], r["records"], r["residue_bytes"],
                 r["trailer_names"]))
    print("wrote %d files, %d phoneme weights, %d .lad rows" % (len(files), len(phonemes), len(lads)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
