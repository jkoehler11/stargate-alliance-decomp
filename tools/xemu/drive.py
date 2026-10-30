#!/usr/bin/env python3
"""Drive the game with a keystroke and prove the screen changed (PLAN 7.2's pixel assertion).

The boot smoke test proves the guest executes game code by sampling `EIP`. It cannot tell whether the *display* is
alive or whether input reaches the guest, which is what a gameplay-level regression needs. xemu has no `screendump` in
its QMP, so this drives the X window it opens instead: capture, press a key, capture again, and compare.

    DISPLAY=:0 XAUTHORITY=/run/user/1000/xauth_XXXX python3 tools/xemu/drive.py \\
        --iso=$WORK/sg1.iso --bios=$SG1_DUMP/bios --hdd=$WORK/hdd.qcow2 --out=$WORK/drive

It needs `xdotool` and ImageMagick's `import`/`compare`, and an X display: over plain ssh xemu cannot open one at all
(docs/REGRESSION.md). Exit codes: 0 the screen changed, 2 it did not, 3 the emulator never came up.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import time

SMOKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke.py")


def load_smoke():
    spec = importlib.util.spec_from_file_location("smoke", SMOKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def find_window():
    """The xemu window, by title. SDL sets it from the executable name, so it may be 'xemu' or the AppImage's name."""
    for pattern in ("xemu", "Stargate", ""):
        args = ["xdotool", "search", "--onlyvisible", "--name", pattern] if pattern else \
               ["xdotool", "search", "--onlyvisible", "--class", "xemu"]
        got = run(args).stdout.split()
        if got:
            return got[-1]
    return None


def shot(window, path):
    got = run(["import", "-window", window, path])
    return got.returncode == 0 and os.path.isfile(path)


def difference(a, b):
    """Differing pixels between two PNGs, via ImageMagick's AE metric (which exits 1 when they differ)."""
    got = run(["compare", "-metric", "AE", a, b, "null:"])
    text = (got.stderr or got.stdout).strip().split()[0] if (got.stderr or got.stdout).strip() else ""
    try:
        return int(float(text))
    except ValueError:
        return -1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iso", required=True)
    ap.add_argument("--bios", required=True, help="directory with mcpx*.bin and a debug flash")
    ap.add_argument("--hdd", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=110, help="boot window before the first capture")
    ap.add_argument("--key", default="Tab", help="xdotool key name to press (Tab opens the console)")
    ap.add_argument("--settle", type=float, default=4.0, help="seconds between the key and the second capture")
    ap.add_argument("--threshold", type=int, default=200, help="differing pixels that count as a repaint")
    ap.add_argument("--xemu", default=os.environ.get("XEMU", smoke_default()))
    args = ap.parse_args()

    if not os.environ.get("DISPLAY"):
        print("FAIL: no DISPLAY -- xemu needs X11 or Wayland (docs/REGRESSION.md)")
        return 3
    for tool in ("xdotool", "import", "compare"):
        if not subprocess.run(["sh", "-c", "command -v %s" % tool], capture_output=True).returncode == 0:
            print("FAIL: %s is not installed" % tool)
            return 3

    os.makedirs(args.out, exist_ok=True)
    # The same discovery rule smoke.py uses: an mcpx boot ROM and a *debug* flash, which is what the boot needs.
    mcpx = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                 if f.lower().startswith("mcpx")), None)
    flash = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                  if f.lower().endswith(".bin") and "debug" in f.lower()), None)
    if not mcpx or not flash:
        print("FAIL: need mcpx*.bin and a debug flash in %s" % args.bios)
        return 3
    toml = os.path.join(args.out, "xemu.toml")
    with open(toml, "w") as f:
        f.write("[general]\nshow_welcome = false\n[sys]\nmem_limit = '128'\n[sys.files]\n")
        f.write("bootrom_path = '%s'\nflashrom_path = '%s'\n" % (mcpx, flash))
        f.write("hdd_path = '%s'\ndvd_path = '%s'\n" % (args.hdd, args.iso))
    env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1")
    log = open(os.path.join(args.out, "xemu.log"), "wb")
    proc = subprocess.Popen([args.xemu, "-config_path", toml,
                             "-serial", "file:" + os.path.join(args.out, "serial.log")],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        time.sleep(min(args.seconds, 10))
        if proc.poll() is not None:
            print("FAIL: xemu exited immediately (see %s/xemu.log)" % args.out)
            return 3
        print("waiting %ds for the title" % args.seconds)
        time.sleep(max(0, args.seconds - 10))
        window = find_window()
        if window is None:
            print("FAIL: no xemu window on %s" % os.environ.get("DISPLAY"))
            return 3
        print("window: %s" % window)
        before = os.path.join(args.out, "before.png")
        after = os.path.join(args.out, "after.png")
        if not shot(window, before):
            print("FAIL: could not capture the window")
            return 3
        run(["xdotool", "windowactivate", "--sync", window])
        run(["xdotool", "key", "--window", window, args.key])
        print("pressed %s" % args.key)
        time.sleep(args.settle)
        if not shot(window, after):
            print("FAIL: could not capture the second frame")
            return 3
        diff = difference(before, after)
        print("differing pixels: %d (threshold %d)" % (diff, args.threshold))
        if diff < 0:
            print("FAIL: could not compare the frames")
            return 3
        if diff >= args.threshold:
            print("PASS: the screen repainted after a keystroke")
            return 0
        print("FAIL: the screen did not change -- the key did not reach the guest")
        return 2
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def smoke_default():
    spec = importlib.util.spec_from_file_location("smoke", SMOKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DEFAULT_XEMU


if __name__ == "__main__":
    sys.exit(main())
