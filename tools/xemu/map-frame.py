#!/usr/bin/env python3
"""What does a PASS row's post-load frame actually show? (PLAN 7.2, `docs/MAP_LOADS.md`)

`map-loads.py` records a **load**: `missions.sh` asserts a repaint, "not the BIOS screen" and "not the boot
frame", so a map that loads into a cutscene passes and so does one that comes back to the menu with different
art. The page's own "What is left" names the gap: *whether a map that loads is playable*. This reads the frame
the verdict was taken from -- `missions.tsv`'s own third column -- and says whether the HUD corner is drawn in it.

The HUD is the witness, and it is one region rather than a picture. `StargateHUD` draws a portrait and a name
in the **top-left corner** on every gameplay frame; a cutscene letterboxes it away, and the menus draw their own
art there. Measured over the 72 frames this disc's runs left on disk (1280x960):

| verdict | witness | population |
|---|---|---:|
| `hud` | the top-left corner 0-320 x 0-96 is at least **6%** bright | 65 |
| `no-hud` | that corner is dark | 7 |

The corner reads **0.140-0.986** on every gameplay frame and **0.000-0.027** on every frame that is not one, so the
two populations are separated by an empty band and the 0.06 threshold sits **2.3x under the dimmest HUD frame and
2.2x over the brightest frame that is not one**.

The **second** corner does not separate the kinds of no-HUD screen, and this is measured rather than assumed: the
bottom-right 240x100 box draws a prompt at **0.0192** on all five cutscenes (`Press A`), at **0.0005** on index
23's `MISSION FAILED` screen and at **0.0668/0.1055** on the boot's own title and its highlighted menu row, while
HUD frames run from 0.0000 to 0.9601 there (index 37's weapon fills that corner). A cutscene and a menu are both
a dark HUD corner plus a bright thing in the bottom-right, so the instrument answers "did the load reach play"
and refuses the second question rather than guessing it.

What the verdict means, and what it does not:

* `hud` is **the player was in control at the moment of the frame** -- the load reached interactive gameplay, not
  a cinematic. It is not a mission-complete verdict and not a frame-rate or an asset verdict.
* `no-hud` after a `PASS` is not a contradiction, and index 23 is the case that shows it: its frame is
  `MISSION FAILED`, i.e. a level that ran a live mission and lost it while nobody was playing -- the opposite of
  a failed load. The no-HUD rows are listed by name in the page rather than explained away.
* A frame is a **boot artifact**, and `--missing` names the rows whose picture is gone instead of guessing them;
  today none are, and the one row the table has that the load table does not is named by the Status block.

    python3 tools/xemu/map-frame.py --work $WORK/xemu -o inventory/map_frames.csv
    python3 tools/xemu/map-frame.py --frame index-41.png            # one frame, printed
    python3 tools/xemu/map-frame.py --work $WORK/xemu --missing      # PASS rows with no frame left
    python3 tools/xemu/map-frame.py --write-doc docs/MAP_FRAMES.md   # from the CSV only, no frames needed
    python3 tools/xemu/map-frame.py --check-doc docs/MAP_FRAMES.md
    python3 tools/xemu/map-frame.py --check                          # synthetic frames, no dump and no frames

`--write-doc` and `--check-doc` render from the committed CSV joined to `inventory/map_loads.csv` (for the names),
so the page cannot drift from the table without a disc, and `--check` builds six synthetic frames -- HUD and
no-HUD at two resolutions -- plus one frame bright only outside the corner, writes them to a temporary directory
and classifies them through the same decode path, and re-decodes a fixture through each of the five PNG row
filters, so the instrument cannot rot behind a table that looks right.

Frames are decoded here, not by ImageMagick: `console-line.py` needs `convert` and pays for it in a process per
crop, and a PNG reader is what lets `--check` run anywhere. Only non-interlaced 8-bit PNGs are read, which is what
`import` writes; anything else is refused by name rather than measured wrongly.
"""
import argparse
import csv
import io
import os
import struct
import sys
import tempfile
import zlib

