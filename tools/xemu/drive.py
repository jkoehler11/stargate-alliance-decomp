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
    watch=TEXT  dump all of guest RAM inside the boot, find TEXT (UTF-16LE with its terminator, or — when a repeated
                key has grown the string, measured 2026-11-06 — as the prefix of a longer one) in it, translate its *physical* address to the *linear* one the CPU uses (the page
                tables are in the same dump, CR3 from `info registers`, `tools/xemu/vamap.py`), then arm a *read*
                watchpoint over the string's first `--watch-length` bytes and print each stop's register file, the
                stack words around ESP that name the address, and EIP. It needs `--gdb`; the dump is written to
                `--out` (delete it: 128 MiB). `--watch-key` sends one guest key once the watchpoint is armed, because
                an idle guest reads nothing: the read that names the owner has to be caused, and a stop that never
                comes is reported as "no stop in N s" rather than as a stub fault.
    brk=SPEC    a *script* breakpoint, and the reason this step exists: a script function's bytecode is only a byte
                pattern in the copy the package loader made, so finding it in a RAM dump gives the address the
                interpreter *reads* it from, and a watchpoint there answers "did this script function run" for a build
                whose natives are already accounted for. **Read, not execute**: UE2's VM reads bytecode through a
                pointer (`Script[CodeOffset++]` -> `GNatives[op]`) and never fetches an instruction from it, so
                `--brk-kind=exec` (a Z0 execution breakpoint) cannot fire on a script function -- it is kept as the
                control that measures exactly that, and `--brk-kind=read` (the default) arms a Z3 read watchpoint on
                the function's first byte, which stops on the interpreter's first read at entry. SPEC is `label=hex` repeated and
                comma-separated, and the hex is **not** the package's bytes: the loader widens every compact index
                operand to a four-byte slot, so `tools/uscript-dis.py --script PKG CLASS FUNCTION --hex-bytes N`
                prints the pattern with `??` where an index was (matched here as one byte each); a byte-exact copy of
                the file's script occurs nowhere in a loaded guest (docs/XEMU.md). `--brk-freeze` stops the VM across
                the dump and the arming, which is what makes arming a load-time function possible at all. The
                step dumps RAM, finds each pattern, translates it through the page tables to a linear address, arms an
                watchpoint on each copy, then resumes the guest for `--brk-wait` seconds and reports
                **which labels fired** with EIP/ESP and the first 16 stack words -- so a chain of script functions
                answers "how far did it get" in one boot. `--brk-tap=SECS:BUTTON` presses the pad from inside that
                wait, because a step list is sequential and a brk= step that waits behind an intro card ("Press A")
                measures a load that is not running; `--brk-wait` takes a comma list, one value per brk= step, so a
                second short step can re-find the same patterns after the load and say whether the first step's armed
                addresses were still the guest's. It needs `--gdb`.

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
all (docs/REGRESSION.md). A shot named in `--static-shot` is the inverse: its frame *cannot* change (the stock disc's console draws
nothing -- it has no `PostRender`), so an identical frame is the expected reading and a repaint is the failure.
Without it a green console run ends `FAIL: 2 shot step(s) did not repaint` and exit 2, which reads like a lost key.
**Nothing this harness calls is allowed to block forever.** Every `xdotool`, `import`, `compare` and `convert` call
carries a deadline (`--deadline`, default 10 s for the X tools, x3 for `import`, x12 for the ImageMagick filters) and a
call that misses it is reported *by name*, with the step it was on, whether the emulator is still alive (and the tail of
its log), whether the X server still answers and where its focus is, and whether the guest is executing. The evidence
that made this necessary: of six boots that reached for the sink slots, two ended with the log stopping on the line
before an `xdotool` call and no further word, and two more died with the emulator gone (`apprun=0`) — a hang and a lost
guest were both reported as "still the menu". Both now end the run at the step they happened, exit 4, and write
`--out/stall.txt`. The same run also checks `proc.poll()` before every step, so a guest that exits mid-sequence is
named at that step rather than at the capture that fails afterwards.

