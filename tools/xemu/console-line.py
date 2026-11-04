#!/usr/bin/env python3
"""Is the console's input line drawn in this frame? (PLAN 7.3)

The build's configured console (`Engine.Console`) has no renderer at all, and the class that does render
(`GUI.ExtendedConsole`, reachable only by rebuilding the disc) draws one cyan input line at the bottom left. Both
facts make "the console is open" a question about a handful of pixels in a corner, which a whole-frame diff cannot
answer: the level behind it moves by tens of thousands of pixels and the console adds about a hundred.

Measured on two frames from the same session (docs/XEMU.md): with the line drawn, 128 cyan pixels in the crop below;
with the console closed, 0 in the same crop.

    tools/xemu/console-line.py FRAME.png            # present: exit 0, absent: exit 2
    tools/xemu/console-line.py FRAME.png --crop=WxH+X+Y

The crop defaults to the bottom-left tenth of the frame's height and 45% of its width, which is where the line
draws at 1280x960. Uses ImageMagick, which the harness already requires (`import`, `compare`, `convert`).
"""
import argparse
import subprocess
import sys

DEFAULT_THRESHOLD = 20   # measured: 128 with the line, 0 without; 20 is clear of both ends


def size(path):
    out = subprocess.run(["identify", "-format", "%w %h", path], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("cannot read %s: %s" % (path, out.stderr.strip()))
    w, h = out.stdout.split()
    return int(w), int(h)


def cyan_pixels(path, crop, threshold):
    out = subprocess.run(["convert", path, "-crop", crop, "+repage", "-depth", "8", "txt:-"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("convert failed on %s: %s" % (path, out.stderr.strip()))
    count = 0
    for line in out.stdout.splitlines()[1:]:      # first line is the header, e.g. "# ImageMagick pixel enumeration"
        # `0,0: (17,16,16)  #111010  srgb(17,16,16)` -- the hex is the second field, after the decimal triplet.
        _, _, hexpart = line.partition(")")
        hexpart = hexpart.strip()
        if not hexpart.startswith("#"):
            continue
        r = int(hexpart[1:3], 16)
        g = int(hexpart[3:5], 16)
        b = int(hexpart[5:7], 16)
        if g > 120 and b > 120 and r < 110:       # the console text's cyan
            count += 1
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("frame")
    ap.add_argument("--crop", default=None, help="ImageMagick geometry (default: bottom-left, from the frame size)")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help="cyan pixels that mean 'drawn'")
    args = ap.parse_args()

    if args.crop:
        crop = args.crop
    else:
        w, h = size(args.frame)
        crop = "%dx%d+0+%d" % (int(w * 0.45), int(h * 0.28), int(h * 0.72))
    count = cyan_pixels(args.frame, crop, args.threshold)
    verdict = "present" if count >= args.threshold else "absent"
    print("console line: %s (%d cyan pixels in %s, threshold %d)" % (verdict, count, crop, args.threshold))
    return 0 if verdict == "present" else 2


if __name__ == "__main__":
    sys.exit(main())
