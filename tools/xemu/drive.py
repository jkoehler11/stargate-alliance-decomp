#!/usr/bin/env python3
"""Drive the game with a scripted input sequence and assert on each step (PLAN 7.2).

The boot smoke test proves the guest executes game code by sampling `EIP`. It cannot tell whether the *display*
is alive or whether input reaches the guest, which is what a gameplay-level regression needs. xemu has no
`screendump` in its QMP, so this drives the X window it opens instead: capture, send input, capture again, compare.

One keystroke (`--key`) is the original single-step form. Mission loading needs a *sequence* of inputs with an
assertion per step, which is `--step`:

    DISPLAY=:0 XAUTHORITY=/run/user/1000/xauth_XXXX python3 tools/xemu/drive.py \\
        --iso=$WORK/sg1.iso --bios=$SG1_DUMP/bios --hdd=$WORK/hdd.qcow2 --out=$WORK/drive \\
        --step=shot=00-title --step=key=Return --step=wait=6 --step=shot=01-return

Steps, in order:

    shot=NAME   capture NAME.png; compared with the previous shot, >= --threshold differing pixels asserts
    key=KEY     `xdotool key`, targeted at the window (the form the first measured repaint used)
    xkey=KEY    the same, through XTEST to the focused window (some SDL builds ignore targeted events)
    type=TEXT   `xdotool type`, targeted at the window
    xtype=TEXT  the same, through XTEST
    wait=SECS   sleep
    activate    focus the window first (`windowactivate --sync`)
    raise       bring the emulator window to the top *without* taking the focus, so whoever is using the desktop can
                click it and hand the focus over on their own terms
    focus=SECS  wait up to SECS for the emulator to own X input focus, and refuse the sequence if it never does: on a
                desktop somebody is using, the polite form is to wait for them to look at the emulator, never to steal it
    gkey=KEY     press a key in the *guest*, over QMP `sendkey`: it needs the emulated USB keyboard attached (see
                 docs/XEMU.md) and, unlike `key=`/`xkey=`, it needs no window focus and no X at all
    pad=BUTTON   press a button on the virtual pad (`tools/xemu/vpad.py`, created before xemu starts): `a`/`b`/
                 `x`/`y`, `up`/`down`/`left`/`right` (the d-pad), `start`, `back`, `white`, `black`. The map list's
                 single step is the d-pad and the guest keyboard cannot produce one (docs/XEMU.md), so this is the
                 only step that reaches it; it needs no focus, no X and no USB keyboard
    monitor=CMD  run one QMP human-monitor-command (e.g. `device_add usb-kbd,port=1.3,id=kbd`, which xemu documents
                as the way to give the guest a keyboard); the reply is printed
    cpu=COUNT   sample the guest's EIP COUNT times over QMP: pixels cannot tell a key that was ignored from a guest
                that stopped executing, so every sequence that reads "no change" needs this
    noise=SECS  capture, wait SECS with no input, capture again: the screen's own animation, in pixels
    watch=TEXT  dump all of guest RAM inside the boot, find TEXT (UTF-16LE with its terminator, the form a script
                `FString` holds) in it, translate its *physical* address to the *linear* one the CPU uses (the page
                tables are in the same dump, CR3 from `info registers`, `tools/xemu/vamap.py`), then arm a *read*
                watchpoint over the string's first `--watch-length` bytes and print each stop's register file, the
                stack words around ESP that name the address, and EIP. It needs `--gdb`; the dump is written to
                `--out` (delete it: 128 MiB).

`key=` and `type=` take target-window input, `xkey=`/`xtype=` take XTEST input; which reaches the guest is a
measured fact per input type, not a safe assumption (docs/XEMU.md carries the numbers). SDL ignores synthetic
`XSendEvent` keys, so a targeted `key=` that "repaints" may be the background animating rather than input
arriving: run a `noise=` step first and give `--crop` the region the input is supposed to change.

**Every asserting capture is taken twice, 1.5 s apart (`--regrab`).** The window's pixmap lags a keypress:
measured, a grab taken immediately after `sendkey ret` on the title is *byte-identical* to the pre-key frame while the
next grab differs by 18,790 pixels, so the first grab reports "no change" for a key that worked -- which is how the
map list looked inert for two sessions. Two identical keys sent less than ~2 s apart are also coalesced by the guest
(a second `pgdn` moved 19 pixels where the first moved 14,203), so a sequence spaces its keys.

It needs `xdotool` and ImageMagick's `import`/`compare`, and an X display: over plain ssh xemu cannot open one at
all (docs/REGRESSION.md). Exit codes: 0 every shot step repainted, 2 a step did not change the screen (the input
never reached the guest, or the screen it was meant to change was already up), 3 the emulator never came up.
Every frame stays in `--out`: they are frames of the game and must not be committed.
"""
import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time

SMOKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke.py")
MEM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mem.py")
VAMAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vamap.py")
CR3 = re.compile(r"CR3=([0-9a-fA-F]{8})")


def load_vamap():
    spec = importlib.util.spec_from_file_location("vamap", VAMAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_mem():
    spec = importlib.util.spec_from_file_location("mem", MEM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def capture(window, path):
    """Capture the window, re-finding it once: the guest can destroy and re-create its window."""
    for _ in range(2):
        got = run(["import", "-window", window, path])
        if got.returncode == 0 and os.path.isfile(path):
            return True, window
        fresh = find_window()
        if not fresh or fresh == window:
            break
        window = fresh
    return False, window


REGRAB_SECONDS = 1.5


def regrab(window, path):
    """Capture twice and keep the second.

    The X window's pixmap can lag a keypress by about a second: the grab taken right after `sendkey` comes back
    byte-identical to the frame from *before* the key, and the next one shows the change. Every frame an assertion is
    made on therefore goes through here; `--regrab 0` restores the single grab for a run that does not send input.
    """
    ok, window = capture(window, path)
    if ok and REGRAB_SECONDS > 0:
        time.sleep(REGRAB_SECONDS)
        ok, window = capture(window, path)
    return ok, window


def comparable(path, geom, out_dir):
    """The frame, or the crop of it the assertion is made on -- the title animates outside the menu panel."""
    if not geom:
        return path
    cropped = os.path.join(out_dir, os.path.basename(path)[:-4] + "-crop.png")
    run(["convert", path, "-crop", geom, "+repage", cropped])
    return cropped


def difference(a, b, fuzz=0):
    """Differing pixels between two PNGs, via ImageMagick's AE metric (which exits 1 when they differ).

    `fuzz` is a percentage: differences below it are ignored, which is what separates a menu highlight moving from
    the flicker of an animated background behind a translucent panel.
    """
    cases = ["-fuzz", "%d%%" % fuzz] if fuzz else []
    got = run(["compare", "-metric", "AE"] + cases + [a, b, "null:"])
    text = (got.stderr or got.stdout).strip().split()[0] if (got.stderr or got.stdout).strip() else ""
    try:
        return int(float(text))
    except ValueError:
        return -1


class FocusElsewhere(Exception):
    """Raised instead of sending XTEST keys into a window that is not the emulator."""


STEPS = ("shot", "key", "xkey", "type", "xtype", "wait", "activate", "raise", "noise", "cpu", "monitor",
         "focus", "gkey", "pad", "watch")

PAD = None          # the virtual pad, when a step needs one (created before xemu starts, see vpad.py)
PAD_MAPPING = ""    # the pad's SDL mapping, handed to xemu through SDL_GAMECONTROLLERCONFIG
PAD_GUID = ""       # and the GUID port 1 is bound to, which is how xemu picks *this* device
PAD_HOLD = 0.15


def focus_owner():
    """The window X input focus is on right now, and its name -- an XTEST key goes there, whoever owns it."""
    got = run(["xdotool", "getwindowfocus"]).stdout.strip()
    name = run(["xdotool", "getwindowfocus", "getwindowname"]).stdout.strip() if got else ""
    return got, name or "(no name)"


def parse_step(text):
    name, _, value = text.partition("=")
    name = name.strip().lower()
    if name not in STEPS:
        raise SystemExit("unknown step '%s' (known: %s)" % (text, ", ".join(STEPS)))
    if name != "activate" and not value:
        raise SystemExit("step '%s' needs a value (%s=...)" % (text, name))
    return name, value


def send(window, name, value):
    """Send one input step to the guest; return a line for the log.

    XTEST (`xkey`/`xtype`) is delivered to whichever window owns X input focus, so it is only sent when that is the
    emulator: on a desktop somebody is using, the alternative is typing into their editor or their game, which is both
    useless for the test and audible (KDE rings when an application refuses the key).
    """
    if name == "pad":
        if PAD is None:
            raise SystemExit("a pad= step needs a pad: it is created when --vpad is passed")
        return "pad " + PAD.tap(value, PAD_HOLD)
    if name == "raise":
        run(["xdotool", "windowraise", window])
        return "raise (focus untouched)"
    if name == "activate":
        run(["xdotool", "windowraise", window])
        run(["xdotool", "windowfocus", "--sync", window])
        return "activate"
    targeted = name in ("key", "type")
    if not targeted:
        owner, owner_name = focus_owner()
        if owner != window:
            raise FocusElsewhere("XTEST would type into %s (%s), not the emulator (%s)"
                                 % (owner_name, owner or "?", window))
    if name in ("key", "xkey"):
        args = ["xdotool", "key"] + (["--window", window] if targeted else []) + [value]
        run(args)
        return "%s %s" % (name, value)
    args = ["xdotool", "type", "--delay", "120"] + (["--window", window] if targeted else []) + [value]
    run(args)
    return "%s %r" % (name, value)


def say(*args):
    """Unbuffered: the harness is often run over ssh, where stdout is a pipe and a buffered log hides progress."""
    print(*args, flush=True)


def load_vpad():
    spec = importlib.util.spec_from_file_location("vpad", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                      "vpad.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    global REGRAB_SECONDS, PAD, PAD_MAPPING, PAD_GUID, PAD_HOLD
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iso", required=True)
    ap.add_argument("--bios", required=True, help="directory with mcpx*.bin and a debug flash")
    ap.add_argument("--hdd", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=110, help="boot window before the first step")
    ap.add_argument("--key", default="Tab", help="the single-keystroke form's key (Tab opens the console)")
    ap.add_argument("--settle", type=float, default=4.0, help="the single-keystroke form's wait after the key")
    ap.add_argument("--threshold", type=int, default=200, help="differing pixels that count as a repaint")
    ap.add_argument("--step", action="append", metavar="NAME=VALUE", help="one step of a sequence; repeatable")
    ap.add_argument("--crop", metavar="GEOM", help="assert on this ImageMagick crop (WxH+X+Y), not the whole frame")
    ap.add_argument("--fuzz", type=int, default=0, help="ignore pixel differences below this percentage")
    ap.add_argument("--regrab", type=float, default=REGRAB_SECONDS,
                    help="seconds between the two grabs of an asserting capture (0 = one grab)")
    ap.add_argument("--vpad", action="store_true",
                    help="create the virtual pad even when no step presses it (proves SDL sees the device)")
    ap.add_argument("--pad-hold", type=float, default=PAD_HOLD, help="seconds a pad button is held (default 0.15)")
    ap.add_argument("--pad-settle", type=float, default=1.5, help="seconds udev gets to publish the pad")
    ap.add_argument("--eeprom", default=None,
                    help="EEPROM to boot from (default: the one xemu itself uses); a copy is kept in --out, so a "
                         "run's profile and settings survive only as long as its own directory")
    ap.add_argument("--xemu", default=os.environ.get("XEMU", smoke_default()))
    ap.add_argument("--gdb", type=int, default=0,
                    help="open a GDB stub on this port, so tools/xemu/mem.py and objects.py can read the guest "
                         "during the sequence")
    ap.add_argument("--watch-stops", type=int, default=3, help="watchpoint stops to take per watch= step")
    ap.add_argument("--watch-window", type=lambda v: int(v, 0), default=0x200,
                    help="how close a register or stack word must be to the watched address to count as naming it")
    ap.add_argument("--watch-stack", type=lambda v: int(v, 0), default=0x400, help="stack bytes to read around ESP")
    ap.add_argument("--watch-timeout", type=float, default=20.0, help="seconds to wait for one watchpoint stop")
    ap.add_argument("--watch-length", type=lambda v: int(v, 0), default=8,
                    help="bytes the read watchpoint covers, from the watch= text's first character")
    ap.add_argument("--linger", type=float, default=0,
                    help="after the last step, keep the guest running this many seconds for a memory read "
                         "(xemu is killed on exit, so a read has to happen while this sleeps)")
    args = ap.parse_args()

    REGRAB_SECONDS = args.regrab
    PAD_HOLD = args.pad_hold
    steps = [parse_step(s) for s in args.step] if args.step else \
            [("shot", "before"), ("key", args.key), ("wait", str(args.settle)), ("shot", "after")]

    if not os.environ.get("DISPLAY"):
        say("FAIL: no DISPLAY -- xemu needs X11 or Wayland (docs/REGRESSION.md)")
        return 3
    for tool in ["xdotool", "import", "compare"] + (["convert"] if args.crop else []):
        if subprocess.run(["sh", "-c", "command -v %s" % tool], capture_output=True).returncode == 0:
            continue
        say("FAIL: %s is not installed" % tool)
        return 3

    os.makedirs(args.out, exist_ok=True)
    # The same discovery rule smoke.py uses: an mcpx boot ROM and a *debug* flash, which is what the boot needs.
    mcpx = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                 if f.lower().startswith("mcpx")), None)
    flash = next((os.path.join(args.bios, f) for f in sorted(os.listdir(args.bios))
                  if f.lower().endswith(".bin") and "debug" in f.lower()), None)
    if not mcpx or not flash:
        say("FAIL: need mcpx*.bin and a debug flash in %s" % args.bios)
        return 3
    toml = os.path.join(args.out, "xemu.toml")
    sock_path = os.path.join(args.out, "qmp.sock")
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    # xemu merges the settings in its base path ($XDG_DATA_HOME/xemu/xemu/xemu.toml) *over* -config_path, and
    # this desktop's own file binds port 1 to a physical pad. Measured: the run's TOML asked for the virtual pad
    # on port 1, and xemu came back up with the physical one on port 1 and the virtual one on port 2, so the pad
    # drove nobody. A private base path is what makes the run's own bindings the only ones.
    env_xdg = os.path.join(args.out, "xdg")
    os.makedirs(os.path.join(env_xdg, "xemu", "xemu"), exist_ok=True)
    # The EEPROM is where the profile list lives: an empty one sends `Continue` to `SELECT PROFILE`, while the one
    # this harness has been carrying since 2026-10-24 keeps a profile and goes straight to the map list.
    shared_eeprom = args.eeprom or os.path.join(os.path.expanduser("~/.local/share/xemu/xemu"), "eeprom.bin")
    eeprom = os.path.join(args.out, "eeprom.bin")
    if not os.path.exists(eeprom) and os.path.exists(shared_eeprom):
        shutil.copyfile(shared_eeprom, eeprom)
    want_pad = args.vpad or any(name == "pad" for name, _ in steps)
    if want_pad:
        module = load_vpad()
        PAD = module.Pad(settle=args.pad_settle)
        PAD_MAPPING, PAD_GUID = module.SDL_MAPPING, module.GUID
        say("pad: created before the emulator starts, so SDL sees it at init")
    with open(toml, "w") as f:
        f.write("[general]\nshow_welcome = false\n[sys]\nmem_limit = '128'\n")
        # xemu drops input while its window is unfocused; a harness started over ssh never gets the WM to focus it.
        f.write("[input]\nbackground_input_capture = true\n")
        if want_pad:
            # Port 1 is bound by GUID, and this desktop has a physical pad of its own: binding to the virtual
            # pad's own GUID is what makes xemu read the device the steps press, and auto-bind has to be off or
            # xemu hands port 1 to whatever it finds first -- measured, the physical pad took port 1 and the
            # virtual one was pushed to port 2, where the game does not look.
            f.write("auto_bind = false\ngamepad_mappings = [\n    { gamepad_id = '%s'}\n    ]\n" % PAD_GUID)
            f.write("[input.bindings]\nport1_driver = 'usb-xbox-gamepad'\nport1 = '%s'\n" % PAD_GUID)
        f.write("[sys.files]\n")
        f.write("bootrom_path = '%s'\nflashrom_path = '%s'\n" % (mcpx, flash))
        if os.path.exists(eeprom):
            f.write("eeprom_path = '%s'\n" % eeprom)
        f.write("hdd_path = '%s'\ndvd_path = '%s'\n" % (args.hdd, args.iso))
    env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1", XDG_DATA_HOME=env_xdg)
    if PAD_MAPPING:
        env["SDL_GAMECONTROLLERCONFIG"] = PAD_MAPPING
        # SDL drops joystick events while the application is unfocused unless it is told not to, which is the
        # pad's half of the reason a scripted run has to work without ever owning the focus.
        env["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
    log = open(os.path.join(args.out, "xemu.log"), "wb")
    prior_focus = ""
    cmd = [args.xemu, "-config_path", toml,
           "-serial", "file:" + os.path.join(args.out, "serial.log"),
           "-qmp", "unix:%s,server,nowait" % sock_path]
    if args.gdb:
        # A second evidence channel: QMP samples the CPU and injects input, the stub reads memory.
        # Measured: a read needs `interrupt()` and *virtual* mode, never `Qqemu.PhyMemMode:1`.
        cmd += ["-gdb", "tcp::%d" % args.gdb]
    proc = subprocess.Popen(cmd,
                            stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    try:
        time.sleep(min(args.seconds, 10))
        if proc.poll() is not None:
            say("FAIL: xemu exited immediately (see %s/xemu.log)" % args.out)
            return 3
        say("waiting %ds for the title" % args.seconds)
        time.sleep(max(0, args.seconds - 10))
        qmp = {}

        def cmd(name, arguments=None):
            """One QMP call, connecting on first use (smoke.py's session, so both tools speak the same dialect)."""
            if "fn" not in qmp:
                module = load_smoke()
                sock = module.qmp_connect(sock_path, time.time() + 60)
                qmp["fn"] = module.qmp_session(sock) if sock else None
                qmp["module"] = module
            if qmp["fn"] is None:
                return None
            return qmp["fn"](name, arguments)

        window = find_window()
        if window is None:
            say("FAIL: no xemu window on %s" % os.environ.get("DISPLAY"))
            return 3
        say("window: %s" % window)
        prior_focus, prior_name = focus_owner()
        if prior_focus and prior_focus != window:
            say("note: X input focus is %s (%s), not the emulator -- XTEST steps will refuse rather than type there"
                % (prior_name, prior_focus))
        failures, shots, previous, measured_noise = [], 0, None, False
        for index, (name, value) in enumerate(steps, 1):
            if name == "noise":
                first = os.path.join(args.out, "%02d-noise-a.png" % index)
                second = os.path.join(args.out, "%02d-noise-b.png" % index)
                ok, window = regrab(window, first)
                if ok:
                    time.sleep(float(value))
                    ok, window = regrab(window, second)
                if not ok:
                    say("FAIL: could not capture the noise frames")
                    return 3
                floor = difference(comparable(first, args.crop, args.out),
                                   comparable(second, args.crop, args.out), args.fuzz)
                say("step %d: noise %d differing pixels over %ss with no input%s"
                    % (index, floor, value, " (crop %s)" % args.crop if args.crop else ""))
                measured_noise = True
                if floor > 0:
                    args.threshold = max(args.threshold, 2 * floor)
                    say("         threshold raised to %d (twice the noise floor)" % args.threshold)
                continue
            if name == "focus":
                deadline = time.time() + float(value)
                while time.time() < deadline:
                    if focus_owner()[0] == window:
                        say("step %d: the emulator owns the focus after %.0fs"
                            % (index, float(value) - (deadline - time.time())))
                        break
                    time.sleep(0.5)
                else:
                    owner, owner_name = focus_owner()
                    say("REFUSED: the emulator never got the focus (owner is %s (%s)); nothing was sent"
                        % (owner_name, owner or "?"))
                    return 2
                continue
            if name == "gkey":
                got = cmd("human-monitor-command", {"command-line": "sendkey " + value})
                if got is None:
                    say("FAIL: no QMP socket at %s" % sock_path)
                    return 3
                say("step %d: guest key %s -> %s" % (index, value, (got.get("return") or "").strip() or "ok"))
                continue
            if name == "monitor":
                got = cmd("human-monitor-command", {"command-line": value})
                if got is None:
                    say("FAIL: no QMP socket at %s" % sock_path)
                    return 3
                say("step %d: monitor %s -> %s" % (index, value, (got.get("return") or "").strip() or "(no output)"))
                continue
            if name == "cpu":
                status = cmd("query-status")
                if status is None:
                    say("FAIL: no QMP socket at %s" % sock_path)
                    return 3
                module = qmp["module"]
                eips = []
                for _ in range(int(value)):
                    regs = cmd("human-monitor-command", {"command-line": "info registers"})
                    match = module.EIP_RE.search((regs or {}).get("return", "") if regs else "")
                    eips.append(int(match.group(1), 16) if match else None)
                    time.sleep(1.0)
                kernel = sum(1 for e in eips if e is not None and e >= module.KERNEL_BASE)
                say("step %d: vm %s; %d eip samples, %d in kernel: %s"
                    % (index, status.get("return", {}).get("status"), len(eips), kernel,
                       " ".join("0x%08x" % e if e is not None else "?" for e in eips)))
                continue
            if name == "watch":
                if not args.gdb:
                    say("FAIL: a watch= step needs --gdb, so there is a stub to attach to")
                    return 3
                ram = os.path.join(args.out, "watch-ram.bin")
                got = cmd("human-monitor-command", {"command-line": 'pmemsave 0 0x8000000 "%s"' % ram})
                if got is None:
                    say("FAIL: no QMP socket at %s" % sock_path)
                    return 3
                with open(ram, "rb") as handle:
                    blob = handle.read()
                # The terminator is part of the pattern: `hist` alone also matches inside `History`, which is how a
                # first version of this step armed a watchpoint on a class-name string nobody reads (8 hits, no stop).
                needle = value.encode("utf-16-le") + b"\x00\x00"
                hits, at = [], blob.find(needle)
                while at >= 0 and len(hits) < 8:
                    hits.append(at)
                    at = blob.find(needle, at + 1)
                say("step %d: watch %r -- %s holds %d byte(s), %d cop%s of the UTF-16 text%s"
                    % (index, value, os.path.basename(ram), len(blob), len(hits),
                       "y" if len(hits) == 1 else "ies",
                       "" if not hits else " (%s)" % ", ".join("%#010x" % hit for hit in hits)))
                if not hits:
                    continue
                # Arm at the *start* of the string: a text pattern matches inside it, and a reader starts at the
                # first character. Walk back while the bytes are still printable UTF-16.
                start = hits[0]
                while start >= 2 and 0x20 <= blob[start - 2] <= 0x7E and blob[start - 1] == 0:
                    start -= 2
                if start != hits[0]:
                    say("         the string starts at %#010x (the match is %#x bytes into it)"
                        % (start, hits[0] - start))
                # `pmemsave` writes *physical* memory and the stub watches *linear* addresses: the page tables in
                # the same dump translate one into the other (CR3 comes from the register file, one QMP call).
                try:
                    registers = cmd("human-monitor-command", {"command-line": "info registers"})
                except Exception:
                    registers = None
                match = CR3.search((registers or {}).get("return", "") or "")
                if not match:
                    say("         no CR3 in `info registers`: cannot translate %#010x to a linear address" % start)
                    continue
                cr3 = int(match.group(1), 16)
                vamap = load_vamap()
                linear = vamap.linear_for(blob, cr3, start)
                if not linear:
                    say("         CR3 %#x maps no linear address onto %#010x: nothing to watch" % (cr3, start))
                    continue
                say("         CR3 %#x: %#010x is linear %s"
                    % (cr3, start, ", ".join("%#010x (%s)" % (at, how) for at, how, _flags in linear)))
                watched = linear[0][0]
                memory = load_mem()
                gdb = memory.Gdb("127.0.0.1", args.gdb)
                try:
                    first, supported, reply, records = memory.watchpoint(
                        gdb, watched, length=args.watch_length, window=args.watch_window,
                        stack=args.watch_stack, stops=args.watch_stops, timeout=args.watch_timeout)
                    for line in memory.format_report(watched, first, supported, reply, records,
                                                     args.watch_window):
                        say("         " + line)
                except (IOError, OSError) as exc:
                    say("         the stub did not answer: %s" % exc)
                finally:
                    try:
                        gdb.resume()
                    except (IOError, OSError):
                        pass
                    gdb.close()
                continue
            if name == "wait":
                time.sleep(float(value))
                say("step %d: wait %ss" % (index, value))
                continue
            if name == "shot":
                path = os.path.join(args.out, "%s.png" % value)
                ok, window = regrab(window, path)
                if not ok:
                    say("FAIL: could not capture %s" % path)
                    return 3
                if previous is None:
                    say("step %d: shot %s (first frame, nothing to compare)" % (index, value))
                else:
                    diff = difference(comparable(previous, args.crop, args.out),
                                      comparable(path, args.crop, args.out), args.fuzz)
                    if diff < 0:
                        say("FAIL: could not compare %s with %s" % (path, previous))
                        return 3
                    ok = diff >= args.threshold
                    if not measured_noise and diff < args.threshold:
                        say("         (no noise= step ran: the threshold is the default %d, not a measured floor)"
                            % args.threshold)
                    shots += 1
                    say("step %d: shot %s differing pixels: %d (threshold %d) %s"
                        % (index, value, diff, args.threshold, "PASS" if ok else "FAIL"))
                    if not ok:
                        failures.append(value)
                previous = path
                continue
            try:
                say("step %d: %s" % (index, send(window, name, value)))
            except FocusElsewhere as exc:
                say("REFUSED: %s" % exc)
                say("         nothing was sent. A `--step=activate` moves focus to the emulator, and takes it away")
                say("         from whoever is using the desktop -- do that only when the machine is free.")
                return 2
        if args.linger:
            # The guest is alive and the stub is up: this is the window a memory read attaches in.
            say("lingering %.0fs for a memory read: gdb 127.0.0.1:%d" % (args.linger, args.gdb))
            time.sleep(args.linger)
        if failures:
            say("FAIL: %d shot step(s) did not repaint: %s" % (len(failures), ", ".join(failures)))
            return 2
        say("PASS: all %d shot step(s) repainted" % shots)
        return 0
    finally:
        # xemu runs as an AppImage: the wrapper exits but `AppRun` keeps the HDD lock, so kill the whole group.
        try:
            os.killpg(proc.pid, 15)
        except OSError:
            proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                proc.kill()
        if PAD is not None:
            PAD.close()
        # Do not leave the desktop user's focus on a machine that is about to be killed.
        try:
            owner, _ = focus_owner()
            if prior_focus and owner == window and owner != prior_focus:
                run(["xdotool", "windowfocus", "--sync", prior_focus])
                say("focus returned to %s" % prior_focus)
        except Exception:
            pass
        log.close()


def smoke_default():
    spec = importlib.util.spec_from_file_location("smoke", SMOKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DEFAULT_XEMU


if __name__ == "__main__":
    sys.exit(main())
