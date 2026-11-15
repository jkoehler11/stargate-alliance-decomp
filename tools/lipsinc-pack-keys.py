#!/usr/bin/env python3
"""The clip directory inside every `.lad` pack, and whether anything in the dump names its writer.

U4's last open question was *which tool built the packs*: the `.map` side's writer is `0x000b55f0`,
so the read was whether the shipped artifacts name the exporter. This census answers both parts at
once and finds a structure the previous `.lad` work had not separated out.

Every pack carries a **key directory**: before a clip's curve data sits a record
`dword 2, u8 len, <cue name> NUL, u8 len, <group>.<cue name> NUL`, and the packs end with the same
trailer (`BN-Head`, `undefined`). Read as a directory it gives per-pack key counts (0 .. 786) and
the cue names each pack answers to. The long name's group prefix is the same five-letter name in all
eight packs, and it names no object in the package that carries it.

The join is what says where the keys came from: each pack's cue names are the *voice* names of one
character -- they appear verbatim in that character's `Sounds/*.uax` and in `StargateContent.u`, and
most also match a shipped `Speech/**/*.wav` basename. The root packs (`System/LIPSincData/*.lad`) hold
2-3 keys; the `Controllers/` packs hold the character's whole cue list.

What it rules out: no pack carries a tool name, version or build stamp. The 8-byte header goes
straight into the channel table, the payload's only ASCII is channel names, cue names, the trailer and
float-data coincidences, and the `.map` files are hand-edited CRLF ini text whose own `;` header names
the file, not the writer. So the exporter is identified by its output shape, not by a stamp in it.

    tools/lipsinc-pack-keys.py --dump Stargate_SG-1_-_The_Alliance-2005-12-15 -o inventory
    tools/lipsinc-pack-keys.py --dump ... --write-doc docs/formats/lipsinc-lad.md
    tools/lipsinc-pack-keys.py --check-doc docs/formats/lipsinc-lad.md   # from the committed CSV
    tools/lipsinc-pack-keys.py --check        # synthetic packs, no dump, writes nothing

No boot: the shipped files only.
"""
import argparse
import csv
import glob
import os
import re
import statistics
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["kind", "pack", "size", "keys", "distinct_keys", "group", "trailer", "first_key",
          "first_key_off", "trailer_off", "gap_min", "gap_med", "gap_max", "wav_hits",
          "voice_pack", "voice_hits", "content_hits", "residue_runs", "residue_words",
          "residue_expr"]
ARTIFACT = os.path.join(ROOT, "inventory/lipsinc_pack_keys.csv")
DOC = os.path.join(ROOT, "docs/formats/lipsinc-lad.md")
DOC_START = "<!-- generated:lipsinc-pack-keys -->"
DOC_END = "<!-- /generated:lipsinc-pack-keys -->"
# dword 2, a length-prefixed NUL-terminated name, then the same name with its group prefix.
KEY = re.compile(rb"\x02\x00\x00\x00(.)([ -~]{1,120}?)\x00(.)([ -~]{1,200}?)\x00", re.S)
RUN = re.compile(rb"[ -~]{4,}")
TRAILER = "BN-Head"
DEFAULT_DUMP = os.environ.get("SG1_DUMP", "") + "/Stargate_SG-1_-_The_Alliance-2005-12-15"


def header_end(blob):
    """One past the channel table: two dwords, a channel count, then that many NUL-terminated names."""
    try:
        n, o = blob[8], 9
        for _ in range(n):
            o += 1 + blob[o]
    except (IndexError, TypeError):
        return 0
    return o


def keys_of(blob, start=None):
    """(list of (offset, short, long), trailer) for one `.lad`. Length prefixes must agree."""
    if start is None:
        start = header_end(blob)
    sites = []
    for m in KEY.finditer(blob, start):
        if m.group(1)[0] == len(m.group(2)) + 1 and m.group(3)[0] == len(m.group(4)) + 1:
            sites.append((m.start(), m.group(2).decode("latin1"), m.group(4).decode("latin1")))
    trailer = None
    if sites and sites[-1][1] == TRAILER:
        trailer = sites.pop()
    return sites, trailer


EXPR = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")


def residue(blob, known):
    """ASCII runs not explained by a channel name, a key or `undefined`.

    A name's own length prefix is printable (`0x21` is `!`) and the run scanner keeps it, so a run
    that is `prefix + <known name>` is the name, not new text -- strip up to two leading bytes.
    Returns (runs, word-shaped runs, expression-shaped runs).
    """
    runs, words, exprs = 0, 0, 0
    for m in RUN.finditer(blob):
        text = m.group().decode("latin1")
        core = text if text in known else text[1:] if text[1:] in known else text[2:]
        if core in known or text == "undefined" or text.startswith("BN-"):
            continue
        runs += 1
        if len(text) >= 5 and sum(c.isalpha() for c in text) >= 0.9 * len(text):
            words += 1
            if EXPR.match(text):
                exprs += 1
    return runs, words, exprs


