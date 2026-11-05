#!/usr/bin/env python3
"""Ask a frame whether the guest has fallen back to the BIOS screen.

A corrupt map does not assert in this build. `appAssertFailed` logs and returns (docs/NATIVE_NOTES.md, U6), so a map
that fails to load leaves no dialog behind -- but it does not leave the game running either: measured 2026-11-04, a
truncated `M00.unr` selected from QUICK SELECT MAP puts the guest on the BIOS screen ("Please insert an Xbox disc...")
within 20 s, with both EIP samples in the kernel idle loop (`0x80024a97`, `0x80024a98`).

That matters for the harness because such a frame *passes* a "did the screen repaint" test: the screen changes from the
map list to black, so a diff-based assertion reports success for a guest that has crashed. This tool is the assertion
that belongs next to the repaint check.

Measured on four frames (1280x960, Xbox limited range so "black" is 16, not 0):

| frame | black (<= 20) | p99 | text pixels, top-left |
|---|---|---|---|
| BIOS prompt (`assert3/03-after20.png`) | **0.9972** | **13** | 1,808 |
| in-game `M00` (`travel20b/03-ingame.png`) | 0.1488 | 192 | 16,037 |
| title menu (`assert2/00-title.png`) | 0.1984 | 255 | 20,201 |
| map list (`index-38/01-highlight.png`) | 0.1074 | 218 | 4,394 |

So the threshold is nowhere near the normal frames: a frame is the BIOS screen when at least 98% of it is at or below
20 and its 99th percentile is at or below 30. The game always draws something brighter than that.

Usage:
    guest-halt.py FRAME [FRAME ...] [--json]

Exit: 0 = at least one frame is the BIOS screen, 2 = all frames are running, 3 = a frame could not be read.
"""
import json
import sys

from PIL import Image

BLACK_MAX = 20      # limited-range black is 16; the prompt's own glyphs are the only bright thing on it
BLACK_SHARE = 0.98  # measured 0.9972 on the prompt, 0.20 or less on every running frame
P99_MAX = 30        # measured 13 on the prompt, 192 or more on every running frame
TEXT_BOX = (0.00, 0.00, 0.60, 0.30)  # the prompt sits top-left; measured 1,808 bright pixels there
TEXT_MIN = 200


def measure(path):
    image = Image.open(path).convert("L")
    width, height = image.size
    total = float(width * height)
    histogram = image.histogram()
    black = sum(histogram[:BLACK_MAX + 1]) / total
    running = 0
    p99 = 255
    for value, count in enumerate(histogram):
        running += count
        if running >= 0.99 * total:
            p99 = value
            break
    left, top, right, bottom = TEXT_BOX
    box = image.crop((int(width * left), int(height * top), int(width * right), int(height * bottom)))
    text = sum(box.histogram()[151:])
    halted = black >= BLACK_SHARE and p99 <= P99_MAX and text >= TEXT_MIN
    return {"frame": path, "black": round(black, 4), "p99": p99, "text": text, "halted": halted}


def main(argv):
    as_json = "--json" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__.strip(), file=sys.stderr)
        return 3
    rows = []
    for path in paths:
        try:
            rows.append(measure(path))
        except Exception as exc:  # noqa: BLE001 - a frame that will not open is a harness error, not a verdict
            print("%s: cannot read (%s)" % (path, exc), file=sys.stderr)
            return 3
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print("%-52s black=%.4f p99=%3d text=%5d  %s" % (row["frame"], row["black"], row["p99"], row["text"],
                                                             "BIOS SCREEN" if row["halted"] else "running"))
    return 0 if any(row["halted"] for row in rows) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