COLUMNS = ["index", "name", "verdict", "corner", "prompt", "run"]
TABLE_MARK = ("<!-- generated:map-frames -->", "<!-- /generated:map-frames -->")
STATUS_MARK = ("<!-- generated:map-frames-status -->", "<!-- /generated:map-frames-status -->")
TSV = "missions.tsv"
# Directories that exist to prove the retry loop, the halt path or a console read, not to measure a load.
EXCLUDED = ("missions-neg", "halt-test", "halt-test2", "mtest", "ms1")
EXCLUDED_PREFIXES = ("sink",)
PORTRAIT_MIN = 0.06     # the dimmest HUD corner reads 0.140, the brightest non-HUD one 0.027
BRIGHT = 100            # max(r,g,b) above this is a drawn pixel
CORNERS = {
    # (x, y, w, h) as fractions of the frame: measured at 1280x960 and scaled for anything else
    "portrait": (0.0, 0.0, 0.25, 0.10),
    "prompt": (0.8125, 0.8958, 0.1875, 0.1042),
}


# ---------------------------------------------------------------- PNG decode

def read_png(path):
    """(width, height, rows) of an 8-bit non-interlaced PNG; rows are bytes, 3 per pixel."""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit("%s: not a PNG" % path)
    pos, idat = 8, []
    width = height = depth = colour = None
    interlace = 0
    palette = b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if depth != 8 or interlace != 0 or colour not in (0, 2, 3, 6):
        sys.exit("%s: only 8-bit non-interlaced grey/RGB/palette PNGs are read (depth %r, colour %r, "
                 "interlace %r)" % (path, depth, colour, interlace))
    channels = {0: 1, 2: 3, 3: 1, 6: 4}[colour]
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    rows, prev, at = [], bytearray(stride), 0
    for _ in range(height):
        filt = raw[at]
        at += 1
        line = bytearray(raw[at:at + stride])
        at += stride
        if filt == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif filt != 0:
            sys.exit("%s: unknown row filter %d" % (path, filt))
        rows.append(bytes(line))
        prev = line
    if colour == 3:
        if not palette:
            sys.exit("%s: palette image with no PLTE" % path)
        rows = [bytes(b for i in row for b in palette[3 * i:3 * i + 3]) for row in rows]
    elif colour == 0:
        rows = [bytes(b for value in row for b in (value, value, value)) for row in rows]
    elif colour == 6:
        rows = [bytes(b for i in range(0, len(row), 4) for b in row[i:i + 3]) for row in rows]
    return width, height, rows


def region_bright(width, height, rows, box):
    """The fraction of a fractional box whose pixels have a channel above BRIGHT."""
    x0 = int(round(box[0] * width))
    y0 = int(round(box[1] * height))
    x1 = min(width, int(round((box[0] + box[2]) * width)))
    y1 = min(height, int(round((box[1] + box[3]) * height)))
    total = bright = 0
    for y in range(y0, y1):
        row = rows[y]
        for x in range(x0, x1):
            r, g, b = row[3 * x], row[3 * x + 1], row[3 * x + 2]
            total += 1
            if max(r, g, b) > BRIGHT:
                bright += 1
    return bright / total if total else 0.0


def measure(path):
    """(portrait, prompt) bright fractions of one frame."""
    width, height, rows = read_png(path)
    return tuple(region_bright(width, height, rows, CORNERS[k]) for k in ("portrait", "prompt"))


def verdict(portrait):
    """The reading the module docstring tabulates: is the HUD corner drawn, or not."""
    return "hud" if portrait >= PORTRAIT_MIN else "no-hud"


# ---------------------------------------------------------------- runs and rows