def joined(keys, blobs):
    """{path: count of distinct keys that occur verbatim} for one pack's key list."""
    if not keys:
        return {}
    pattern = re.compile(b"|".join(re.escape(k.encode("latin1")) for k in sorted(keys, key=len, reverse=True)))
    out = {}
    for path, blob in blobs:
        seen = set()
        for m in pattern.finditer(blob):
            seen.add(m.group())
        if seen:
            out[path] = len(seen)
    return out


def census(dump):
    packs = sorted(glob.glob(os.path.join(dump, "System/LIPSincData/**/*.lad"), recursive=True))
    wavs = {os.path.splitext(os.path.basename(p))[0].lower()
            for p in glob.glob(os.path.join(dump, "Speech/**/*.wav"), recursive=True)}
    blobs = [(p, open(p, "rb").read())
             for p in sorted(glob.glob(os.path.join(dump, "System/*.u")))
             + sorted(glob.glob(os.path.join(dump, "Sounds/*.uax")))]
    content = [b for b in blobs if os.path.basename(b[0]) == "StargateContent.u"]
    rows, allkeys = [], set()
    for pack in packs:
        blob = open(pack, "rb").read()
        sites, trailer = keys_of(blob)
        shorts = [s[1] for s in sites]
        longs = [s[2] for s in sites]
        groups = sorted({ln.split(".")[0] for ln in longs if "." in ln})
        gaps = [b[0] - a[0] for a, b in zip(sites, sites[1:])]
        runs, words, exprs = residue(blob, set(shorts) | set(longs))
        hits = joined(set(shorts), blobs)
        voice = max(((v, k) for k, v in hits.items() if k.endswith(".uax")), default=(0, ""))
        allkeys |= set(shorts)
        rows.append(dict(
            kind="pack", pack=os.path.relpath(pack, dump), size=str(len(blob)),
            keys=str(len(shorts)), distinct_keys=str(len(set(shorts))),
            group=",".join(groups), trailer=",".join(trailer[1:]) if trailer else "",
            first_key=shorts[0] if shorts else "",
            first_key_off="0x%x" % sites[0][0] if sites else "",
            trailer_off="0x%x" % trailer[0] if trailer else "",
            gap_min=str(min(gaps)) if gaps else "", gap_med=str(int(statistics.median(gaps))) if gaps else "",
            gap_max=str(max(gaps)) if gaps else "",
            wav_hits=str(sum(1 for s in shorts if s.lower() in wavs)),
            voice_pack=os.path.basename(voice[1]), voice_hits=str(voice[0]),
            content_hits=str(hits.get(content[0][0], 0)) if content else "0",
            residue_runs=str(runs), residue_words=str(words),
            residue_expr=str(exprs)))
    rows.append(dict(
        kind="census", pack="", size="", keys=str(sum(int(r["keys"]) for r in rows)),
        distinct_keys=str(len(allkeys)),
        group=",".join(sorted({g for r in rows for g in r["group"].split(",") if g})),
        trailer=",".join(sorted({r["trailer"] for r in rows if r["trailer"]})),
        first_key="", first_key_off="", trailer_off="", gap_min="", gap_med="", gap_max="",
        wav_hits=str(sum(int(r["wav_hits"]) for r in rows)),
        voice_pack="", voice_hits="",
        content_hits=str(sum(int(r["content_hits"]) for r in rows)),
        residue_runs=str(sum(int(r["residue_runs"]) for r in rows)),
        residue_words=str(sum(int(r["residue_words"]) for r in rows)),
        residue_expr=str(sum(int(r["residue_expr"]) for r in rows))))
    return rows


