#!/usr/bin/env python3
"""Boot the prototype in xemu and check that the guest actually executes game code (PLAN 7.2).

Measured on xemu 0.8.139, 2026-10-24, with the December 2005 dump:

* xemu no longer accepts `-mcpx_path` (it forwards everything after `-display xemu` to QEMU, which
  rejects it), so the configuration goes through a TOML file -- the form `play.sh` writes.
* `-serial <path>` needs QEMU's `file:` prefix.
* **Leave `eeprom_path` out.** A zero-filled EEPROM boots the machine but leaves the CPU halted in the
  kernel forever (EIP pinned at 0x8001d1ea, `HLT=1`); letting xemu create its own default EEPROM makes
  the title run. This is the difference between "xemu is alive" and "the game is running".
* A prepared HDD image is required; a fresh one also idles.
* `screendump` does not exist in this build (`CommandNotFound` over QMP), and the serial log stays at
  0 bytes, so the evidence channel is the CPU: sample EIP over the boot window and require that it
  leaves the kernel (0x80000000..) and lands in the image's own address space, varying between samples.

    python3 tools/xemu/smoke.py --iso=$WORK/sg1.iso --bios=$SG1_DUMP/bios \
        --hdd=$WORK/hdd.qcow2 --out=$WORK/smoke --seconds=150

Exit status: 0 when the guest ran game code, 2 when the machine stayed in the kernel (idle, wrong
EEPROM or HDD), 3 when xemu or its QMP socket never came up.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time

DEFAULT_XEMU = os.path.expanduser("~/tools/xemu.AppImage")
EIP_RE = re.compile(r"EIP=([0-9a-fA-F]{8})")
KERNEL_BASE = 0x80000000


def qmp_connect(path, deadline):
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(10)
            s.connect(path)
            return s
        except OSError:
            time.sleep(0.5)
    return None


def qmp_session(sock):
    f = sock.makefile("rwb")
    json.loads(f.readline().decode())  # greeting
    f.write(json.dumps({"execute": "qmp_capabilities"}).encode() + b"\n")
    f.flush()
    f.readline()

    def cmd(name, args=None):
        f.write(json.dumps({"execute": name, "arguments": args or {}}).encode() + b"\n")
        f.flush()
        while True:
            msg = json.loads(f.readline().decode())
            if "return" in msg or "error" in msg:
                return msg

    return cmd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True, help="XISO, or a directory mount if --dvd-dir")
    ap.add_argument("--dvd-dir", action="store_true", help="treat --iso as a directory to mount")
    ap.add_argument("--bios", required=True, help="directory with mcpx*.bin and a debug flash")
    ap.add_argument("--hdd", required=True, help="prepared HDD image (a fresh one idles in the kernel)")
    ap.add_argument("--out", required=True, help="scratch directory for logs, config, QMP socket")
    ap.add_argument("--seconds", type=int, default=150, help="boot window before sampling starts")
    ap.add_argument("--samples", type=int, default=10)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--xemu", default=os.environ.get("XEMU", DEFAULT_XEMU))
    args = ap.parse_args()

    if not os.path.exists(args.iso):
        sys.exit("no DVD source at %s" % args.iso)
    mcpx = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                 if f.lower().startswith("mcpx")), None)
    flash = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                  if f.lower().endswith(".bin") and "debug" in f.lower()), None)
    if not mcpx or not flash:
        sys.exit("need mcpx*.bin and a debug flash in %s" % args.bios)
    if not os.path.isfile(args.hdd):
        sys.exit("no HDD image at %s" % args.hdd)

    os.makedirs(args.out, exist_ok=True)
    toml = os.path.join(args.out, "xemu.toml")
    sock_path = os.path.join(args.out, "qmp.sock")
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    with open(toml, "w") as f:
        f.write("[general]\nshow_welcome = false\n[sys]\nmem_limit = '128'\n[sys.files]\n")
        f.write("bootrom_path = '%s'\nflashrom_path = '%s'\n" % (mcpx, flash))
        # deliberately no eeprom_path -- see the module docstring
        f.write("hdd_path = '%s'\ndvd_path = '%s'\n" % (args.hdd, args.iso))

    env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1")
    log = open(os.path.join(args.out, "xemu.log"), "wb")
    print("xemu: %s\nflash: %s\ndvd:  %s\nhdd:  %s\nout:  %s"
          % (args.xemu, flash, args.iso, args.hdd, args.out))
    proc = subprocess.Popen([args.xemu, "-config_path", toml,
                             "-serial", "file:" + os.path.join(args.out, "serial.log"),
                             "-qmp", "unix:%s,server,nowait" % sock_path],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        sock = qmp_connect(sock_path, time.time() + 60)
        if not sock:
            print("FAIL: xemu never opened its QMP socket")
            tail = open(os.path.join(args.out, "xemu.log"), errors="replace").read().splitlines()[-6:]
            for line in tail:
                print("   " + line)
            return 3
        cmd = qmp_session(sock)
        status = cmd("query-status").get("return", {})
        print("vm status: %s" % status.get("status"))
        print("waiting %ds for the title to come up" % args.seconds)
        time.sleep(args.seconds)
        eips = []
        for _ in range(args.samples):
            regs = cmd("human-monitor-command", {"command-line": "info registers"}).get("return", "")
            m = EIP_RE.search(regs)
            if m:
                eips.append(int(m.group(1), 16))
            time.sleep(args.interval)
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    if not eips:
        print("FAIL: no register samples (guest did not start)")
        return 3
    user = [e for e in eips if e < KERNEL_BASE]
    distinct = sorted(set(user))
    frac = len(user) / len(eips)
    print("eip samples: %d, in-kernel: %d, user-space: %d (%.0f%%), distinct user: %d"
          % (len(eips), len(eips) - len(user), len(user), frac * 100, len(distinct)))
    print("   addresses: %s" % ", ".join("0x%08x" % e for e in eips))
    if len(distinct) >= 3 and frac >= 0.25:
        print("PASS: the guest is executing game code")
        return 0
    print("FAIL: the CPU never left the kernel -- the machine booted but the title did not run")
    print("      check: no eeprom_path in the config, and a prepared HDD image")
    return 2


if __name__ == "__main__":
    sys.exit(main())
