#!/usr/bin/env python3
"""What is the console drawing in this frame? (PLAN 7.3)

The question this answers is not "is the console open" -- the stock build's console has no window, so "open" and
"closed" are not distinguishable from a frame. What is drawn is the **input line**, one cyan line of text in the
bottom left, and it has three states that a pixel count alone cannot separate:

| drawn | example | cyan pixels | trimmed extent |
|---|---|---:|---|
| nothing | the frame before `Tab` lands | 0 | -- |
| the empty prompt | `(>_` | 46--62 | **50--64 x 32** |
| the prompt with text | `(> stat fps_` (stock) | 113--138 | **176 x 32** |
| the prompt with text | `(> Say: at fps_` (`GUI.ExtendedConsole`) | 141--150 | **210 x 32** |
| cyan that is not a line | the title screen's own cyan art | 22 | 554 x **246** |
| cyan that is not a line | a menu button's cyan glyph | 87 | **18 x 8** |

All measured at 1280x960, crop `576x268+0+691`, over fifteen frames in four sessions (`docs/XEMU.md`). The count
alone was never a verdict: an empty prompt measures 46--62 and a threshold tuned for `stat fps` calls it absent,
which is how a console that was open and empty read as "never opened" -- and the title screen carries 22 cyan pixels
of its own, which the old 20-pixel threshold called `present`. The **extent** separates the states: the console line
is one 32-px-tall band at the same corner, so a wide *and tall* cyan area is not a line, and the width of the band
says whether the prompt holds text.

Two readings this tool cannot make, both measured: a frame can be captured before the guest has rendered keys that
were already sent (an eight-key burst plus a 1 s wait still drew an empty prompt; the frame 3 s later had every
character), so `nothing` is not evidence a key never arrived; and a submitted command and a closed console look
identical (both lose the line).

    tools/xemu/console-line.py FRAME.png               # exit 0 drawn, exit 2 nothing drawn
    tools/xemu/console-line.py FRAME.png --require=typed   # exit 2 unless text is drawn
    tools/xemu/console-line.py FRAME.png --crop=WxH+X+Y

The crop defaults to `576x268+0+691` at 1280x960 -- the bottom-left corner where the line draws -- computed from the
frame's size for other resolutions. `--min-text` is the trimmed width, in pixels, above which the line counts as
text; it is calibrated at 1280x960 and scales with the frame width, so a taller capture needs it raised by hand.
Uses ImageMagick, which the harness already requires (`import`, `compare`, `convert`).
"""
import argparse
import subprocess
import sys

DEFAULT_THRESHOLD = 20      # cyan pixels below which nothing is drawn (measured: 0 with no line, 46+ with one)
DEFAULT_MIN_TEXT = 96       # trimmed width above which the drawn line holds text (measured: 50--64 empty, 176+ typed)
PROMPT_EXTENT = 0.075       # DEFAULT_MIN_TEXT as a fraction of a 1280-px-wide frame, for other resolutions
LINE_HEIGHT = 0.05          # a console line is one 32-px band: 5% of a 960-px-tall frame separates it from art
LINE_WIDTH = 0.03           # ...and at least 3% of the frame's width: the shortest drawn prompt is 50 px of 1280


def size(path):
    out = subprocess.run(["identify", "-format", "%w %h", path], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("cannot read %s: %s" % (path, out.stderr.strip()))
    w, h = out.stdout.split()
    return int(w), int(h)


def cyan_points(path, crop):
    """Every cyan pixel of the crop, as (x, y) inside the crop."""
    out = subprocess.run(["convert", path, "-crop", crop, "+repage", "-depth", "8", "txt:-"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("convert failed on %s: %s" % (path, out.stderr.strip()))
    pts = []
    for line in out.stdout.splitlines()[1:]:      # first line is the header, e.g. "# ImageMagick pixel enumeration"
        # `0,0: (17,16,16)  #111010  srgb(17,16,16)` -- the hex is the second field, after the decimal triplet.
        x, _, rest = line.partition(",")
        y, _, hexpart = rest.partition(":")
        hexpart = hexpart.split("#")
        if len(hexpart) < 2:
            continue
        hx = hexpart[1][:6]
        try:
            r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
        except ValueError:
            continue
        if g > 120 and b > 120 and r < 110:       # the console text's cyan
            try:
                pts.append((int(x), int(y)))
            except ValueError:
                continue
    return pts


def extent(pts):
    """Trimmed bounding box of the drawn pixels: (width, height, left, top)."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, min(xs), min(ys)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("frame")
    ap.add_argument("--crop", default=None, help="ImageMagick geometry (default: bottom-left, from the frame size)")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help="cyan pixels that mean 'something drawn'")
    ap.add_argument("--min-text", type=int, default=None,
                    help="trimmed width, in pixels, that means 'text drawn' (default: 7.5%% of the frame width, "
                         "which is 96 at 1280x960)")
    ap.add_argument("--max-line-height", type=int, default=None,
                    help="trimmed height above which the cyan is not a console line (default: 5%% of the frame "
                         "height, which is 48 at 1280x960)")
    ap.add_argument("--min-line-width", type=int, default=None,
                    help="trimmed width below which the cyan is not a console line (default: 3%% of the frame "
                         "width, which is 38 at 1280x960)")
    ap.add_argument("--require", choices=("drawn", "typed"), default="drawn",
                    help="what exit 0 means: anything drawn (default), or text drawn")
    ap.add_argument("--quiet", action="store_true", help="no line on stdout, exit code only")
    args = ap.parse_args()

    if args.crop:
        crop = args.crop
    else:
        w, h = size(args.frame)
        crop = "%dx%d+0+%d" % (int(w * 0.45), int(h * 0.28), int(h * 0.72))
    frame_w, frame_h = size(args.frame)
    min_text = args.min_text
    if min_text is None:
        min_text = max(1, int(frame_w * PROMPT_EXTENT))
    max_height = args.max_line_height
    if max_height is None:
        max_height = max(1, int(frame_h * LINE_HEIGHT))
    min_width = args.min_line_width
    if min_width is None:
        min_width = max(1, int(frame_w * LINE_WIDTH))
    pts = cyan_points(args.frame, crop)

    if len(pts) < args.threshold:
        state, detail = "absent", "%d cyan pixels (< %d)" % (len(pts), args.threshold)
    else:
        width, height, left, top = extent(pts)
        if height > max_height or width < min_width:
            state = "other"
            detail = ("%d cyan pixels, %dx%d at +%d+%d (not a console line: a line is %d--%d px wide and at most "
                      "%d px tall)") % (len(pts), width, height, left, top, min_width, frame_w, max_height)
        elif width >= min_text:
            state = "typed"
            detail = "%d cyan pixels, %dx%d at +%d+%d (>= %d px wide: text)" % (
                len(pts), width, height, left, top, min_text)
        else:
            state = "prompt"
            detail = "%d cyan pixels, %dx%d at +%d+%d (< %d px wide: empty prompt)" % (
                len(pts), width, height, left, top, min_text)

    ok = state == "typed" if args.require == "typed" else state != "absent"
    if not args.quiet:
        print("console line: %s (%s)" % (state, detail))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
