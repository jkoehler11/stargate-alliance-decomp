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
    monitor=CMD  run one QMP human-monitor-command (e.g. `device_add usb-kbd,port=1.3,id=kbd`, which xemu documents
                as the way to give the guest a keyboard); the reply is printed
    cpu=COUNT   sample the guest's EIP COUNT times over QMP: pixels cannot tell a key that was ignored from a guest
                that stopped executing, so every sequence that reads "no change" needs this
    noise=SECS  capture, wait SECS with no input, capture again: the screen's own animation, in pixels

`key=` and `type=` take target-window input, `xkey=`/`xtype=` take XTEST input; which reaches the guest is a
measured fact per input type, not a safe assumption (docs/XEMU.md carries the numbers). SDL ignores synthetic
`XSendEvent` keys, so a targeted `key=` that "repaints" may be the background animating rather than input
arriving: run a `noise=` step first and give `--crop` the region the input is supposed to change.

It needs `xdotool` and ImageMagick's `import`/`compare`, and an X display: over plain ssh xemu cannot open one at
all (docs/REGRESSION.md). Exit codes: 0 every shot step repainted, 2 a step did not change the screen (the input
never reached the guest, or the screen it was meant to change was already up), 3 the emulator never came up.
Every frame stays in `--out`: they are frames of the game and must not be committed.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import time

SMOKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke.py")


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


STEPS = ("shot", "key", "xkey", "type", "xtype", "wait", "activate", "noise", "cpu", "monitor")


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


def main():
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
    ap.add_argument("--xemu", default=os.environ.get("XEMU", smoke_default()))
    args = ap.parse_args()

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
    with open(toml, "w") as f:
        f.write("[general]\nshow_welcome = false\n[sys]\nmem_limit = '128'\n")
        # xemu drops input while its window is unfocused; a harness started over ssh never gets the WM to focus it.
        f.write("[input]\nbackground_input_capture = true\n[sys.files]\n")
        f.write("bootrom_path = '%s'\nflashrom_path = '%s'\n" % (mcpx, flash))
        f.write("hdd_path = '%s'\ndvd_path = '%s'\n" % (args.hdd, args.iso))
    env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1")
    log = open(os.path.join(args.out, "xemu.log"), "wb")
    prior_focus = ""
    proc = subprocess.Popen([args.xemu, "-config_path", toml,
                             "-serial", "file:" + os.path.join(args.out, "serial.log"),
                             "-qmp", "unix:%s,server,nowait" % sock_path],
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
                ok, window = capture(window, first)
                if ok:
                    time.sleep(float(value))
                    ok, window = capture(window, second)
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
            if name == "wait":
                time.sleep(float(value))
                say("step %d: wait %ss" % (index, value))
                continue
            if name == "shot":
                path = os.path.join(args.out, "%s.png" % value)
                ok, window = capture(window, path)
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