Exit codes: 0 every shot step repainted, 2 a step did not change the screen (the input
never reached the guest, or the screen it was meant to change was already up), 3 the emulator never came up,
4 an external tool missed its deadline or the emulator exited mid-sequence (see `stall.txt`).
Every frame stays in `--out`: they are frames of the game and must not be committed.
"""
import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import struct
import sys
import threading
import time

SMOKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke.py")
MEM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mem.py")
VAMAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vamap.py")
RAMSCAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ramscan.py")
CR3 = re.compile(r"CR3=([0-9a-fA-F]{8})")


def load_ramscan():
    spec = importlib.util.spec_from_file_location("ramscan", RAMSCAN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


TOOL_DEADLINE = 10.0   # seconds an X tool may take before the run names it; --deadline overrides
GRAB_FACTOR = 3.0      # `import` grabs a 1280x960 window in well under a second; this is the factor for a *stuck* one
FILTER_FACTOR = 12.0   # `compare`/`convert` on two frames, which are slower but still bounded work

# Which step the run is on, for a report written from inside a hang and for the guest-exit check between steps.
STATE = {"step": "before the first step", "window": None, "index": 0}


class ToolHang(Exception):
    """An external tool (one of the X or ImageMagick calls) never answered inside its deadline.

    Every call this harness made to `xdotool`, `import`, `compare` or `convert` used to be unbounded. Measured over six
    sink-slot boots (PLAN 2026-11-08), two ended with the log stopping on the line *before* an `xdotool` call and no
    further word: a hang is unreadable without a deadline, because a harness blocked inside `subprocess.run` cannot even
    report that it is stuck. The deadline turns a wedge into a named call.
    """

    def __init__(self, cmd, deadline, elapsed):
        self.cmd = list(cmd)
        self.deadline = deadline
        self.elapsed = elapsed
        super().__init__("%s did not answer within %.1fs (%s)"
                         % (os.path.basename(cmd[0]), deadline, " ".join(str(c) for c in cmd[1:5])))


def deadline_for(cmd):
    """How long this call may take: the X tools are cheap, `import` and the ImageMagick filters less so."""
    name = os.path.basename(cmd[0])
    if name == "import":
        return max(5.0, TOOL_DEADLINE * GRAB_FACTOR)
    if name in ("compare", "convert", "identify"):
        return max(10.0, TOOL_DEADLINE * FILTER_FACTOR)
    return TOOL_DEADLINE


def run(cmd, deadline=None, **kw):
    """One external tool, bounded: raises ToolHang instead of blocking.

    `subprocess.run(timeout=...)` kills the child, so a wedged `xdotool` cannot outlive the report that names it.
    """
    limit = deadline if deadline is not None else deadline_for(cmd)
    started = time.time()
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=limit, **kw)
    except subprocess.TimeoutExpired:
        raise ToolHang(cmd, limit, time.time() - started)


def bounded(fn, seconds):
    """Call fn in a thread and give up after `seconds`; None when it does not answer.

    Some probes can block on a guest that is wedged or paused (a QMP round trip, a stub read), and the report must not
    be able to hang the way the thing it reports on did.
    """
    box = {}

    def body():
        try:
            box["value"] = fn()
        except Exception as exc:                                            # noqa: BLE001 -- reported as text
            box["error"] = exc

    worker = threading.Thread(target=body, daemon=True)
    worker.start()
    worker.join(seconds)
    return None if worker.is_alive() else box.get("value")


def probe(cmd, seconds=3.0):
    """A bounded question asked from inside the stall report: the reply, or None when the tool will not talk."""
    try:
        return run(cmd, deadline=seconds)
    except ToolHang:
        return None


def eeprom_digest(path):
    """The md5 of the profile image a run boots with: two boots that differ only in this file look identical otherwise."""
    import hashlib
    try:
        with open(path, "rb") as handle:
            return hashlib.md5(handle.read()).hexdigest()
    except OSError:
        return "(unreadable)"


def tail(path, lines=6):
    try:
        with open(path, "r", errors="replace") as handle:
            return handle.read().splitlines()[-lines:]
    except OSError:
        return []


def stall_report(exc, out_dir, proc=None, ask=None):
    """Name the call that never answered, and what the desktop and the guest looked like while it hung.

    Written to `--out/stall.txt` as well as the log, because a run killed from outside leaves only the file behind.
    Every question in here is bounded: the reason for the report may be that the X server itself stopped answering,
    and "the probe did not answer either" is a reading, not a second hang.
    """
    lines = ["STALL: %s" % exc,
             "       step: %s" % STATE.get("step")]
    if proc is not None:
        status = proc.poll()
        lines.append("       emulator: %s" % ("exited %s" % status if status is not None
                                             else "still running (pid %d)" % proc.pid))
        for line in tail(os.path.join(out_dir, "xemu.log")):
            lines.append("       xemu.log: %s" % line.strip())
    got = probe(["xdotool", "search", "--onlyvisible", "--name", "xemu"])
    if got is None:
        lines.append("       X: `xdotool search` did not answer either -- the server is not serving this client")
    else:
        live = got.stdout.split()
        lines.append("       X: answered; xemu windows now %s (this run's: %s)"
                     % (", ".join(live) or "none", STATE.get("window") or "?"))
    got = probe(["xdotool", "getwindowfocus", "getwindowname"])
    lines.append("       X focus: %s" % ("did not answer" if got is None
                                        else (got.stdout.strip() or "(no name)")))
    if ask is not None:
        # The guest is the other half of the question: a harness hung on X while the console still animates is a
        # different fault from a guest that stopped executing, and EIP is what separates them.
        answer = bounded(ask, 4.0)
        lines.append("       guest: %s" % (answer or "QMP did not answer inside 4 s"))
    text = "\n".join(lines)
    say(text)
    try:
        with open(os.path.join(out_dir, "stall.txt"), "w") as handle:
            handle.write(text + "\n")
    except OSError:
        pass
    return 4


def guest_gone(proc, out_dir, ask=None):
    """The emulator exited mid-sequence: say at which step, with its exit status and the tail of its own log."""
    status = proc.poll()
    lines = ["STALL: the emulator exited (%s) at %s" % (status, STATE.get("step"))]
    for line in tail(os.path.join(out_dir, "xemu.log")):
        lines.append("       xemu.log: %s" % line.strip())
    if ask is not None:
        lines.append("       guest: %s" % (bounded(ask, 4.0) or "no QMP answer (the emulator is gone)"))
    text = "\n".join(lines)
    say(text)
    try:
        with open(os.path.join(out_dir, "stall.txt"), "w") as handle:
            handle.write(text + "\n")
    except OSError:
        pass
    return 4


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
         "focus", "gkey", "pad", "watch", "brk")

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


WATCH_STOP = re.compile(rb"(?:r|a)?watch:([0-9a-fA-F]+);")


def watch_address(stop):
    """The address a read/access watchpoint stop names (`T05thread:01;rwatch:16b2800;`), or None.

    A read watchpoint stops at the *reader's* EIP, so this address is the only thing that says which pattern was
    touched -- matching EIP against the armed addresses is the execution form of the question and answers nothing
    here. xemu's stub names the address, not an index into the watchpoint table (a `watch:2` reading of this packet is
    a number that matches no address, and it ended the first run's wait on its first hit).
    """
    match = WATCH_STOP.search(stop or b"")
    return int(match.group(1), 16) if match else None


def load_vpad():
    spec = importlib.util.spec_from_file_location("vpad", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                      "vpad.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selftest():
    """The deadline path, which no game run can be relied on to exercise: a hang has to be produced on purpose.

    Hermetic -- no X, no emulator, no dump: every external tool is substituted, so this runs in `make ci`.
    """
    import tempfile
    global TOOL_DEADLINE
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, ok, detail))

    # 1. The table: the X tools get the base deadline, a grab three, a filter twelve.
    saved = TOOL_DEADLINE
    try:
        TOOL_DEADLINE = 10.0
        case("xdotool gets the base deadline", deadline_for(["xdotool", "getwindowfocus"]) == 10.0)
        case("import gets three times the base", deadline_for(["import", "-window", "1", "x.png"]) == 30.0)
        case("compare gets twelve", deadline_for(["compare", "-metric", "AE", "a", "b", "null:"]) == 120.0)
    finally:
        TOOL_DEADLINE = saved

    # 2. A tool that never answers raises inside its deadline and names itself; the child does not outlive it.
    started = time.time()
    try:
        run([sys.executable, "-c", "import time; time.sleep(30)"], deadline=0.4)
        case("a hung tool raises ToolHang", False, "it returned")
    except ToolHang as exc:
        took = time.time() - started
        case("a hung tool raises ToolHang", True, exc.args[0])
        case("the hang report names the program", "did not answer within" in exc.args[0], exc.args[0])
        case("the deadline is honoured, not slept through", took < 5.0, "%.2fs" % took)
        case("the deadline it says is the one used", abs(exc.deadline - 0.4) < 1e-9)

    # 3. The same call for a tool that answers: unchanged behaviour, which is the case that must not regress.
    got = run(["sh", "-c", "echo hi"])
    case("a fast call still returns its output", got.returncode == 0 and got.stdout.strip() == "hi")

    # 4. The stall report reads X and the guest without hanging on either, and leaves a file behind.
    with tempfile.TemporaryDirectory() as out:
        saved_probe = globals()["probe"]
        try:
            globals()["probe"] = lambda cmd, seconds=3.0: None          # an X server that answers nothing
            STATE["step"] = "step 3 (shot=01-list)"
            exc = ToolHang(["xdotool", "getwindowfocus"], 10.0, 10.0)
            code = stall_report(exc, out)
            text = open(os.path.join(out, "stall.txt")).read()
        finally:
            globals()["probe"] = saved_probe
        case("the report exits 4", code == 4)
        case("the report names the call", "STALL: xdotool did not answer within 10.0s" in text, text.splitlines()[0])
        case("the report names the step", "step 3 (shot=01-list)" in text)
        case("a silent X server is a reading, not a crash", "did not answer either" in text)
        case("the report is written to stall.txt", os.path.getsize(os.path.join(out, "stall.txt")) > 0)

        # 5. A guest that exited is named at its step with its status, and the guest probe is asked exactly once.
        class Dead:
            pid = 4321

            def poll(self):
                return 3
        asked = []
        with tempfile.TemporaryDirectory() as out2:
            code = guest_gone(Dead(), out2, lambda: asked.append(1) or "vm running, EIP 0x80024a97")
            text = open(os.path.join(out2, "stall.txt")).read()
        case("a dead emulator exits 4", code == 4)
        case("a dead emulator is reported with its status and step", "exited (3)" in text and "step 3" in text)
        case("the report's EIP answer is carried through", "EIP" in text)

    # 6. A probe that blocks is given up on, so the report cannot hang like the thing it reports.
    case("a blocking probe is abandoned", bounded(lambda: time.sleep(30), 0.3) is None)
    case("a fast probe is returned", bounded(lambda: "vm running", 2.0) == "vm running")

    bad = [c for c in cases if not c[1]]
    for name, ok, detail in cases:
        say("%s %s%s" % ("ok  " if ok else "FAIL", name, ": " + detail if detail else ""))
    say("%s: %d case(s), %d failed" % ("ok  " if not bad else "FAIL", len(cases), len(bad)))
    return 0 if not bad else 1


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()
    global REGRAB_SECONDS, PAD, PAD_MAPPING, PAD_GUID, PAD_HOLD, TOOL_DEADLINE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iso", required=True)
    ap.add_argument("--bios", required=True, help="directory with mcpx*.bin and a debug flash")
    ap.add_argument("--hdd", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=110, help="boot window before the first step")
    ap.add_argument("--key", default="Tab", help="the single-keystroke form's key (Tab opens the console)")
    ap.add_argument("--settle", type=float, default=4.0, help="the single-keystroke form's wait after the key")
    ap.add_argument("--threshold", type=int, default=200, help="differing pixels that count as a repaint")
    ap.add_argument("--static-shot", action="append", default=[], metavar="SUBSTR",
                    help="a shot whose frame cannot change, like a console with no `PostRender`: identical is the "
                         "expected reading and it does not fail the run; repeatable, matched as a substring of the "
                         "shot's name")
    ap.add_argument("--step", action="append", metavar="NAME=VALUE", help="one step of a sequence; repeatable")
    ap.add_argument("--crop", metavar="GEOM", help="assert on this ImageMagick crop (WxH+X+Y), not the whole frame")
    ap.add_argument("--fuzz", type=int, default=0, help="ignore pixel differences below this percentage")
    ap.add_argument("--regrab", type=float, default=REGRAB_SECONDS,
                    help="seconds between the two grabs of an asserting capture (0 = one grab)")
    ap.add_argument("--deadline", type=float, default=TOOL_DEADLINE,
                    help="seconds an external call may take before the run stops and names it (default %(default)s; "
                         "`import` gets x3 and the ImageMagick filters x12). Measured: two sink-slot boots hung on an "
                         "`xdotool` call with no further log line, and an unbounded harness cannot report its own hang")
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
    ap.add_argument("--watch-key", metavar="KEY", default=None,
                    help="guest key to send over QMP once a watch= step has armed its watchpoint: an idle guest "
                         "reads nothing, so the read that names the owner has to be caused (e.g. a letter that "
                         "appends to the console's TypedStr)")
    ap.add_argument("--watch-length", type=lambda v: int(v, 0), default=8,
                    help="bytes the read watchpoint covers, from the watch= text's first character")
    ap.add_argument("--brk-wait", default="60",
                    help="seconds a brk= step leaves the guest running, waiting for its breakpoints; a comma list is "
                         "consumed one value per brk= step, so a second short step can re-find the patterns after a "
                         "load and say whether the first step's armed addresses were still the guest's")
    ap.add_argument("--brk-timeout", type=float, default=20.0, help="seconds to wait for one breakpoint stop")
    ap.add_argument("--brk-tap", action="append", metavar="SECS:BUTTON",
                    help="tap a virtual-pad button SECS into a brk= step's wait, from inside the wait: a load that "
                         "stops to ask for a button (M00's intro card) never reaches the code the breakpoints cover, "
                         "so without this an armed load reports every label dead for a reason that is input, not the "
                         "route. Repeatable, e.g. --brk-tap=8:a --brk-tap=25:a")

    ap.add_argument("--brk-copies", type=int, default=4,
                    help="copies of one pattern to arm (a package's bytecode can have more than one in RAM)")
    ap.add_argument("--brk-keep", action="store_true",
                    help="keep a read watchpoint armed after it fires and log the reader eip of every stop: one stop "
                         "says a byte was read once, not who read it, and a function that runs is read again by the "
                         "interpreter -- the set of eips is what separates the loader's copy from the VM")
    ap.add_argument("--brk-stops", type=int, default=8,
                    help="stops to take per brk= step under --brk-keep (a function read every frame reaches this at "
                         "once, which is the point of the control)")
    ap.add_argument("--brk-keep-stops", type=int, default=0, metavar="N",
                    help="under --brk-keep, reads to record per armed copy before that copy's watchpoint is removed "
                         "(0 = keep every copy armed for the whole wait): one hot arm otherwise eats the --brk-stops "
                         "budget and the wait ends in the load window instead of gameplay -- three load passes are "
                         "the measurement, the rest is repetition")
    ap.add_argument("--brk-kind", default="read",
                    help="`read` or `exec`, one value per brk= step (comma list, or one for all): `read` puts a Z3 read "
                         "watchpoint on the function's "
                         "first byte, which is what an *interpreted* build does with bytecode -- UE2's VM reads it "
                         "through a pointer and never fetches an instruction from it, so a Z0 execution breakpoint on "
                         "a script function cannot fire; `exec` keeps that Z0, the control that shows it")
    ap.add_argument("--brk-tap-step", type=int, default=1,
                    help="which brk= step (1-based) the --brk-tap presses belong to: a press in the title screen is a "
                         "menu action, so the taps go to the one step that is waiting behind a level load")
    ap.add_argument("--brk-freeze", action="store_true",
                    help="freeze the VM (QMP `stop`) across a brk= step's dump and arming: the guest runs during a "
                         "pmemsave, so without this the pattern is searched in a state the CPU has left and the "
                         "execution being looked for can start before a breakpoint is in")
    ap.add_argument("--linger", type=float, default=0,
                    help="after the last step, keep the guest running this many seconds for a memory read "
                         "(xemu is killed on exit, so a read has to happen while this sleeps)")
    args = ap.parse_args()

    REGRAB_SECONDS = args.regrab
    TOOL_DEADLINE = args.deadline
    PAD_HOLD = args.pad_hold
    BASE_THRESHOLD = args.threshold
    # A brk= wait can stop being a wait at all: M00 opens on an intro card ("Press A") with the guest frozen on it, so
    # every pattern armed before that card is armed for a load that is not running. The taps are sent from inside the
    # wait loop, because a step list is sequential and no pad= step can run while a brk= step is waiting.
    BRK_TAPS = []
    for spec in args.brk_tap or []:
        secs, _, button = spec.partition(":")
        try:
            BRK_TAPS.append((float(secs), (button.strip() or "a")))
        except ValueError:
            raise SystemExit("--brk-tap=%r is not SECS:BUTTON" % spec)
    BRK_TAPS.sort()
    BRK_KINDS = []
    for item in str(args.brk_kind).split(","):
        item = item.strip()
        if item not in ("read", "exec"):
            raise SystemExit("--brk-kind=%r is not read or exec" % args.brk_kind)
        BRK_KINDS.append(item)
    BRK_WAITS = []
    for item in str(args.brk_wait).split(","):
        try:
            BRK_WAITS.append(float(item))
        except ValueError:
            raise SystemExit("--brk-wait=%r is not a number or a comma list of them" % args.brk_wait)
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
    if args.eeprom and not os.path.exists(args.eeprom):
        # A missing path used to fall through to the shared image, which carries no profile: `Continue` then opens
        # SELECT PROFILE, the ten d-pad presses land in a screen with nothing to step, and the run reports "still the
        # menu" -- measured on sink5/sink6, whose `$W/smoke1/eeprom.bin` did not exist (the profile image is under
        # `$W/xemu/smoke1/`) while the same boot with the right path opened the map list on the first press.
        say("FAIL: --eeprom %s does not exist" % args.eeprom)
        say("      (the fallback is ~/.local/share/xemu/xemu/eeprom.bin, which has no profile: `Continue` opens "
            "SELECT PROFILE and every later press is wasted)")
        return 3
    if not os.path.exists(eeprom) and os.path.exists(shared_eeprom):
        shutil.copyfile(shared_eeprom, eeprom)
    if os.path.exists(eeprom):
        # Name the profile image in the log: two boots that differ only here look identical from the outside.
        say("eeprom: %s md5 %s" % (os.path.basename(shared_eeprom), eeprom_digest(eeprom)))
    want_pad = args.vpad or bool(BRK_TAPS) or any(name == "pad" for name, _ in steps)
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
    ask = None          # the guest probe the stall report uses, once QMP is up
    window = None
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
        STATE["window"] = window
        say("window: %s" % window)
        prior_focus, prior_name = focus_owner()
        if prior_focus and prior_focus != window:
            say("note: X input focus is %s (%s), not the emulator -- XTEST steps will refuse rather than type there"
                % (prior_name, prior_focus))

        def ask_guest():
            """One question for the stall report: is the guest executing, and where is it?"""
            status = cmd("query-status")
            regs = cmd("human-monitor-command", {"command-line": "info registers"})
            module = qmp.get("module")
            match = module.EIP_RE.search((regs or {}).get("return", "") if regs else "") if module else None
            return "vm %s, EIP %s" % ((status or {}).get("return", {}).get("status", "?"),
                                      "0x%08x" % int(match.group(1), 16) if match else "?")

        ask = ask_guest
        failures, shots, previous, measured_noise = [], 0, None, False
        brk_seen = 0
        for index, (name, value) in enumerate(steps, 1):
            # A guest that exits mid-sequence used to be discovered at the capture *after* the step that lost it, which
            # reads as an input the guest ignored. Asking before every step names the step it happened on.
            STATE["step"] = "step %d (%s=%s)" % (index, name, value)
            STATE["index"] = index
            if proc.poll() is not None:
                return guest_gone(proc, args.out, ask)
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
                    # Twice the floor *this screen* animates by. Keeping the largest floor of the whole run made a
                    # title's 18,343 px poison every gameplay shot after it: six commands that all executed were
                    # reported as 18 FAILs, because in-game animation is 682-754 px per 3 s. `--threshold` stays a
                    # floor, so an explicit value is still honoured.
                    args.threshold = max(BASE_THRESHOLD, 2 * floor)
                    say("         threshold set to %d (twice this screen's noise floor)" % args.threshold)
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
                # But a needle that *requires* the terminator is a needle a repeated key invalidates: measured
                # 2026-11-06, a stuck `t` grew TypedStr to ` histttt…` (Count 55, then 104 in the same boot) and
                # `hist\0` matched 0 copies, so the step printed "0 copies" and never armed -- and `--watch-key`
                # never fired, which reads as "the console ignored the submit". Fall back to the bare text: a copy
                # that begins with it is still resolved to its FString field by `string_copies`, which walks to the
                # string's first character and counts to its own terminator.
                needle = value.encode("utf-16-le") + b"\x00\x00"
                form = "terminated"
                hits, at = [], blob.find(needle)
                while at >= 0 and len(hits) < 16:
                    hits.append(at)
                    at = blob.find(needle, at + 1)
                if not hits:
                    needle, form = value.encode("utf-16-le"), "as a prefix of a longer string"
                    at = blob.find(needle)
                    while at >= 0 and len(hits) < 16:
                        hits.append(at)
                        at = blob.find(needle, at + 1)
                say("step %d: watch %r (%s) -- %s holds %d byte(s), %d%s cop%s of the UTF-16 text%s"
                    % (index, value, form, os.path.basename(ram), len(blob), len(hits),
                       "+" if len(hits) == 16 else "", "y" if len(hits) == 1 else "ies",
                       "" if not hits else " (%s)" % ", ".join("%#010x" % hit for hit in hits)))
                if not hits:
                    continue
                # `pmemsave` writes *physical* memory and the stub watches *linear* addresses: the page tables in
                # the same dump translate one into the other (CR3 comes from the register file, one QMP call).
                try:
                    registers = cmd("human-monitor-command", {"command-line": "info registers"})
                except Exception:
                    registers = None
                match = CR3.search((registers or {}).get("return", "") or "")
                if not match:
                    say("         no CR3 in `info registers`: cannot translate %#010x to a linear address"
                        % hits[0])
                    continue
                cr3 = int(match.group(1), 16)
                vamap = load_vamap()
                ramscan = load_ramscan()
                # Several copies of a short word exist (a class name, an ini key), and arming on the wrong one is
                # "8 hits, no stop". `string_copies` picks the copy an object holds (an FString field: a pointer to
                # the first character, then Count/Max) and names that object by its vtable.
                copies = ramscan.string_copies(
                    blob, needle, ramscan.class_names(), limit=16, depth=0x1000,
                    addr_for=lambda phys: [at for at, _how, _flags in vamap.linear_for(blob, cr3, phys)])
                say("         CR3 %#x: %d cop%s, %d with an FString field and %d with a named owner"
                    % (cr3, len(copies), "y" if len(copies) == 1 else "ies",
                       sum(1 for copy in copies if copy["field"]),
                       sum(1 for copy in copies if copy["owner"])))
                for copy in copies:
                    where = ", linear " + "/".join("%#010x" % at for at in copy["addresses"]) \
                        if copy["addresses"] else ", no linear address"
                    if copy["field"]:
                        pos, address, count, capacity = copy["field"]
                        where += "; field at %#010x -> %#010x, Count/Max %d/%d" % (pos, address, count, capacity)
                        if copy["owner"]:
                            base, back, name = copy["owner"]
                            where += ", %s at %#010x +%#x" % (name, base, back)
                    else:
                        where += "; no FString field names it"
                    say("           %#010x (%d char(s), match +%#x)%s"
                        % (copy["phys"], copy["chars"], copy["match"] - copy["phys"], where))
                # The console's own field wins: that is the buffer a keystroke appends to and a submit reads.
                ordered = sorted(copies, key=lambda copy: (
                    not (copy["owner"] and "console" in copy["owner"][2].lower()),
                    not copy["field"], not copy["addresses"]))
                owner = ordered[0] if ordered and ordered[0]["addresses"] else None
                if owner is None:
                    say("         CR3 %#x maps no linear address onto any copy: nothing to watch" % cr3)
                    continue
                if not owner["field"]:
                    say("         no copy has an FString field: arming on %#010x, the lowest-address copy"
                        % owner["phys"])
                watched = owner["addresses"][0]
                memory = load_mem()

                def trigger(_key=args.watch_key):
                    """Send the key that makes the guest read the buffer: an idle console reads nothing."""
                    if not _key:
                        return None
                    answer = cmd("human-monitor-command", {"command-line": "sendkey " + _key})
                    return "sendkey %s -> %r" % (_key, (answer or {}).get("return"))

                gdb = memory.Gdb("127.0.0.1", args.gdb)
                try:
                    first, supported, reply, records = memory.watchpoint(
                        gdb, watched, length=args.watch_length, window=args.watch_window,
                        stack=args.watch_stack, stops=args.watch_stops, timeout=args.watch_timeout,
                        trigger=trigger)
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
            if name == "brk":
                # A *script* breakpoint. The function's bytecode is a byte pattern in the copy the package loader made,
                # and the interpreter reads it in place -- so the pattern's address in a RAM dump is the address the
                # VM reads, and a watchpoint there answers "did this script function run" for a build whose natives
                # are already accounted for. Read, not execute: a Z0 on that address waits for a fetch that an
                # interpreted build never performs (docs/XEMU.md, measured 2026-11-07).
                if not args.gdb:
                    say("FAIL: a brk= step needs --gdb, so there is a stub to attach to")
                    return 3
                wanted = []
                for item in value.split(","):
                    label, _, hexes = item.partition("=")
                    label, hexes = label.strip(), hexes.strip()
                    try:
                        if not label or not hexes:
                            raise ValueError(item)
                        hexes = hexes.replace(" ", "").replace("0x", "")
                        # `??` is one wildcard byte. It is what the file and the guest differ in: the loader widens
                        # every compact index operand to a four-byte slot, so `uscript-dis.py --hex-bytes` emits `??`
                        # where an index was and the rest exact (docs/XEMU.md).
                        if "?" in hexes:
                            parts = []
                            i = 0
                            while i < len(hexes):
                                if hexes[i:i + 2] == "??":
                                    parts.append(b".")
                                    i += 2
                                else:
                                    parts.append(re.escape(bytes([int(hexes[i:i + 2], 16)])))
                                    i += 2
                            wanted.append((label, re.compile(b"".join(parts), re.DOTALL)))
                        else:
                            wanted.append((label, bytes.fromhex(hexes)))
                    except ValueError:
                        say("FAIL: brk=%r is not label=hex, where hex may carry ?? wildcards" % item)
                        return 3
                ram = os.path.join(args.out, "brk-ram.bin")
                # A dump of a *running* guest is a snapshot the guest has already moved past, and the code this step
                # is asking about runs during the load in front of it -- so freeze the VM across the dump and the
                # arming when asked, and the state searched is the state the CPU is in.
                if args.brk_freeze:
                    cmd("human-monitor-command", {"command-line": "stop"})
                got = cmd("human-monitor-command", {"command-line": 'pmemsave 0 0x8000000 "%s"' % ram})
                if got is None:
                    say("FAIL: no QMP socket at %s" % sock_path)
                    return 3
                with open(ram, "rb") as handle:
                    blob = handle.read()
                try:
                    registers = cmd("human-monitor-command", {"command-line": "info registers"})
                except Exception:
                    registers = None
                match = CR3.search((registers or {}).get("return", "") or "")
                if not match:
                    say("FAIL: no CR3 in `info registers`: a pattern has no linear address without the page tables")
                    return 3
                cr3 = int(match.group(1), 16)
                vamap = load_vamap()
                memory = load_mem()
                armed, absent = [], []
                for label, pattern in wanted:
                    if isinstance(pattern, re.Pattern):
                        hits = [m.start() for m in pattern.finditer(blob)][:8]
                        width = len(pattern.pattern)
                    else:
                        hits, at = [], blob.find(pattern)
                        while at >= 0 and len(hits) < 8:
                            hits.append(at)
                            at = blob.find(pattern, at + 1)
                        width = len(pattern)
                    lineas = []
                    for hit in hits:
                        for lin, _how, _flags in vamap.linear_for(blob, cr3, hit):
                            if lin not in lineas:
                                lineas.append(lin)
                    say("step %d: brk %s -- %d cop%s of %d byte(s) in %s (%d byte(s) dumped), %d with a linear address"
                        % (index, label, len(hits), "y" if len(hits) == 1 else "ies", width,
                           os.path.basename(ram), len(blob), len(lineas)))
                    if not hits:
                        # A name is a second, independent witness: the package's name table is resident whenever its
                        # functions are, so "the bytes are not here but the name is" and "the dump holds no package at
                        # all" are different failures, and a 0-copy search cannot tell them apart.
                        say("         the name %r appears %d time(s) as ASCII in the same dump"
                            % (label, blob.count(label.encode("ascii"))))
                    if not hits or not lineas:
                        absent.append((label, "not in the dump" if not hits else "no linear address"))
                        continue
                    for lin in sorted(lineas)[:max(1, args.brk_copies)]:
                        armed.append((label, lin))
                if not armed:
                    say("step %d: brk -- nothing to arm" % index)
                    continue
                gdb = memory.Gdb("127.0.0.1", args.gdb)
                kind = BRK_KINDS[min(brk_seen, len(BRK_KINDS) - 1)]
                fired, live, readers, stops = {}, [], {}, 0
                copies_read = {}
                try:
                    try:
                        gdb.stop(3.0)
                    except (IOError, OSError):
                        pass
                    try:
                        gdb.phys_mode(False)
                    except (IOError, OSError):
                        pass
                    for label, lin in armed:
                        reply = gdb.watch(lin, 1, kind)
                        if reply != b"OK":
                            say("         %s at %#010x: the stub refused %s (%r)"
                                % (label, lin, "Z3" if kind == "read" else "Z0", reply[:32]))
                            continue
                        live.append((label, lin))
                    brk_step_wait = BRK_WAITS[min(brk_seen, len(BRK_WAITS) - 1)]
                    brk_seen += 1
                    say("         %d %s armed: %s; running up to %.0fs"
                        % (len(live), "read watchpoint(s)" if kind == "read" else "breakpoint(s)",
                           " ".join("%s@%#x" % item for item in live), brk_step_wait))
                    if args.brk_freeze:
                        # The stub's breakpoints are in; let the guest go on from exactly the state that was searched.
                        cmd("human-monitor-command", {"command-line": "cont"})
                    # A tap is per brk= step only: a second step that re-finds the patterns after the load has nothing
                    # to press for, and a press in gameplay is a game action rather than a load being let through.
                    taps = list(BRK_TAPS) if brk_seen == args.brk_tap_step else []
                    started = time.time()
                    deadline = started + brk_step_wait
                    strays = set()
                    while live and time.time() < deadline:
                        # The card is a *wait for input*: with the guest frozen on it, every armed pattern sits behind
                        # a keypress, so a tap scheduled here is what lets the load reach the code under the
                        # breakpoints at all.
                        while taps and started + taps[0][0] <= time.time():
                            delay, button = taps.pop(0)
                            if PAD is None:
                                say("         brk-tap at %.1fs: this run has no pad" % delay)
                                continue
                            say("         brk-tap %s at %.1fs (%.0fs of the wait left)"
                                % (PAD.tap(button, PAD_HOLD), time.time() - started,
                                   max(0.0, deadline - time.time())))
                        wait_for = max(1.0, min(args.brk_timeout, deadline - time.time()))
                        if taps:
                            # Do not sleep past the next tap: wait_stop blocks, and a press delivered after the
                            # timeout the guest needs it inside is the same as no press.
                            wait_for = max(1.0, min(wait_for, started + taps[0][0] - time.time()))
                        gdb.resume()
                        try:
                            stop = gdb.wait_stop(wait_for)
                        except (IOError, OSError):
                            # A stub that answers nothing within the timeout has not disproved the breakpoint: keep
                            # waiting until the step's own deadline, so a stage that fires late is still seen.
                            say("         no stop within %.0fs, still waiting (%.0fs left)"
                                % (args.brk_timeout, max(0.0, deadline - time.time())))
                            continue
                        regs = gdb.registers()
                        eip = regs.get("eip")
                        if kind == "exec":
                            # Execution: the stop *is* the armed address, which is why this cannot see an interpreter.
                            hit = [item for item in live if eip is not None and item[1] == eip]
                        else:
                            # A read watchpoint stops at the reader, so the stub names the *address* it guards. One
                            # hit per label is the measurement, and the watchpoint is removed on it -- a function read
                            # every frame would otherwise stop the guest 60 times a second for the rest of the wait.
                            where = watch_address(stop)
                            hit = [(label, lin) for label, lin in live if where is not None and lin == where]
                        if not hit:
                            # A stray stop is not evidence that an armed pattern will not be read later, and one
                            # of these truncated a boot's whole measurement: report each distinct address once and
                            # keep waiting, with a cap so a stub that stops constantly cannot spin the loop.
                            where = watch_address(stop)
                            key = where if where is not None else (eip, stop[:16])
                            if key not in strays:
                                strays.add(key)
                                say("         a stop at eip %s matches no armed pattern (%r): still waiting"
                                    % ("%#010x" % eip if eip is not None else "?", stop[:40]))
                            if len(strays) > 8:
                                say("         %d unmatched stops: ending the wait" % len(strays))
                                break
                            continue
                        label, lin = hit[0]
                        if args.brk_keep:
                            # The watchpoint stays in: one read names a reader, a second read from another eip names
                            # the route. (A hardware read watchpoint does not retrigger on the instruction that just
                            # retired, so resuming is enough.)
                            readers.setdefault(label, []).append(eip)
                            stops += 1
                            copies_read[(label, lin)] = copies_read.get((label, lin), 0) + 1
                            say("         READ %s at %#010x, eip %#010x, t+%.1fs (stop %d of %d)"
                                % (label, lin, eip if eip is not None else 0, time.time() - started,
                                   stops, args.brk_stops))
                            if args.brk_keep_stops and copies_read[(label, lin)] >= args.brk_keep_stops:
                                # Retire this copy, not the step: an arm that runs every frame would otherwise
                                # spend the whole budget on one label and cut the wait inside the load window.
                                # The counter is per copy so a second copy of the same pattern keeps its own reads.
                                try:
                                    gdb.unwatch(lin, 1, kind)
                                except (IOError, OSError):
                                    pass
                                live[:] = [item for item in live if item != (label, lin)]
                                say("         %s at %#010x reached --brk-keep-stops=%d: unwatched, %d arm(s) left"
                                    % (label, lin, args.brk_keep_stops, len(live)))
                            if stops >= args.brk_stops:
                                say("         %d stop(s) taken: ending the wait" % stops)
                                break
                            if not live:
                                say("         every armed copy reached --brk-keep-stops: ending the wait")
                                break
                            continue
                        live.remove(hit[0])
                        try:
                            gdb.unwatch(lin, 1, kind)
                        except (IOError, OSError):
                            pass
                        fired[label] = regs
                        esp = regs.get("esp")
                        words = ""
                        if esp:
                            try:
                                words = " ".join("%08x" % word for word in
                                                 struct.unpack("<16I", gdb.read(esp, 0x40)))
                            except (IOError, OSError):
                                words = ""
                        say("         FIRED %s at %#010x, eip %#010x, t+%.1fs esp %s%s"
                            % (label, lin, eip, time.time() - started,
                               "%#010x" % esp if esp else "?", "; stack " + words if words else ""))
                except (IOError, OSError) as exc:
                    say("         the stub did not answer: %s" % exc)
                finally:
                    for label, lin in list(live):
                        try:
                            gdb.unwatch(lin, 1, kind)
                        except (IOError, OSError):
                            pass
                    try:
                        gdb.resume()
                    except (IOError, OSError):
                        pass
                    gdb.close()
                for label, _lin in armed:
                    if label in fired:
                        say("         %-28s fired (eip %#010x)" % (label, fired[label].get("eip", 0)))

                    elif not args.brk_keep:
                        # In keep mode a label that was read is not "fired" -- the read summary below is the result,
                        # and six contradicting negatives beside it are how this run's first log read.
                        say("         %-28s did not fire in %.0fs" % (label, brk_step_wait))
                for label, eips in readers.items():
                    seen = {}
                    for one in eips:
                        seen[one] = seen.get(one, 0) + 1
                    say("         %-28s read %d time(s) from %s"
                        % (label, len(eips), ", ".join("%#010x x%d" % (k or 0, v) for k, v in seen.items())))
                for label, why in absent:
                    say("         %-28s never armed: %s" % (label, why))
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
                    static = any(pattern in value for pattern in args.static_shot)
                    if static:
                        ok = diff < args.threshold
                        say("step %d: shot %s differing pixels: %d (expected static) %s"
                            % (index, value, diff, "PASS" if ok else "FAIL -- a static shot repainted"))
                        if not ok:
                            failures.append(value)
                        previous = path
                        shots += 1
                        continue
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
    except ToolHang as exc:
        # The one failure this harness could neither report nor survive: an external call that never returned.
        return stall_report(exc, args.out, proc=proc, ask=ask)
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