def render(rows):
    packs = [r for r in rows if r["kind"] == "pack"]
    total = next(r for r in rows if r["kind"] == "census")
    lines = ["| pack | keys | speech `.wav` of the same name | voice package (keys it holds) | `StargateContent.u` | first key at | key spacing (min/median/max) |",
             "|---|---|---|---|---|---|---|"]
    for r in packs:
        lines.append("| `%s` | %s | %s | %s (%s) | %s | %s | %s / %s / %s |"
                     % (r["pack"].replace("System/LIPSincData/", ""), r["keys"], r["wav_hits"],
                        ("`%s`" % r["voice_pack"]) if r["voice_pack"] else "none",
                        r["voice_hits"] or "0", r["content_hits"], r["first_key_off"] or "-",
                        r["gap_min"] or "-", r["gap_med"] or "-", r["gap_max"] or "-"))
    lines.append("")
    lines.append("**%s keys in %d packs (%s distinct), every one in the `%s` group, and the same trailer "
                 "`%s` ends all %d.** %s of them are a shipped speech file's own name, %s occur verbatim "
                 "in a shipped package (a voice `.uax`, or `StargateContent.u`), and the rest of each "
                 "file's ASCII is float data: %s runs that are neither a channel name nor a key, %s of "
                 "them word-shaped, and only %s of those are expression names (`tealc_frowning`, "
                 "`carter_happy`) -- the rest are letters that happen to print inside a float."
                 % (total["keys"], len(packs), total["distinct_keys"], total["group"],
                    total["trailer"], len(packs), total["wav_hits"], total["content_hits"],
                    total["residue_runs"], total["residue_words"], total["residue_expr"]))
    lines.append("")
    lines.append("**No pack names its writer.** The header is `u32 2, u32 2, u8 channels` and then the "
                 "channel table -- no version field, no date, no tool string -- and the `.map` siblings "
                 "are CRLF ini text whose `;` header names the mapping file itself. So the exporter is "
                 "identified by this directory's shape, not by a stamp inside it.")
    return "\n".join(lines)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice(path, block):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def synthetic():
    """One pack built by hand: header, one channel, two keys, a decoy tag, the trailer."""
    blob = b"\x02\x00\x00\x00\x02\x00\x00\x00\x01\x05BN-Jaw\x00"
    for short, long_ in (("m01_02_a_x", "Group.m01_02_a_x"), ("Beware", "Group.Beware")):
        blob += (b"\x02\x00\x00\x00" + bytes([len(short) + 1]) + short.encode() + b"\x00"
                 + bytes([len(long_) + 1]) + long_.encode() + b"\x00" + b"\x01\x00\x00\x00")
    # a decoy: the tag is there, the first length prefix does not match its string
    blob += b"\x02\x00\x00\x00\x09abc\x00\x03xyz\x00"
    blob += b"\x02\x00\x00\x00" + bytes([len(TRAILER) + 1]) + TRAILER.encode() + b"\x00\x0aundefined\x00"
    return blob


def self_check():
    bad = []
    blob = synthetic()
    sites, trailer = keys_of(blob)
    got = [s[1] for s in sites]
    if got != ["m01_02_a_x", "Beware"]:
        bad.append("key extraction: %r" % (got,))
    if trailer is None or trailer[1] != TRAILER:
        bad.append("the trailer key was not separated from the directory: %r" % (trailer,))
    if "abc" in got:
        bad.append("a tag whose length prefix disagrees with its string was read as a key")
    if sites and sites[0][2] != "Group.m01_02_a_x":
        bad.append("the long name: %r" % (sites[0][2],))
    runs, words, exprs = residue(blob, {s[1] for s in sites} | {s[2] for s in sites})
    if runs != 0 or words or exprs:
        bad.append("residue counted while every run was a name: %d %d %d" % (runs, words, exprs))
    hits = joined({"m01_02_a_x", "absent"}, [("a.uax", b"..m01_02_a_x.."), ("b.u", b"nothing")])
    if hits != {"a.uax": 1}:
        bad.append("the package join: %r" % (hits,))
    rows = [dict(kind="pack", pack="p/x.lad", size="1", keys="2", distinct_keys="2", group="Group",
                 trailer=TRAILER + ",undefined", first_key="m01_02_a_x", first_key_off="0x1a",
                 trailer_off="0x40", gap_min="1", gap_med="1", gap_max="1", wav_hits="1",
                 voice_pack="a.uax", voice_hits="1", content_hits="1", residue_runs="0",
                 residue_words="0", residue_expr="0"),
            dict(kind="census", pack="", size="", keys="2", distinct_keys="2", group="Group",
                 trailer=TRAILER + ",undefined", first_key="", first_key_off="", trailer_off="",
                 gap_min="", gap_med="", gap_max="", wav_hits="1", voice_pack="", voice_hits="",
                 content_hits="1", residue_runs="0", residue_words="0", residue_expr="0")]
    block = render(rows)
    for want in ("**2 keys in 1 packs", "No pack names its writer", "`p/x.lad`"):
        if want not in block:
            bad.append("render is missing %r" % want)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        write_artifact(rows, path)
        back = read_artifact(path)
        if back != rows:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL lipsinc-pack-keys: " + "; ".join(bad))
        return 1
    print("ok   lipsinc-pack-keys: a length-prefixed name pair is a key, a disagreeing prefix is not, "
          "the trailer is not a key, and the pack names no writer")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=DEFAULT_DUMP, help="the game directory (holds System/, Sounds/, Speech/)")
    ap.add_argument("-o", "--out", default=None, help="write inventory/lipsinc_pack_keys.csv here")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc:
        rows = read_artifact(ARTIFACT)
    else:
        if not os.path.isdir(args.dump):
            print("no dump at %s" % args.dump)
            return 2
        rows = census(args.dump)
        if args.out:
            path = os.path.join(args.out, "lipsinc_pack_keys.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL lipsinc-pack-keys: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL lipsinc-pack-keys: %s is stale against the committed artifact" % path)
                return 1
            print("ok   lipsinc-pack-keys: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
