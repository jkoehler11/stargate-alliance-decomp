#!/usr/bin/env python3
"""Decide whether the guest is *running the game*, not just awake (PLAN 7.2).

`smoke.py` asks one question — did the CPU leave kernel space — and a kernel at `EIP=0x8001b03x` with `CPL=0` cannot
distinguish three states that look identical from the outside:

  1. the disc never mounted, so the title was never loaded;
  2. the title's image was loaded and is resident in RAM, but the CPU is somewhere else;
  3. the title is executing.

This tool separates them with two measurements: the CPU's `CPL` and `EIP` over a window (from QMP), and whether the
XBE's own title string is present in guest physical memory (over the GDB stub, `tools/xemu/mem.py`). A hit proves the
image reached RAM; `CPL=3` with `EIP` inside the image's own range proves it is running.

    python3 tools/xemu/bootcheck.py --qmp /tmp/x/qmp.sock --gdb-port 12345 \\
        --xbe ~/nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe

Exit codes: 0 = executing, 1 = image resident only, 2 = no image in RAM, 3 = harness could not sample.
"""
import argparse
import json
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mem import Gdb, find_text  # noqa: E402

TITLE = "Stargate SG-1"      # UTF-16, in the certificate the header points at
REGISTER_RE = __import__("re").compile(r"EIP=([0-9a-fA-F]{8}).*CPL=(\d)")


def qmp(path, deadline=30.0):
    end = time.time() + deadline
    while time.time() < end:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(path)
            break
        except OSError:
            time.sleep(1)
    else:
        raise IOError("no QMP socket at %s" % path)
    fh = sock.makefile("rwb")
    fh.readline()

    def cmd(name, args=None):
        msg = {"execute": name}
        if args:
            msg["arguments"] = args
        fh.write(json.dumps(msg).encode() + b"\n")
        fh.flush()
        while True:
            reply = json.loads(fh.readline().decode())
            if "return" in reply or "error" in reply:
                return reply
    cmd("qmp_capabilities")
    return cmd


def samples(cmd, window, interval):
    out = []
    end = time.time() + window
    while True:
        regs = cmd("human-monitor-command", {"command-line": "info registers"}).get("return", "")
        for line in regs.splitlines():
            m = REGISTER_RE.search(line)
            if m:
                out.append((int(m.group(1), 16), int(m.group(2))))
                break
        if time.time() >= end:
            return out
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--qmp", required=True, help="xemu's -qmp socket path")
    ap.add_argument("--gdb-port", type=int, default=12345, help="xemu's -gdb tcp::PORT")
    ap.add_argument("--xbe", required=True, help="the dump's default.xbe (title string + base address)")
    ap.add_argument("--window", type=float, default=20.0, help="how long to sample the CPU")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--region", default="0x10000:0x1000000", help="physical region to search for the image")
    args = ap.parse_args()

    image = open(args.xbe, "rb").read(4096)
    if TITLE.encode("utf-16-le") not in image:
        sys.exit("no %r in the first 4 KiB of %s: wrong XBE?" % (TITLE, args.xbe))
    base = struct.unpack_from("<I", image, 0x104)[0]
    print("image: base %#x, title string present in the certificate" % base)

    cmd = qmp(args.qmp)
    got = samples(cmd, args.window, args.interval)
    if not got:
        print("FAIL: could not read EIP")
        return 3
    kernel = [s for s in got if s[1] == 0 or s[0] >= 0x80000000]
    print("cpu: %d samples, %d in kernel (CPL=0/system), eip range %#x..%#x"
          % (len(got), len(kernel), min(s[0] for s in got), max(s[0] for s in got)))

    addr, size = (int(x, 0) for x in args.region.split(":"))
    hits = []
    try:
        gdb = Gdb("127.0.0.1", args.gdb_port)
        try:
            gdb.interrupt()
            if not gdb.phys_mode():
                sys.exit("the stub refused Qqemu.PhyMemMode:1")
            blob = gdb.read(addr, size)
        finally:
            gdb.close()
        hits = [addr + off for off in find_text(blob, TITLE)]
    except (OSError, IOError) as exc:
        print("FAIL: %s" % exc)
        return 3

    print("memory: %d hit(s) of the title string in %#x..%#x%s"
          % (len(hits), addr, addr + size, "" if not hits else ": " + ", ".join("%#x" % h for h in hits[:4])))

    running = [s for s in got if s[1] != 0 and any(abs(s[0] - h) < 0x800000 for h in hits)]
    if running:
        print("VERDICT: the title is executing (%d sample(s) at CPL=3 inside the image)" % len(running))
        return 0
    if hits:
        print("VERDICT: the image is resident but the CPU is not in it — loaded, not running")
        return 1
    print("VERDICT: no image in RAM — the disc did not mount (see docs/XEMU.md's dvd_path pitfall)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
