#!/usr/bin/env python3
"""A virtual Xbox pad for the harness: the menus take one step only from the d-pad (U14).

The map list's step is `XC_PadUp`/`XC_PadDown`, and the keyboard cannot produce one: `pgdn` moves ten entries,
`down` moves nothing (measured, docs/XEMU.md). xemu reads a controller through SDL, and SDL reads an evdev
gamepad with no window focus -- so a pad created here also works under ssh and on a desktop somebody is using,
where an XTEST key would either need the focus or type into their application.

The identity is deliberately **not** a real one. The first version of this file claimed to be an Xbox 360 pad
(045e:028e:0114), and that is a trap on this desktop: SDL gives that identity the GUID
`030081b85e0400008e02000014010000`, which is the GUID of the *physical* pad plugged into the machine, and
xemu's saved settings bind port 1 to that GUID. A fake pad wearing it exists and is mapped, and every scripted
press still moves nothing -- measured, with the pad created and SDL listing two devices under that one GUID.
This pad claims 1209:0001 (pid.codes vendor, product unused) instead, so its GUID
`030015c6091200000100000001000000` is its own; the harness binds port 1 to that GUID in the run's TOML.
Both the GUID and the mapping below were read back from SDL (`SDL_GetJoystickGUIDForID`,
`SDL_GetGamepadMappingForID`) rather than computed here: an identity, a GUID and a mapping that disagree look
exactly like a pad nobody is pressing.

Library use, from the harness -- create the pad *before* xemu starts, so SDL enumerates it at init:

    pad = Pad(); pad.tap("a", hold=0.15); pad.down("down"); pad.up("down"); pad.close()

Standalone, for a hand-driven run under `play.sh`, commands on stdin:

    python3 tools/xemu/vpad.py --serve     # tap a | down down | up down | quit
    python3 tools/xemu/vpad.py --tap down --hold 0.2
"""
import argparse
import sys
import time

try:
    from evdev import AbsInfo, UInput, ecodes as e
except ImportError:  # the container has no evdev; only the desktop creates a pad
    UInput = AbsInfo = None
    e = None

NAME = "SG1 virtual pad"
VENDOR, PRODUCT, VERSION, BUS = 0x1209, 0x0001, 0x0001, 0x0003
# SDL indexes buttons in the order the device declares them, so BTN_A..BTN_THUMBR are b0..b10 with no gaps
# (no C, no Z, no TL2/TR2 here). The d-pad is the two hats, not BTN_DPAD_*: SDL maps whichever the device has,
# and a real pad has hats.
BUTTONS = {"a": 0x130, "b": 0x131, "x": 0x133, "y": 0x134, "white": 0x136, "black": 0x137,
           "back": 0x13A, "start": 0x13B, "guide": 0x13C, "lstick": 0x13D, "rstick": 0x13E}
HATS = {"up": (0x11, -1), "down": (0x11, 1), "left": (0x10, -1), "right": (0x10, 1)}  # (code, ABS_HAT value)

GUID = "030015c6091200000100000001000000"
# Measured off the live device (`SDL_GetGamepadMappingForID`), then pinned: the axes are a0..a3 in the order
# declared above, and the hats are h0.1/h0.2/h0.4/h0.8 for up/right/down/left.
SDL_MAPPING = (GUID + ",SG1 virtual pad,a:b0,b:b1,x:b2,y:b3,leftshoulder:b4,rightshoulder:b5,"
               "back:b6,start:b7,guide:b8,leftstick:b9,rightstick:b10,"
               "dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,leftx:a0,lefty:a1,rightx:a2,righty:a3")


class Pad:
    """One uinput gamepad. `settle` is how long udev gets to make it visible before xemu looks for it."""

    def __init__(self, settle=1.5, verbose=True):
        if UInput is None:
            raise SystemExit("python-evdev is not installed here (it is on the desktop)")
        events = {e.EV_KEY: sorted(BUTTONS.values()),
                  e.EV_ABS: [(e.ABS_X, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                             (e.ABS_Y, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                             (e.ABS_RX, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                             (e.ABS_RY, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                             (e.ABS_HAT0X, AbsInfo(0, -1, 1, 0, 0, 0)),
                             (e.ABS_HAT0Y, AbsInfo(0, -1, 1, 0, 0, 0))]}
        self.ui = UInput(events, name=NAME, vendor=VENDOR, product=PRODUCT, version=VERSION, bustype=BUS)
        self.verbose = verbose
        if verbose:
            print("vpad: %s %04x:%04x v%04x on %s" % (NAME, VENDOR, PRODUCT, VERSION, self.ui.device.path),
                  flush=True)
        time.sleep(settle)

    def down(self, name):
        """Hold one button. Hats are absolute, so `down("down")` then `up("down")` is one d-pad click."""
        if name in HATS:
            code, value = HATS[name]
            self.ui.write(e.EV_ABS, code, value)
        elif name in BUTTONS:
            self.ui.write(e.EV_KEY, BUTTONS[name], 1)
        else:
            raise SystemExit("unknown pad button '%s' (known: %s)"
                             % (name, ", ".join(sorted(list(BUTTONS) + list(HATS)))))
        self.ui.syn()
        return "%s down" % name

    def up(self, name):
        if name in HATS:
            self.ui.write(e.EV_ABS, HATS[name][0], 0)
        elif name in BUTTONS:
            self.ui.write(e.EV_KEY, BUTTONS[name], 0)
        else:
            raise SystemExit("unknown pad button '%s'" % name)
        self.ui.syn()
        return "%s up" % name

    def tap(self, name, hold=0.15):
        """Press and release: the guest ignores a hat left at 0 for less than a frame, so the hold is not zero."""
        self.down(name)
        time.sleep(hold)
        self.up(name)
        return "%s tap (%.2fs)" % (name, hold)

    def close(self):
        try:
            self.ui.close()
        except Exception:
            pass


def serve(pad, stream):
    """Commands on stdin, one per line: `tap a`, `down down`, `up down`, `hold a 0.5`, `quit`."""
    for line in stream:
        parts = line.split()
        if not parts:
            continue
        verb, rest = parts[0], parts[1:]
        try:
            if verb == "quit":
                break
            if verb == "tap" and rest:
                print(pad.tap(rest[0], float(rest[1]) if len(rest) > 1 else 0.15), flush=True)
            elif verb == "hold" and len(rest) > 1:
                print(pad.tap(rest[0], float(rest[1])), flush=True)
            elif verb == "down" and rest:
                print(pad.down(rest[0]), flush=True)
            elif verb == "up" and rest:
                print(pad.up(rest[0]), flush=True)
            else:
                print("commands: tap|hold <button> [secs], down <button>, up <button>, quit", flush=True)
        except SystemExit as exc:
            print(exc, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tap", metavar="BUTTON", help="create the pad, tap one button, exit")
    ap.add_argument("--hold", type=float, default=0.15, help="seconds to hold it (default 0.15)")
    ap.add_argument("--serve", action="store_true", help="read commands on stdin until `quit`")
    ap.add_argument("--settle", type=float, default=1.5, help="seconds udev gets before xemu looks (default 1.5)")
    args = ap.parse_args()
    pad = Pad(settle=args.settle)
    try:
        if args.tap:
            print(pad.tap(args.tap, args.hold))
        elif args.serve:
            serve(pad, sys.stdin)
        else:
            print("pad is up; ctrl-c to remove it")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        pad.close()


if __name__ == "__main__":
    main()
