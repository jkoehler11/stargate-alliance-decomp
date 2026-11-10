#!/usr/bin/env python3
"""Break on an address in a running xemu and read the arguments it was called with (PLAN 2.6 / U11).

The static half of U11 located the D3D8 device-creation path but cannot answer what it passes to
`CreateDevice`: those are arguments on the stack, built into a struct the render device owns. This tool is the
runtime half — it arms `Z0` execution breakpoints on addresses taken from `symbols/xbox.map`, lets the guest reach
them, and records for each stop the register file, the words on the stack (the call's arguments on an x86 cdecl /
stdcall frame), the printable string at any stack word that lands in a mapped section, and any memory block the
caller asked for.

    python3 tools/xemu/bp-args.py --port 12345 --arm-after 140 \\
        --break 0x2b2d48 --break 0x2b2d78 --dump 'esi+0x4708:0x40' --stops 4 --json $WORK/bp.json

Two things learned from the stub, both of which read as a broken tool otherwise:

* **Arm late, in user context.** `Z0` patches the guest image through the *current* MMU context; arming while the
  CPU is in the kernel can write the `0xCC` into a different mapping than the one the game executes in, and then
  the breakpoint simply never fires. `--arm-after` waits for the guest to be executing game code (it samples
  `EIP`/`CS` and refuses to arm while `CPL` is 0).
* **A stop is not always your breakpoint.** The stub reports a `T` packet for interrupts and watchpoints too, so
  each stop's `EIP` is compared with the armed set and anything else is recorded as a `not-mine` stop instead of
  being reported as a hit.

The stack words are read in the **virtual** address space (the guest's own), never in physical mode: `ESP` is a
virtual address, and a stub left in `Qqemu.PhyMemMode:1` by an earlier client answers with the wrong bytes.
Nothing here prints guest strings unless `--show-strings` is given; the JSON holds addresses and numbers.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mem import Gdb  # noqa: E402

ASCII = re.compile(rb"[\x20-\x7e]{4,}")


def parse_region(text):
    """`ADDR:SIZE` or `REG+OFF:SIZE` -> (base or None, register, offset, size)."""
    where, size = text.split(":")
    size = int(size, 0)
    if "+" in where and not where.lower().startswith("0x"):
        reg, off = where.split("+", 1)
        return None, reg.strip(), int(off, 0), size
    if where.lower() in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "eip"):
        return None, where.lower(), 0, size
    return int(where, 0), None, 0, size


def read_strings(gdb, blob, base):
    out = []
    for m in ASCII.finditer(blob):
        out.append({"addr": base + m.start(), "text": m.group().decode("ascii", "replace")})
    return out


def step(gdb, timeout=10.0):
    """Single-step the guest (`s`): the only way past a `Z0` stop on this stub (see the loop in `main`)."""
    gdb.sock.sendall(b"$s#73")
    return gdb.wait_stop(timeout)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=12345)
    ap.add_argument("--break", dest="breaks", action="append", default=[], metavar="VA",
                    help="execution breakpoint (repeatable)")
    ap.add_argument("--dump", action="append", default=[], metavar="ADDR:SIZE",
                    help="memory to read at every stop (`0x4708:0x40` or `esi+0x4708:0x40`)")
    ap.add_argument("--stack", type=lambda v: int(v, 0), default=0x40, help="stack bytes above ESP to record")
    ap.add_argument("--arm-after", type=float, default=0.0,
                    help="seconds to let the guest run before arming (it must be in user context)")
    ap.add_argument("--stops", type=int, default=3, help="how many hits to record")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for one hit")
    ap.add_argument("--show-strings", action="store_true", help="print the strings found on the stack")
    ap.add_argument("--force", action="store_true", help="arm even when the CPU is outside the image")
    ap.add_argument("--json", help="write the stops here (addresses and numbers only)")
    args = ap.parse_args()
    global print
    import functools
    print = functools.partial(print, flush=True)

    breaks = [int(b, 0) for b in args.breaks]
    dumps = [parse_region(d) for d in args.dump]
    gdb = Gdb(args.host, args.port)
    try:
        gdb.phys_mode(False)
    except IOError:
        pass

    if args.arm_after:
        time.sleep(args.arm_after)
    for attempt in range(3):
        try:
            gdb.stop(3.0)
        except (IOError, OSError):
            pass
        regs = gdb.registers()
        print("context %d: EIP=0x%08x CS=0x%x CPL=%d" % (attempt, regs["eip"], regs["cs"], regs["cs"] & 3))
        # The image lives in 0x00010000..0x007d9000; anything else is the kernel (0x80000000 and up, or a low
        # stub). Arming a `Z0` while the CPU is somewhere else can patch a mapping the game never executes in,
        # and then the breakpoint silently never fires.
        if 0x10000 <= regs["eip"] < 0x7d9000 or args.force:
            break
        gdb.resume()
        time.sleep(2.0)
    else:
        print("refusing to arm: the CPU is not executing inside the image (EIP=0x%08x CS=0x%x). Re-run with a "
              "larger --arm-after, or --force if you know the context." % (regs["eip"], regs["cs"]))
        return 3

    armed = []
    for va in breaks:
        reply = gdb.watch(va, 1, kind="exec")
        ok = reply in (b"OK", b"")
        print("break 0x%08x: stub answered %r" % (va, reply))
        if ok:
            armed.append(va)
    if not armed:
        print("no breakpoint could be armed")
        return 3
    gdb.resume()

    hits = []
    last_hit = None
    for n in range(args.stops):
        try:
            stop = gdb.wait_stop(args.timeout)
        except (IOError, OSError) as exc:
            print("no hit within %.0fs after %d recorded (%s)" % (args.timeout, len(hits), exc))
            break
        regs = gdb.registers()
        rec = {"stop_packet": stop.decode("ascii", "replace"), "regs": regs, "hit": regs["eip"] in armed,
               "stack": [], "dumps": [], "strings": []}
        try:
            raw = gdb.read(regs["esp"], args.stack)
            rec["stack"] = ["0x%08x" % int.from_bytes(raw[i:i + 4], "little")
                            for i in range(0, len(raw) - 3, 4)]
        except IOError as exc:
            rec["stack_error"] = str(exc)
        for m in dumps:
            base, reg, off, size = m
            addr = base if reg is None else regs[reg] + off
            try:
                blob = gdb.read(addr, size)
                rec["dumps"].append({"expr": "%s+0x%x" % (reg, off) if reg else "0x%x" % base,
                                     "addr": addr, "words": ["0x%08x" % int.from_bytes(blob[i:i + 4], "little")
                                                             for i in range(0, len(blob) - 3, 4)]})
            except IOError as exc:
                rec["dumps"].append({"expr": m, "addr": addr, "error": str(exc)})
        # Any ASCII on the stack is usually the format string of the call being made.
        for word in rec["stack"][:12]:
            try:
                addr = int(word, 16)
                blob = gdb.read(addr, 64)
            except (ValueError, IOError):
                continue
            for s in read_strings(gdb, blob, addr):
                if s["addr"] == addr and s["text"].isprintable():
                    rec["strings"].append(s)
        print("stop %d: EIP=0x%08x %s esp=0x%08x eax=0x%08x ecx=0x%08x edx=0x%08x ebx=0x%08x esi=0x%08x edi=0x%08x ebp=0x%08x"
              % (n, regs["eip"], "MINE" if rec["hit"] else "not-mine", regs["esp"], regs["eax"], regs["ecx"],
                 regs["edx"], regs["ebx"], regs["esi"], regs["edi"], regs["ebp"]))
        print("   stack: " + " ".join(rec["stack"][:12]))
        for d in rec["dumps"]:
            print("   dump %s @0x%08x: %s" % (d["expr"], d["addr"], " ".join(d.get("words", [])) or d.get("error")))
        if rec["strings"] and args.show_strings:
            print("   strings: " + "; ".join("%s" % s["text"] for s in rec["strings"][:6]))
        elif rec["strings"]:
            print("   strings: %d on the stack (%d shown with --show-strings)"
                  % (len(rec["strings"]), 0))
        hits.append(rec)
        if rec["hit"] and len([h for h in hits if h["hit"]]) >= args.stops:
            break
        # Step over the breakpoint before continuing: this stub re-reports a `Z0` stop at the same `EIP` on a
        # plain `c`, so eight "hits" in a row can all be one instruction that never retired. Removing the
        # breakpoint, single-stepping, re-arming and resuming is what makes the guest actually advance.
        if rec["hit"] and rec["regs"]["eip"] == last_hit:
            va = rec["regs"]["eip"]
            try:
                gdb.unwatch(va, 1, kind="exec")
                step(gdb)
            except (IOError, OSError) as exc:
                print("   (step over 0x%08x failed: %s)" % (va, exc))
            gdb.watch(va, 1, kind="exec")
        last_hit = rec["regs"]["eip"] if rec["hit"] else last_hit
        gdb.resume()

    for va in armed:
        try:
            gdb.unwatch(va, 1, kind="exec")
        except (IOError, OSError):
            pass
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"breaks": ["0x%08x" % b for b in breaks], "stops": hits}, fh, indent=1)
        print("wrote %s" % args.json)
    print("%d stop(s), %d at an armed address" % (len(hits), len([h for h in hits if h["hit"]])))
    gdb.close()
    return 0 if any(h["hit"] for h in hits) else 1


if __name__ == "__main__":
    sys.exit(main())
