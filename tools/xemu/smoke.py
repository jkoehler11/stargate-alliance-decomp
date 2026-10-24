#!/usr/bin/env python3
"""Boot the prototype in xemu and check that the guest actually ran (PLAN 7.2).

The other two scripts in this directory predate the xemu version this project pins: `boot-test.sh`
passes `-mcpx_path`, which xemu 0.8.139 no longer accepts (it hands everything after `-display xemu`
to QEMU, and rejects the option), and `build-xiso.sh` calls extract-xiso 2.7 with `-o`, which that
version does not take. This one uses the configuration `play.sh` writes -- a TOML file -- because that
is the form the pinned emulator reads.

    # needs a graphical session: xemu wants a GL context even when nothing is on screen
    DISPLAY=:0 XAUTHORITY=/run/user/1000/xauth_* \\
        python3 tools/xemu/smoke.py --iso=$WORK/sg1.iso --bios=$SG1_DUMP/bios \\
            --out=$WORK/smoke-1 --seconds=100

Exit status is the result: 0 when the serial log contains engine output, 2 when the boot produced
nothing, 3 when xemu itself refused to start. What it proves is "the image boots and the engine runs";
loading a mission needs a button press, which is the input-automation gap in docs/XEMU.md.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

MARKERS = [r"Log:", r"Build:\s*\w{3}\s+\d+\s+\d{4}", r"Unreal", r"Init:"]
DEFAULT_XEMU = os.path.expanduser("~/tools/xemu.AppImage")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True, help="XISO built with extract-xiso")
    ap.add_argument("--bios", required=True, help="directory with mcpx*.bin and a *debug* flash")
    ap.add_argument("--out", required=True, help="scratch directory for the log and config")
    ap.add_argument("--seconds", type=int, default=100)
    ap.add_argument("--hdd", help="hard-disk image (default: a blank one xemu creates)")
    ap.add_argument("--xemu", default=os.environ.get("XEMU", DEFAULT_XEMU))
    args = ap.parse_args()

    if not os.path.isfile(args.iso):
        sys.exit("no ISO at %s -- build one first (see docs/XEMU.md)" % args.iso)
    mcpx = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                 if f.lower().startswith("mcpx")), None)
    flash = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                  if f.lower().endswith(".bin") and "debug" in f.lower()), None)
    if not mcpx or not flash:
        sys.exit("need mcpx*.bin and a debug flash in %s" % args.bios)
    if not os.access(args.xemu, os.X_OK):
        sys.exit("xemu not executable at %s (set XEMU)" % args.xemu)

    os.makedirs(args.out, exist_ok=True)
    serial = os.path.join(args.out, "serial.log")
    toml = os.path.join(args.out, "xemu.toml")
    hdd = args.hdd or os.path.join(args.out, "hdd.qcow2")
    with open(toml, "w") as f:
        f.write("[general]\nshow_welcome = false\n[sys]\nmem_limit = '128'\n[sys.files]\n")
        f.write("bootrom_path = '%s'\nflashrom_path = '%s'\n" % (mcpx, flash))
        f.write("eeprom_path = '%s'\nhdd_path = '%s'\ndvd_path = '%s'\n"
                % (os.path.join(args.out, "eeprom.bin"), hdd, args.iso))

    env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1")
    log = open(os.path.join(args.out, "xemu.log"), "wb")
    print("xemu: %s\nbios: %s\niso:  %s\nout:  %s" % (args.xemu, flash, args.iso, args.out))
    proc = subprocess.Popen([args.xemu, "-config_path", toml, "-serial", "file:" + serial],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    deadline = time.time() + args.seconds
    while time.time() < deadline and proc.poll() is None:
        time.sleep(1)
    alive = proc.poll() is None
    if alive:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    log.close()

    if not os.path.exists(serial):
        print("FAIL: xemu wrote no serial log (it never reached the guest)")
        tail = open(os.path.join(args.out, "xemu.log"), errors="replace").read().splitlines()[-6:]
        for line in tail:
            print("   " + line)
        return 3
    text = open(serial, errors="replace").read()
    hits = {m: bool(re.search(m, text)) for m in MARKERS}
    print("serial: %d bytes, process was %s after %ds"
          % (len(text), "alive" if alive else "gone", args.seconds))
    for m, found in hits.items():
        print("   %-28s %s" % (m, "seen" if found else "absent"))
    first = [l for l in text.splitlines() if l.strip()][:6]
    for line in first:
        print("   | " + line[:120])
    if any(hits.values()):
        print("PASS: the guest ran and logged")
        return 0
    print("FAIL: serial log has no engine output")
    return 2


if __name__ == "__main__":
    sys.exit(main())
