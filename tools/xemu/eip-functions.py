#!/usr/bin/env python3
"""Sample EIP over a live boot and say which *named functions* the guest executes (PLAN 7.x).

`smoke.py` answers "is the guest running game code" with ten samples and a yes/no. This answers the next
question — *which* code — by sampling densely and mapping every EIP into `symbols/xbox_functions.csv`. It is
the cheap end of runtime pairing: no breakpoints, no debugger, just the monitor's register read.

    python3 tools/xemu/eip-functions.py --iso <dump dir> --dvd-dir \\
        --bios ~/nfs/sg1-dump/bios --hdd ~/nfs/sg1-dump/bios/xbox_hdd.qcow2 \\
        --out ~/sg1/work/eip-run --seconds 150 --samples 200 --interval 0.25

Prints the hit distribution by function and flags named targets that were reached. Nothing is committed:
the samples describe one boot on one machine.
"""

import argparse
import collections
import csv
import importlib.util
import os
import re
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "xemu"))

_spec = importlib.util.spec_from_file_location("smoke", os.path.join(ROOT, "tools", "xemu", "smoke.py"))
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

EIP_RE = re.compile(r"EIP=([0-9a-fA-F]{8})")


def load_functions():
    rows = [(int(r["address"], 16), int(r["size"]), r["name"])
            for r in csv.DictReader(open(os.path.join(ROOT, "symbols", "xbox_functions.csv")))]
    rows.sort()
    return rows


def load_vpad():
    spec = importlib.util.spec_from_file_location("vpad", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                      "vpad.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def containing(rows, va):
    best = None
    for addr, size, name in rows:
        if addr > va:
            break
        if addr <= va < addr + size and (best is None or size < best[1]):
            best = (addr, size, name)
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tap-at", action="append", default=[], metavar="SECS:BUTTON",
                    help="press a pad button this many seconds into sampling (implies --vpad); the game idles at the "
                         "title until something presses it, so a sample with no tap measures the wait")
    ap.add_argument("--vpad", action="store_true",
                    help="create the virtual pad before xemu starts; without a controller this game never leaves idle")
    ap.add_argument("--iso", required=True)
    ap.add_argument("--dvd-dir", action="store_true")
    ap.add_argument("--bios", required=True)
    ap.add_argument("--hdd", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=150)
    ap.add_argument("--samples", type=int, default=200)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--watch", action="append", default=[],
                    help="symbol name to report explicitly (repeatable)")
    ap.add_argument("--xemu", default=os.environ.get("XEMU", smoke.DEFAULT_XEMU))
    args = ap.parse_args()

    mcpx = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                 if f.lower().startswith("mcpx")), None)
    flash = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                  if f.lower().endswith(".bin") and "debug" in f.lower()), None)
    if not mcpx or not flash or not os.path.isfile(args.hdd):
        raise SystemExit("need mcpx*.bin, a debug flash and an HDD image in %s" % args.bios)

    os.makedirs(args.out, exist_ok=True)
    toml = os.path.join(args.out, "xemu.toml")
    sock_path = os.path.join(args.out, "qmp.sock")
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    # A controller is not optional for this game: without one it sits in the kernel's idle loop, and every sample comes
    # back as `0x8001d1ea` with `HLT=1` -- which reads exactly like a crashed guest. drive.py creates the pad for the
    # same reason; the sampler has to as well or its numbers cannot be compared with a driver run.
    taps = []
    for spec in args.tap_at:
        secs, _, button = spec.partition(":")
        try:
            taps.append((float(secs), button))
        except ValueError:
            raise SystemExit("--tap-at wants SECS:BUTTON, got %r" % spec)
    taps.sort()
    if taps:
        args.vpad = True          # a tap without a pad is a no-op, and a no-op tap is how a run measures the wait

    pad = None
    pad_guid = None
    if args.vpad:
        module = load_vpad()
        pad = module.Pad(settle=1.5)
        pad_guid = module.GUID
        print("pad: created before the emulator starts, so SDL sees it at init")

    with open(toml, "w") as f:
        f.write("[general]\nshow_welcome = false\n[sys]\nmem_limit = '128'\n")
        f.write("[input]\nbackground_input_capture = true\n")
        if pad_guid:
            f.write("auto_bind = false\ngamepad_mappings = [\n    { gamepad_id = '%s'}\n    ]\n" % pad_guid)
            f.write("[input.bindings]\nport1_driver = 'usb-xbox-gamepad'\nport1 = '%s'\n" % pad_guid)
        f.write("[sys.files]\n")
        f.write("bootrom_path = '%s'\nflashrom_path = '%s'\n" % (mcpx, flash))
        f.write("hdd_path = '%s'\ndvd_path = '%s'\n" % (args.hdd, args.iso))

    env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1")
    log = open(os.path.join(args.out, "xemu.log"), "wb")
    print("booting: %s (sampling %d x %.2fs after %ds)" % (args.iso, args.samples, args.interval, args.seconds))
    proc = subprocess.Popen([args.xemu, "-config_path", toml,
                             "-serial", "file:" + os.path.join(args.out, "serial.log"),
                             "-qmp", "unix:%s,server,nowait" % sock_path],
                            stdout=log, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)
    eips = []
    try:
        sock = smoke.qmp_connect(sock_path, time.time() + 60)
        if not sock:
            print("FAIL: no QMP socket")
            return 3
        cmd = smoke.qmp_session(sock)
        time.sleep(args.seconds)
        started = time.time()
        for _ in range(args.samples):
            now = time.time() - started
            while taps and now >= taps[0][0]:
                _when, button = taps.pop(0)
                if pad is not None:
                    pad.tap(button, 0.15)
                print("   tap %s at %.1fs into sampling" % (button, now))
            regs = cmd("human-monitor-command", {"command-line": "info registers"}).get("return", "")
            m = EIP_RE.search(regs)
            if m:
                eips.append(int(m.group(1), 16))
            time.sleep(args.interval)
    finally:
        # The AppImage spawns AppRun -> qemu-system as children; terminating only the wrapper leaks a
        # qemu that keeps the HDD image locked and makes every later boot fail "Failed to get write lock".
        if pad is not None:
            pad.close()
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(10)
                break
            except subprocess.TimeoutExpired:
                continue
        log.close()

    rows = load_functions()
    named = collections.Counter()
    unknown = 0
    for e in eips:
        hit = containing(rows, e)
        if hit:
            named[hit[2]] += 1
        else:
            unknown += 1
    print("samples: %d (%d distinct EIP), inside a named function: %d, outside any: %d"
          % (len(eips), len(set(eips)), len(eips) - unknown, unknown))
    for name, n in named.most_common(12):
        print("   %-46s %3d (%4.1f%%)" % (name, n, 100.0 * n / max(1, len(eips))))
    for want in args.watch:
        got = named.get(want, 0)
        print("%s %s: %d sample(s)" % ("HIT " if got else "MISS", want, got))
    open(os.path.join(args.out, "eips.txt"), "w").write("\n".join("%#010x" % e for e in eips) + "\n")
    return 0 if named else 2


if __name__ == "__main__":
    sys.exit(main())