def run_dirs(work):
    """Every directory under WORK that holds a `missions.tsv`, two levels down at most.

    The sweeps are not all in one place: `$WORK/xemu/missions-*` holds the first half and the head probes,
    `$WORK/mapback*` and `$WORK/missions-*` the campaign's back half, and `map-loads.py` is given them as
    arguments. Scanning keeps that list from being typed twice, and `EXCLUDED` keeps the directories that exist
    to prove a failure path out of the table.
    """
    if not os.path.isdir(work):
        sys.exit("%s: no such work directory" % work)
    found = []
    for base, dirs, files in os.walk(work):
        rel = os.path.relpath(base, work)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > 2:
            dirs[:] = []
            continue
        name = os.path.basename(base)
        if TSV in files and name not in EXCLUDED and not name.startswith(EXCLUDED_PREFIXES):
            found.append(base)
    return sorted(found)


def read_rows(directory):
    """[(index, status, frame)] from one run directory's missions.tsv (its third column is the frame)."""
    out = []
    path = os.path.join(directory, TSV)
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            out.append((int(parts[0]), parts[1].strip().upper(), parts[2].strip()))
    return out


def frames_by_index(work):
    """{index: (frame path, run name)} for every PASS row whose frame is still on disk."""
    found = {}
    for directory in run_dirs(work):
        for index, status, frame in read_rows(directory):
            if status != "PASS":
                continue
            if not frame.lower().endswith(".png"):
                frame = os.path.join(directory, "index-%02d" % index, "02-mission.png")
            if not os.path.exists(frame):
                continue
            # the first run directory in path order that still holds a frame supplies it
            found.setdefault(index, (frame, os.path.relpath(directory, work)))
    return found


def missing(work):
    """[(index, run)] for every PASS row no surviving frame covers."""
    have = frames_by_index(work)
    out = {}
    for directory in run_dirs(work):
        for index, status, _frame in read_rows(directory):
            if status == "PASS" and index not in have:
                out.setdefault(index, os.path.basename(directory))
    return sorted(out.items())


def load_table():
    """{index: (name, result)} from the committed map-loads table, so the page needs no disc."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, "inventory", "map_loads.csv")
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {int(row["index"]): (row["name"], row["result"]) for row in csv.DictReader(fh)}


def names():
    """{index: name} -- the load table's name column alone."""
    return {index: name for index, (name, _result) in load_table().items()}


# ---------------------------------------------------------------- rendering

def build(work):
    """The rows this tool writes: one per PASS row with a surviving frame."""
    known = names()
    rows = []
    for index, (frame, run) in sorted(frames_by_index(work).items()):
        portrait, prompt = measure(frame)
        rows.append({"index": index, "name": known.get(index, ""), "verdict": verdict(portrait),
                     "corner": "%.4f" % portrait, "prompt": "%.4f" % prompt, "run": run})
    return rows


def write_csv(rows, path):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    open(path, "w", newline="", encoding="utf-8").write(buf.getvalue())
    return buf.getvalue()


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def render_table(rows):
    known = names()
    out = ["| index | map | verdict | top-left corner | bottom-right | run |",
           "|---:|---|---|---:|---:|---|"]
    for row in rows:
        out.append("| %s | `%s` | %s | %s | %s | `%s` |" % (
            row["index"], known.get(int(row["index"]), row["name"]), row["verdict"],
            row["corner"], row["prompt"], row["run"]))
    return "\n".join(out)


