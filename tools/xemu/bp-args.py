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

* **Arm late, in the image.** Arming while the CPU is in the kernel can register a `Z0` in a context the game does
  not execute, and then the breakpoint simply never fires. `--arm-after` waits for the guest to be executing game
  code (it samples `EIP` and refuses to arm outside the image's 0x00010000..0x007d9000 range).
* **The armed byte is not a witness.** `--force` arms in whatever context the CPU is in, and every arm reads its
  byte back into `armed_bytes` -- but this stub's `Z0` does not leave a `0xCC` in the bytes a read returns: measured
  2026-11-11, a control breakpoint armed at `0x000544e8` fired on that address while its read-back was still `8b`,
  the body's own byte. So an unchanged read-back proves nothing, in either direction, and the proof that an arm
  landed is a **control** breakpoint (next-but-one bullet), not the byte.
* **A stop is not always your breakpoint.** The stub reports a `T` packet for interrupts and watchpoints too, so
  each stop's `EIP` is compared with the armed set and anything else is recorded as a `not-mine` stop instead of
  being reported as a hit.
* **A per-hit timeout is not a window.** The pair this tool measures dispatches around a level load, not on a timer,
  so a measured body can be silent for minutes while the boot is still doing the thing under test. `--window` keeps
  the stop loop open for that whole span and reports each quiet `--timeout` inside it; without it the first 60 s of
  silence ends the measurement and a level that loaded 30 s later is never covered.
* **`CPL` is not the discriminator, and the image's address range is.** The stub reports `CS=0x8` (`CPL 0`) for the
  front end, the menus and the game alike, because this build's kernel is emulated in-process and runs titles at ring
  0 -- so a `--wait-user` gate on `CPL 3` waits forever (measured 2026-11-11: 88 samples over one boot, 84 of them
  inside the image at `0x00454510`, `0x00463864`, `0x000c8010`, every one of them `CPL 0`, none in ring 3).
  `--wait-user` therefore arms on the first sample whose `EIP` is **inside the image**, which is the mapping the game
  executes in; every sample is counted in the JSON's `context` block, so a window that never sees in-image context
  says so instead of leaving a breakpoint that could not fire.
* **The control is the sampled `EIP` itself.** `--control-self` also arms a breakpoint on the address the CPU was
  executing at the moment it was sampled. That arm is in the mapping the game runs in by construction, so it has to
  fire at once: a window where the control hits and the measured bodies do not is a true negative, which is the
  distinction a forced arm cannot draw.

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


# The image lives in 0x00010000..0x007d9000; anything else is the kernel (0x80000000 and up, or a low stub).
IMAGE_LO, IMAGE_HI = 0x10000, 0x7D9000


def sample(gdb):
    """One context sample: stop the guest and return its register file (the guest stays stopped)."""
    try:
        gdb.stop(3.0)
    except (IOError, OSError):
        pass
    return gdb.registers()


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
                    help="seconds to let the guest run before arming (it must be inside the image)")
    ap.add_argument("--stops", type=int, default=3, help="how many hits to record")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for one hit")
    ap.add_argument("--window", type=float, default=0.0, metavar="SECS",
                    help="keep the stop loop open this long: a quiet --timeout inside the window is survived")
    ap.add_argument("--show-strings", action="store_true", help="print the strings found on the stack")
    ap.add_argument("--force", action="store_true", help="arm even when the CPU is outside the image")
    ap.add_argument("--wait-user", type=float, default=0.0, metavar="SECS",
                    help="patience loop: sample the CPU until EIP is inside the image (ring is not usable here)")
    ap.add_argument("--sample-interval", type=float, default=0.5, metavar="SECS",
                    help="seconds between context samples in the patience loop")
    ap.add_argument("--control-self", action="store_true",
                    help="also arm the sampled in-image EIP: a control that must fire if the arm landed")
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
    regs = sample(gdb)
    # `CPL` cannot separate game code from the kernel on this build: titles and the front end both run at ring 0
    # (`CS=0x8`), so `--wait-user` is a patience loop on the **image's address range** -- keep sampling until the CPU
    # is inside the XBE, counting every sample so a window that never gets there says so instead of leaving an arm
    # that could not fire. Without it the legacy behaviour is three samples two seconds apart.
    context = {"samples": 1, "kernel": 0, "image": 0, "first_image_after": None, "cpl": {}, "eips": {}}
    started = time.time()

    def note(regs):
        key = "0x%08x" % regs["eip"]
        context["eips"][key] = context["eips"].get(key, 0) + 1
        cpl = "CPL %d" % (regs["cs"] & 3)
        context["cpl"][cpl] = context["cpl"].get(cpl, 0) + 1

    def summary():
        cpl = ", ".join("%s x%d" % (k, v) for k, v in sorted(context["cpl"].items())) or "none"
        return ("context samples: %d (%d in the image, %d in the kernel); ring: %s; distinct EIPs: %d; "
                "first in-image sample: %s" % (
                    context["samples"], context["image"], context["kernel"], cpl, len(context["eips"]),
                    "%.1fs" % context["first_image_after"]
                    if context["first_image_after"] is not None else "never"))

    while True:
        cpl = regs["cs"] & 3
        # The image lives in 0x00010000..0x007d9000; anything else is the kernel (0x80000000 and up, or a low
        # stub). Arming a `Z0` while the CPU is somewhere else can patch a mapping the game never executes in,
        # and then the breakpoint silently never fires.
        inside = IMAGE_LO <= regs["eip"] < IMAGE_HI
        note(regs)
        if inside:
            context["image"] += 1
            if context["first_image_after"] is None:
                context["first_image_after"] = round(time.time() - started, 2)
            print("context %d: EIP=0x%08x CS=0x%x CPL=%d -- inside the image, arming here"
                  % (context["samples"], regs["eip"], regs["cs"], cpl))
            break
        context["kernel"] += 1
        print("context %d: EIP=0x%08x CS=0x%x CPL=%d (outside the image)" % (context["samples"], regs["eip"],
                                                                             regs["cs"], cpl))
        if args.force:
            print("forcing the arm in this context (--force)")
            break
        if args.wait_user:
            if time.time() - started >= args.wait_user:
                print("no in-image sample in %.0fs: %s. The arm is not taken -- a breakpoint patched into this "
                      "context would never fire (see --force and its byte read-back)." % (args.wait_user, summary()))
                return 3
            gdb.resume()
            time.sleep(args.sample_interval)
            regs = sample(gdb)
            context["samples"] += 1
            continue
        if context["samples"] >= 3:
            print("refusing to arm: the CPU is not executing inside the image (EIP=0x%08x CS=0x%x). Re-run with a "
                  "larger --arm-after, --wait-user, or --force if you know the context." % (regs["eip"], regs["cs"]))
            return 3
        gdb.resume()
        time.sleep(2.0)
        regs = sample(gdb)
        context["samples"] += 1

    # The control: an address the CPU was executing at the moment it was sampled is in the game's mapping by
    # construction, so a `Z0` there must fire at once. It separates "this pair does not dispatch in this window"
    # from "this arm never landed", which the reply and even the byte read-back alone cannot.
    control = regs["eip"] if args.control_self else None
    if control is not None:
        print("control: arming the sampled EIP 0x%08x as a self-breakpoint" % control)

    armed = []
    armed_bytes = {}
    for va in breaks + ([control] if control is not None else []):
        reply = gdb.watch(va, 1, kind="exec")
        ok = reply in (b"OK", b"")
        # The stub's `OK` is not evidence that the `0xCC` landed in the mapping the game executes: `Z0` patches
        # through the *current* MMU context, and a forced arm taken while the CPU idles in the kernel writes into
        # that context. Read the byte back through the same context and keep it in the record -- `cc` is the
        # patch, anything else is an arm that would never fire, and the difference is invisible without the read.
        try:
            back = gdb.read(va, 1).hex()
        except (IOError, OSError) as exc:
            back = "error:%s" % exc
        armed_bytes[va] = back
        print("break 0x%08x: stub answered %r, byte read back %s" % (va, reply, back))
        if ok:
            armed.append(va)
    if not armed:
        print("no breakpoint could be armed")
        return 3
    gdb.resume()

    hits = []
    last_hit = None
    deadline = (time.time() + args.window) if args.window else None
    while True:
        wait = args.timeout
        if deadline is not None and deadline - time.time() < wait:
            wait = max(1.0, deadline - time.time())
        try:
            stop = gdb.wait_stop(wait)
        except (IOError, OSError) as exc:
            if deadline is not None and time.time() < deadline:
                # A quiet minute is not the end of the measurement: the pair dispatches around a level load, not on
                # a timer, so a per-hit timeout inside an open window has to be survived rather than break the run.
                print("no stop in %.0fs after %d recorded (%s); window still open, %.0fs left"
                      % (wait, len(hits), exc, deadline - time.time()))
                continue
            print("no hit within %.0fs after %d recorded (%s)" % (wait, len(hits), exc))
            break
        regs = gdb.registers()
        is_control = control is not None and regs["eip"] == control
        rec = {"stop_packet": stop.decode("ascii", "replace"), "regs": regs, "hit": regs["eip"] in armed,
               "control": is_control, "stack": [], "dumps": [], "strings": []}
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
              % (len(hits), regs["eip"], "CONTROL" if is_control else ("MINE" if rec["hit"] else "not-mine"),
                 regs["esp"], regs["eax"], regs["ecx"], regs["edx"], regs["ebx"], regs["esi"], regs["edi"],
                 regs["ebp"]))
        print("   stack: " + " ".join(rec["stack"][:12]))
        for d in rec["dumps"]:
            print("   dump %s @0x%08x: %s" % (d["expr"], d["addr"], " ".join(d.get("words", [])) or d.get("error")))
        if rec["strings"] and args.show_strings:
            print("   strings: " + "; ".join("%s" % s["text"] for s in rec["strings"][:6]))
        elif rec["strings"]:
            print("   strings: %d on the stack (%d shown with --show-strings)"
                  % (len(rec["strings"]), 0))
        hits.append(rec)
        if is_control:
            # The control has done its job: one firing proves the `0xCC` is in the mapping the game executes in.
            # Disarm it and step past it so it cannot eat the stop budget or spin on its own `0xCC`.
            try:
                gdb.unwatch(control, 1, kind="exec")
                if control in armed:
                    armed.remove(control)
                step(gdb)
            except (IOError, OSError) as exc:
                print("   (control step failed: %s)" % exc)
            gdb.resume()
            continue
        if len([h for h in hits if h["hit"] and not h.get("control")]) >= args.stops:
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
            json.dump({"breaks": ["0x%08x" % b for b in breaks],
                       "control": "0x%08x" % control if control is not None else None,
                       "context": context,
                       "armed_bytes": {"0x%08x" % k: v for k, v in armed_bytes.items()},
                       "stops": hits}, fh, indent=1)
        print("wrote %s" % args.json)
    measured = [h for h in hits if h["hit"] and not h.get("control")]
    controls = [h for h in hits if h.get("control")]
    print(summary())
    print("%d stop(s), %d at an armed body, %d at the control" % (len(hits), len(measured), len(controls)))
    if control is not None and not controls:
        print("the control at 0x%08x never fired: this window proves nothing about the armed bodies" % control)
        return 2
    gdb.close()
    return 0 if measured else 1


if __name__ == "__main__":
    sys.exit(main())
