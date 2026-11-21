#!/usr/bin/env python3
"""Trace the string an import wrapper is handed: break at an address, read one argument as a C string.

The file-open question this answers is "which files does the running build ask for, in what order". `_CreateFileA@28`
(`0x0033e2cd`) takes the name as argument 1, so arming there and dereferencing the first stack slot after the return
address turns a boot into a file-access list -- the one thing a static scan cannot produce, because the name may be
composed at run time and the open may never happen.

    tools/xemu/arg-string.py --port 12345 --break 0x0033e2cd --arg 1 --stops 40 --window 240

Attach after the guest is already running (`drive.py --gdb`), the same way `bp-args.py` does: arming while the CPU is in
the kernel can patch a mapping the game never executes in, so this arms late and reads the byte back.

Output is one line per stop (`stop N: name -> 'text'`), and `--json` writes the same as records.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mem import Gdb  # noqa: E402


def step(gdb, timeout=10.0):
    """Single-step the guest: the only way past a `Z0` stop on this stub.

    The packet is sent raw with its checksum, exactly as `bp-args.py` does. Going through the generic helper here is
    what made the step time out and left the guest re-reporting one call for the whole window.
    """
    gdb.sock.sendall(b"$s#73")
    return gdb.wait_stop(timeout)


def cstring(blob):
    """Decode a byte blob as ASCII up to the first NUL, or as UTF-16LE if it looks wide."""
    cut = blob.split(b"\x00\x00", 1)[0] if blob[:2] != b"" else blob
    wide = blob[:120].split(b"\x00\x00\x00", 1)[0]
    # A wide string has NULs in every odd position; a narrow one does not.
    odd_nuls = sum(1 for i in range(1, min(len(blob), 64), 2) if blob[i] == 0)
    if odd_nuls >= min(20, len(blob) // 4) and len(wide) >= len(cut):
        try:
            return wide.decode("utf-16-le", "replace").rstrip("\x00")
        except Exception:
            pass
    return cut.decode("latin-1", "replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=12345)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--break", dest="breaks", action="append", required=True, metavar="ADDR",
                    help="address to break at (0x...), may be repeated")
    ap.add_argument("--arg", type=int, default=1, help="1-based stack argument holding the string pointer (default 1)")
    ap.add_argument("--width", type=int, default=0x100, help="bytes to read at that pointer (default 0x100)")
    ap.add_argument("--stops", type=int, default=24)
    ap.add_argument("--window", type=float, default=300.0, help="seconds to keep the window open")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for one stop")
    ap.add_argument("--wait-user", type=float, default=120.0,
                    help="seconds to wait for a user-mode sample before arming (default 120)")
    ap.add_argument("--json", dest="json_path")
    args = ap.parse_args()

    addrs = [int(b, 16) for b in args.breaks]
    gdb = Gdb(args.host, args.port)
    try:
        gdb.stop()
    except (IOError, OSError):
        pass

    # Arm only from a user-mode sample. `Z0` patches through the *current* MMU context, so an arm taken while the CPU
    # idles in the kernel writes the `0xCC` into a mapping the game never executes -- the stub still answers `OK`, and
    # the readback (`0x55`, the function's own `push ebp`) is the only sign the breakpoint would never fire.
    if args.wait_user:
        deadline = time.time() + args.wait_user
        landed = False
        while time.time() < deadline:
            try:
                regs = gdb.registers()
            except (IOError, OSError) as exc:
                print("register read failed: %s" % exc)
                break
            eip = regs.get("eip") or 0
            if eip and eip < 0x80000000:
                print("user-mode sample 0x%08x after %.1fs" % (eip, args.wait_user - (deadline - time.time())))
                landed = True
                break
            gdb.resume()
            time.sleep(0.15)
            try:
                gdb.stop()
            except (IOError, OSError):
                pass
        if not landed:
            print("no user-mode sample in %.0fs; arming anyway" % args.wait_user)

    armed = {}
    ok_any = False
    for va in addrs:
        reply = gdb.watch(va, 1, kind="exec")
        try:
            back = gdb.read(va, 1).hex()
        except (IOError, OSError) as exc:
            back = "error:%s" % exc
        # The stub's `Z0` does not leave a `0xCC` in the bytes a read returns (see bp-args.py), so the reply is the
        # gate and the readback is only a note. Gating on `cc` refuses arms that are fine.
        ok = reply in (b"OK", b"")
        armed[va] = back
        ok_any = ok_any or ok
        print("break 0x%08x: stub answered %r, byte read back %s%s"
              % (va, reply, back, "" if ok else "  (not armed)"))
    if not ok_any:
        print("no breakpoint could be armed")
        return 3

    gdb.resume()
    records = []
    last_eip = None
    deadline = time.time() + args.window
    while len(records) < args.stops and time.time() < deadline:
        wait = min(args.timeout, max(1.0, deadline - time.time()))
        try:
            stop = gdb.wait_stop(wait)
        except (IOError, OSError) as exc:
            print("no stop in %.0fs: %s" % (wait, exc))
            break
        if stop is None:
            print("no stop in %.0fs (%d recorded so far)" % (wait, len(records)))
            continue
        regs = gdb.registers()
        eip = regs.get("eip")
        esp = regs.get("esp")
        rec = {"n": len(records) + 1, "eip": eip, "esp": esp, "text": None, "ptr": None, "ret": None}
        try:
            rec["ret"] = int.from_bytes(gdb.read(esp, 4), "little") if esp else None
        except (IOError, OSError):
            pass
        if eip in armed and esp:
            try:
                slot = gdb.read(esp + 4 * args.arg, 4)
                ptr = int.from_bytes(slot, "little")
                rec["ptr"] = ptr
                # The caller often builds the path on its own stack (measured: `D:\System\Starg...` at 0xd0166d4c),
                # so the stack range is a legitimate pointer target and an `addr < 0x80000000` test rejects it.
                if 0x10000 <= ptr < 0x80000000 or 0xC0000000 <= ptr < 0xD0400000:
                    rec["text"] = cstring(gdb.read(ptr, args.width))
            except (IOError, OSError) as exc:
                rec["error"] = str(exc)
        records.append(rec)
        print("stop %d: eip=0x%08x ret=%s ptr=%s -> %r"
              % (rec["n"], eip or 0,
                 ("0x%08x" % rec["ret"]) if rec["ret"] else "?",
                 ("0x%08x" % rec["ptr"]) if rec["ptr"] else "?", rec["text"]))
        # The stub re-reports the same EIP on a plain continue: step over, then re-arm.
        if eip == last_eip:
            try:
                gdb.unwatch(eip, 1, kind="exec")
                step(gdb)
            except (IOError, OSError) as exc:
                print("   (step over 0x%08x failed: %s)" % (eip or 0, exc))
            finally:
                gdb.watch(eip, 1, kind="exec")
        last_eip = eip
        gdb.resume()

    for va in armed:
        try:
            gdb.unwatch(va, 1, kind="exec")
        except (IOError, OSError):
            pass
    gdb.close()

    if args.json_path:
        with open(args.json_path, "w") as handle:
            json.dump({"break": ["0x%08x" % a for a in addrs], "arg": args.arg, "records": records}, handle, indent=1)
        print("wrote %s" % args.json_path)
    named = [r for r in records if r["text"]]
    print("%d stop(s), %d with a string" % (len(records), len(named)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