def render_status(rows):
    count = {}
    for row in rows:
        count[row["verdict"]] = count.get(row["verdict"], 0) + 1
    total = len(rows)
    hud = count.get("hud", 0)
    nohud = total - hud
    lines = ["**%d frames read: %d `hud`, %d `no-hud`.**" % (total, hud, nohud), ""]
    if total:
        lines.append("%.0f%% of the frames whose verdict was taken with a picture still on disk show the HUD "
                     "corner, so the load reached a frame the player was in control of." % (100.0 * hud / total))
        lowest = min(float(r["corner"]) for r in rows if r["verdict"] == "hud") if hud else 0.0
        highest = max(float(r["corner"]) for r in rows if r["verdict"] != "hud") if nohud else 0.0
        lines.append("")
        lines.append("The corner reads **%.3f** on the dimmest `hud` frame and **%.3f** on the brightest frame that "
                     "is not one, so the %.2f threshold sits %.1fx below the first and %.1fx above the second."
                     % (lowest, highest, PORTRAIT_MIN, lowest / PORTRAIT_MIN,
                        PORTRAIT_MIN / highest if highest else float("inf")))
        ahead = [r for r in rows if load_table().get(int(r["index"]), ("", "PASS"))[1] != "PASS"]
        if ahead:
            lines.append("")
            lines.append("%d row(s) here are not `PASS` in `inventory/map_loads.csv` -- %s. A `PASS` row for them "
                         "is on disk, so a run that table was not given passed them; the corner reading is what "
                         "decides whether that pass was real."
                         % (len(ahead), ", ".join("index %s (`%s`, the load table says %s)"
                                                  % (r["index"], load_table()[int(r["index"])][0],
                                                     load_table()[int(r["index"])][1]) for r in ahead)))
        if nohud:
            lines.append("")
            lines.append("The `no-hud` rows are %s." % "; ".join(
                "`%s` (index %s, corner %s, bottom-right %s)" % (names().get(int(r["index"]), r["name"]),
                                                                 r["index"], r["corner"], r["prompt"])
                for r in rows if r["verdict"] != "hud"))
    return "\n".join(lines)


def splice_text(text, mark, body):
    """Replace the block between `mark` with `body`, keeping every other byte."""
    start, end = mark
    if text.count(start) != 1 or text.count(end) != 1:
        sys.exit("expected exactly one %s/%s pair" % (start, end))
    head, rest = text.split(start)
    _old, tail = rest.split(end)
    return head + start + "\n" + body + "\n" + end + tail


def write_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    text = splice_text(text, TABLE_MARK, render_table(rows))
    text = splice_text(text, STATUS_MARK, render_status(rows))
    open(path, "w", encoding="utf-8").write(text)


def check_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    want = splice_text(splice_text(text, TABLE_MARK, render_table(rows)), STATUS_MARK, render_status(rows))
    if want != text:
        sys.exit("%s: the generated blocks are stale (re-run with --write-doc)" % path)


# ---------------------------------------------------------------- self-test

