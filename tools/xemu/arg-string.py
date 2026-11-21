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
    """Decode a byte blob as a C string, narrow or UTF-16LE, and keep whichever reads as text.

    The first version chose on an odd-position-NUL count and mis-decoded a plain ASCII path into mojibake
    (`Z:\\chevguy4.tm` came back as CJK), because a short buffer never reaches the threshold. Score both
    candidates by printable ratio instead: ASCII paths and wide strings then each win on their own merits.
    """
    narrow = blob.split(b"\x00", 1)[0].decode("latin-1", "replace").rstrip("\x00")
    wide_bytes = blob[:160]
    if len(wide_bytes) % 2:
        wide_bytes = wide_bytes[:-1]
    try:
        # Decode everything, then cut at the first NUL *character*: splitting the bytes on a NUL pair straddles the
        # boundary and eats the last character (`...Game.int` came back as `...Game.in`).
        wide = wide_bytes.decode("utf-16-le", "replace").split("\x00", 1)[0]
    except Exception:
        wide = ""

    def score(text):
        # Rank by how many characters survive as text first, then by the ratio: a ratio alone ties a one-character
        # narrow read against a full wide string, and the short one wins the tie.
        if not text:
            return (0, 0.0)
        # ASCII only: CJK ideographs are `isalnum()`, so an ASCII path wrongly decoded as UTF-16 scored well enough
        # to win (`D:\splash.raw` came back as `㩄獜汰獡⹨慲w`).
        good = sum(1 for ch in text if ch.isprintable() and ch.isascii())
        return (good, good / len(text))

    return wide if score(wide) > score(narrow) else narrow


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=12345)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--break", dest="breaks", action="append", required=True, metavar="ADDR",
                    help="address to break at (0x...), may be repeated")
    ap.add_argument("--arg", type=int, default=1, help="1-based stack argument holding the string pointer (default 1)")
    ap.add_argument("--arg2", type=int, default=0,
                    help="a second 1-based stack argument to record as a number (e.g. 2 = dwDesiredAccess)")
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
            # A quiet minute is not the end of the window: a load can sit silent between two opens, and breaking here
            # turned "nothing opened yet" into "nothing opened" (measured: a 60 s timeout ended a 260 s window early).
            if time.time() < deadline:
                print("no stop in %.0fs (%d recorded); window still open, %.0fs left"
                      % (wait, len(records), deadline - time.time()))
                continue
            print("no stop in %.0fs after %d recorded (%s)" % (wait, len(records), exc))
            break
        if stop is None:
            print("no stop in %.0fs (%d recorded so far)" % (wait, len(records)))
            continue
        regs = gdb.registers()
        eip = regs.get("eip")
        esp = regs.get("esp")
        rec = {"n": len(records) + 1, "eip": eip, "esp": esp, "text": None, "ptr": None, "ret": None,
               "arg2": None}
        if args.arg2 and esp:
            try:
                rec["arg2"] = int.from_bytes(gdb.read(esp + 4 * args.arg2, 4), "little")
            except (IOError, OSError):
                pass
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
        print("stop %d: eip=0x%08x ret=%s ptr=%s%s -> %r"
              % (rec["n"], eip or 0,
                 ("0x%08x" % rec["ret"]) if rec["ret"] else "?",
                 ("0x%08x" % rec["ptr"]) if rec["ptr"] else "?",
                 (" arg2=0x%08x" % rec["arg2"]) if rec["arg2"] is not None else "",
                 rec["text"]))
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
