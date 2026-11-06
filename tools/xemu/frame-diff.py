#!/usr/bin/env python3
"""How much of the frame changed between two shots? (PLAN 7.2)

`missions.sh` asserts that the post-load frame repainted and that it is not the BIOS screen, and both pass for a load
that comes straight **back to the main menu**: `MainMenu.unr` is a list entry, and a `Travel` that bounces lands on a
menu that repaints like anything else. This tool supplies the missing comparison -- the post-load frame against the
*boot* frame, which is the main menu -- as a percentage of differing pixels.

    tools/xemu/frame-diff.py BEFORE.png AFTER.png            # changed: exit 0; unchanged: exit 2
    tools/xemu/frame-diff.py A.png B.png --min-pct 2.5 --fuzz 10%

Measured on the frames of four real runs at 1280x960 (`--fuzz 10%`, differing pixels as a share of the frame):

| post-load frame | vs the boot frame |
|---|---|
| index 71, `MainMenu.unr` (the page again) | 1.07% |
| index 70, `M21D` cutscene | 4.94% |
| index 72, `SGCTF-AbydosVsSGC` | 7.70% |
| index 41, `M15C` first-person HUD | 13.04% |

So the default threshold is 2.5%: comfortably above a menu that came back and below the smallest real load measured.
Uses ImageMagick `compare`/`identify`, which the harness already requires.
"""
import argparse
import subprocess
import sys


def pixels(path):
    out = subprocess.run(["identify", "-format", "%w %h", path], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("cannot read %s: %s" % (path, out.stderr.strip()))
    w, h = out.stdout.split()
    return int(w) * int(h)


def differing(a, b, fuzz, crop):
    cmd = ["compare", "-metric", "AE", "-fuzz", fuzz]
    if crop:
        cmd += ["-crop", crop]
    out = subprocess.run(cmd + [a, b, "null:"], capture_output=True, text=True)
    if out.returncode not in (0, 1):
        sys.exit("compare failed: %s" % out.stderr.strip())
    raw = out.stderr.strip().split()[0]
    try:
        return float(raw)
    except ValueError:
        sys.exit("cannot read compare output: %r" % out.stderr.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--min-pct", type=float, default=2.5,
                    help="percentage of the frame that must differ (default 2.5)")
    ap.add_argument("--fuzz", default="10%%", help="compare -fuzz value (default 10%%)")
    ap.add_argument("--crop", help="compare only this crop, e.g. 480x520+760+210")
    args = ap.parse_args()
    total = pixels(args.before)
    diff = differing(args.before, args.after, args.fuzz, args.crop)
    pct = 100.0 * diff / max(total, 1)
    verdict = "changed" if pct >= args.min_pct else "unchanged"
    print("%s -> %s: %.0f pixels differ (%.2f%% of %d), %s (needs %.2f%%)"
          % (args.before, args.after, diff, pct, total, verdict, args.min_pct))
    return 0 if pct >= args.min_pct else 2


if __name__ == "__main__":
    sys.exit(main())