def write_png(path, width, height, pixels):
    """A minimal 8-bit RGB PNG, filter 0: `pixels` is a list of rows of (r,g,b)."""
    raw = b"".join(b"\x00" + b"".join(bytes(px) for px in row) for row in pixels)

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    open(path, "wb").write(b"\x89PNG\r\n\x1a\n"
                           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def fixture(directory, name, width, height, corners):
    """A frame painted at the given fractional corners: {corner: brightness}."""
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            value = 0
            for corner, level in corners.items():
                fx, fy, fw, fh = CORNERS[corner]
                if fx * width <= x < (fx + fw) * width and fy * height <= y < (fy + fh) * height:
                    value = level
            row.append((value, value, value))
        rows.append(row)
    path = os.path.join(directory, name)
    write_png(path, width, height, rows)
    return path


def encode_png(path, width, height, rows, filt):
    """A PNG whose every row uses filter `filt`, to prove the decoder against its own encoder."""
    channels = 3
    stride = width * channels
    out = []
    prev = bytearray(stride)
    for row in rows:
        line = bytearray(b for px in row for b in px)
        encoded = bytearray(stride)
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if filt == 0:
                pred = 0
            elif filt == 1:
                pred = a
            elif filt == 2:
                pred = b
            elif filt == 3:
                pred = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            encoded[i] = (line[i] - pred) & 0xFF
        out.append(bytes([filt]) + bytes(encoded))
        prev = line
    raw = b"".join(out)

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    open(path, "wb").write(b"\x89PNG\r\n\x1a\n"
                           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def selftest():
    """Synthetic frames through the same decode and classify path: no dump, no frames, no writes to the tree."""
    failures = []
    with tempfile.TemporaryDirectory() as directory:
        cases = [
            # name, size, portrait painted, prompt painted, expected verdict
            ("hud.png", (320, 240), True, False, "hud"),
            ("nohud-prompt.png", (320, 240), False, True, "no-hud"),
            ("nohud-plain.png", (320, 240), False, False, "no-hud"),
            ("hud-half.png", (160, 120), True, False, "hud"),
            ("nohud-half.png", (160, 120), False, True, "no-hud"),
        ]
        for name, (width, height), portrait_level, prompt_level, want in cases:
            corners = {}
            if portrait_level:
                corners["portrait"] = 255
            if prompt_level:
                corners["prompt"] = 255
            path = fixture(directory, name, width, height, corners)
            portrait, prompt = measure(path)
            got = verdict(portrait)
            if got != want:
                failures.append("%s: %s (corner %.4f, prompt %.4f), expected %s"
                                % (name, got, portrait, prompt, want))
        # a frame brighter than the threshold only outside the corner is still not a HUD frame
        path = os.path.join(directory, "bright-elsewhere.png")
        rows = [[(0, 0, 0)] * 320 for _ in range(240)]
        for y in range(40, 200):
            for x in range(320):
                rows[y][x] = (255, 255, 255)
        write_png(path, 320, 240, rows)
        got = verdict(measure(path)[0])
        if got != "no-hud":
            failures.append("bright-elsewhere.png: %s, expected no-hud (the witness is the corner)" % got)
        # every row filter decodes to the bytes that were encoded
        rows = [[(x * 7 % 256, y * 3 % 256, (x + y) % 256) for x in range(32)] for y in range(24)]
        for filt in range(5):
            path = os.path.join(directory, "filter-%d.png" % filt)
            encode_png(path, 32, 24, rows, filt)
            width, height, decoded = read_png(path)
            want = b"".join(bytes(px) for row in rows for px in row)
            got = b"".join(decoded)
            if got != want:
                failures.append("filter-%d.png: decoded bytes differ from the encoded ones" % filt)
    if failures:
        for line in failures:
            print("FAIL --check: " + line)
        return 1
    print("ok   map-frame: %d synthetic frames classified at two resolutions, all five row filters decoded"
          % (len(cases) + 1))
    return 0


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", help="the xemu work directory holding the missions* run directories")
    parser.add_argument("--frame", help="one frame to measure, printed and not written")
    parser.add_argument("--missing", action="store_true", help="list PASS rows with no surviving frame")
    parser.add_argument("-o", "--output", help="write the CSV here")
    parser.add_argument("--write-doc", help="render the generated blocks of this page from the CSV")
    parser.add_argument("--check-doc", help="refuse a page whose generated blocks do not match the CSV")
    parser.add_argument("--check", action="store_true", help="synthetic self-test: no dump, no frames, no writes")
    args = parser.parse_args()

    if args.check:
        return selftest()
    if args.frame:
        portrait, prompt = measure(args.frame)
        print("%s verdict=%s portrait=%.4f prompt=%.4f" % (args.frame, verdict(portrait, prompt), portrait, prompt))
        return 0
    if args.missing:
        rows = missing(args.work)
        for index, run in rows:
            print("%d\t%s" % (index, run))
        print("%d PASS rows have no frame on disk" % len(rows))
        return 0
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        source = args.output or os.path.join(root, "inventory", "map_frames.csv")
        if not os.path.exists(source):
            sys.exit("%s: no such table" % source)
        rows = read_csv(source)
        if args.check_doc:
            check_doc(path, rows)
            print("ok   map-frame: %s matches %s" % (path, os.path.relpath(source)))
        else:
            write_doc(path, rows)
            print("wrote %s from %s" % (path, os.path.relpath(source)))
        return 0
    if not args.work:
        parser.error("one of --work, --frame, --missing, --write-doc, --check-doc or --check is required")
    rows = build(args.work)
    if args.output:
        write_csv(rows, args.output)
        print("wrote %s: %d rows" % (args.output, len(rows)))
    else:
        sys.stdout.write(render_table(rows) + "\n")
    gone = missing(args.work)
    if gone:
        print("%d PASS rows have no frame on disk" % len(gone))
    return 0


if __name__ == "__main__":
    sys.exit(main())
