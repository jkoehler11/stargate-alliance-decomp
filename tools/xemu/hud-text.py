#!/usr/bin/env python3
"""Read the three HUD text boxes' `DisplayText` off a live guest, and watch one change (PLAN 7.3, U15).

`StargateHUD.SetSubtitle` hands a `Say`/`TeamSay` string to `GameHud.DisplayTextBox(Text, Duration)`, and
`UITextBox.DisplayTextBox` writes it to `UIWidget.DisplayText` and -- only while
`Level.TimeSeconds <= StartFadeTime` (`FadeOffsetTime + Duration`, i.e. ~2 s for the subtitle) -- leaves it
there; `Update` calls `Hide(..., true)` past that and the field is cleared. So the assertion a `say` can make
is a read **inside two seconds of the submit**, and a dump taken after the run cannot make it at all: `msg1`
searched 128 MiB ten seconds late and found nothing to find.

What makes that read short is the route the earlier sessions measured:

* `StargatePlayer+0x1008` is `GameHudPtr` (`docs/XEMU.md`, `sink-slots.py`), and a gameplay row holds a live
  `GameHud` there -- so the object to read is one pointer, not a class-table search (`GameHud` is a script
  class and carries no table of its own).
* `GameHud` holds its three `UITextBox` objects at `+0x050`, `+0x054`, `+0x058` in declaration order
  (`DialogueWindow`, `NotificationWindow`, `InteractionWindow`), measured on four loaded-`M00` boots.
* `DisplayText` is an `FString` at `+0x394` (`UIWidget`), `(data, count, max)` with `count` including the
  terminating NUL.

Addresses are per boot. The row is found by the class pointer its own header carries (`StargatePlayer`'s
table is `0x00e47800`), newest-first from the end of `GObjObjects`, which is where a level's freshly spawned
rows sit; `--row` and `--hud` skip that walk when a previous read named them.

Usage, against a guest booted with `-gdb tcp::PORT` (`drive.py --gdb=PORT --linger=SECS` keeps one up):

    # resolve the row and the HUD, print the three boxes once
    python3 tools/xemu/hud-text.py --port 12612 --xbe $SG1_DUMP/default.xbe

    # the same boot, but watch: one line per change, for 120 s, while a console command is submitted
    python3 tools/xemu/hud-text.py --port 12612 --xbe $SG1_DUMP/default.xbe --wait 300 --watch 120

    # a row named by an earlier read (skips the walk)
    python3 tools/xemu/hud-text.py --port 12612 --row 0x08a4f800 --watch 60

`--wait SECS` polls the name table until the boot looks like a *level* (`GNames` 41,265 against 34,800 at the
menu) and only then resolves -- that is what lets a watcher start before the boot and still attach to the row
a level spawns. **A watcher started before the boot must also pass `--live`**: attaching to the stub interrupts
the CPU, and the default reader never resumes it, so the guest is frozen from the first poll (a blank frame and a
name table that never reaches a level's size, while every poll answers). `--live` makes each read a
stop/read/resume, which leaves the guest's own clock running -- the 2 s `DisplayText` window needs that.
`--follow SECS` re-reads the row's `+0x1008` while watching and rebinds when it holds a different `GameHud`:
a level destroys its intro HUD when the cinematic hands over to gameplay, so a read bound once reports three
empty boxes for a HUD that no longer exists.
`--selftest` builds a four-object guest in RAM: no emulator, no dump, and it runs in `make ci`.
Field contents are game state, so nothing here writes a file.
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES_PY = os.path.join(HERE, "names.py")
SINK_PY = os.path.join(HERE, "sink-slots.py")

HUD_SLOT = 0x1008                    # StargatePlayer.GameHudPtr (`sink-slots.py`, docs/XEMU.md)
PLAYER_CLASS = 0x00e47800            # `StargatePlayer`'s class table (package data: does not move per boot)
WINDOWS = ((0x050, "DialogueWindow"), (0x054, "NotificationWindow"), (0x058, "InteractionWindow"))
TEXT_OFF = 0x394                     # UIWidget.DisplayText, an FString (`census4`)
HUD_NAME = "GameHud"
MENU_OUTER = "MainMenu"              # the menu's player row: `rows_of_class` order puts it newest while a map loads
LEVEL_FLOOR = 40000                  # `sink-slots.py`: 34,800 names at the menu, 41,265 on a level -- but a
                                     # loading boot read 40,427, so this is a pre-gate and `MENU_OUTER` is the gate
OBJ_SIZE_MAX = 0x40000               # a text data block this large is not a message


def load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


names = load("xemu_names", NAMES_PY)
sink = load("xemu_sink", SINK_PY)


class LiveGuest(object):
    """A reader that leaves the guest **running**: stop, read, resume -- one swap per read.

    `objects.Guest` interrupts the CPU on connect and never resumes it. That is right for a dump taken after a
    run has finished and wrong for a watcher that starts before a boot: the guest it attaches to is frozen from
    that moment, so every frame is blank and `GNames` never grows past whatever the `.data` triple held when the
    attach landed (measured 2026-11-08/09: six `hud-say.sh` boots reported one frozen picture and a name table
    that never reached a level's size, while the same stub answered every poll -- the reader was the freeze).
    The stub serves `m` packets only while the CPU is stopped, so a live read is a stop/read/resume triple; the
    guest's own clock then advances between polls, which a 2 s `DisplayText` window needs.
    """

    def __init__(self, host=None, port=None, inner=None):
        self.inner = inner if inner is not None else names.Guest(host, port)
        self.stopped = True              # `objects.Guest` interrupts on connect, so the CPU starts stopped
        self.reads = 0
        self.resumes = 0

    def _stop(self):
        if not self.stopped:
            self.inner.g.stop(3.0)
            self.stopped = True

    def _resume(self):
        if self.stopped:
            self.inner.g.resume()
            self.stopped = False
            self.resumes += 1

    def read(self, addr, size, chunk=0x4000, attempts=None):
        self._stop()
        try:
            out = self.inner.read(addr, size, chunk=chunk, attempts=attempts)
        finally:
            self._resume()
        self.reads += 1
        return out

    def u32(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]

    def close(self):
        self.inner.close()


def u32(guest, addr):
    """One dword, or None: a hole in memory is a reading, not an exception to unwind a watch loop over."""
    try:
        raw = guest.read(addr, 4)
    except (IOError, OSError):
        return None
    if raw is None or len(raw) != 4:
        return None
    return struct.unpack("<I", raw)[0]


def fstring(guest, addr):
    """`(count, text)` for the `FString` at `addr`, or None when the triple does not read.

    `count` includes the terminating NUL -- `DisplayText` on a live `NotificationWindow` read `Count=45` for a
    44-character line -- so the text is `count - 1` units, and a `count` of 1 is the empty string. The data
    pointer is checked against the object window before it is followed: a text field that has never been
    written holds whatever the object's memory held, and following that as a length is a multi-megabyte read.
    """
    try:
        raw = guest.read(addr, 12)
    except (IOError, OSError):
        return None
    if raw is None or len(raw) != 12:
        return None
    data, count, cap = struct.unpack("<III", raw)
    if count == 0:
        return (0, "")
    if count > OBJ_SIZE_MAX or cap < count:
        return None
    if not names.plausible_object(data):
        return None
    try:
        raw = guest.read(data, 2 * count)
    except (IOError, OSError):
        return None
    if raw is None or len(raw) < 2 * count:
        return None
    text = raw[:2 * count].decode("utf-16-le", "replace")
    return (count, text.split("\x00", 1)[0])


def boxes(guest, hud, pages=None, nm=None):
    """[(label, window address, window name, count, text)] for the HUD's three text boxes."""
    out = []
    for off, label in WINDOWS:
        addr = u32(guest, hud + off)
        if not addr or not names.plausible_object(addr):
            out.append((label, 0, None, None, None))
            continue
        got = fstring(guest, addr + TEXT_OFF)
        oname = sink.object_name(pages, nm, addr) if (pages is not None and nm is not None) else None
        out.append((label, addr, oname, got[0] if got else None, got[1] if got else None))
    return out


def resolve(guest, args, out=print):
    """`(row, hud)` by walking the object array, or `(row, None)` when the HUD slot is not a `GameHud`."""
    data, num, _cap = sink.table(guest, args.at, "name table")
    nm = names.Names(guest, data, num)
    odata, onum, _omax = sink.table(guest, args.objects, "object array")
    pages = sink.Pages(guest, probe=data, deadline=args.deadline)
    rows = sink.rows_of_class(guest, pages, odata, onum, args.klass, tail=args.tail)
    if not rows:
        out("no row of class %#010x among the last %s of %d object(s): `--tail` is a shortcut, so this says "
            "where the row is not" % (args.klass, args.tail if args.tail else "all", onum))
        return None, None, pages, nm
    # A menu boot holds a `StargatePlayer` row too, outered to `MainMenu`, and it is the *newest* object while
    # the level is still loading -- this boot resolved `0x06b30000` (object 41,474 of 42,401, outer `MainMenu`)
    # 35 s after the name table crossed 40,000 names, and that row and its `GameHud` were destroyed when the
    # map finished loading (measured 2026-11-09). So the floor is a pre-gate and the row's own `Outer` is the
    # level gate: skip the menu's row instead of reading a HUD that is about to be torn down.
    outer = None
    for index, row in rows:
        outer = sink.object_name(pages, nm, pages.u32(row + 0x18)) or "?"
        if outer != MENU_OUTER:
            break
        out("row %#010x (object %d of %d) is outered to `%s`: the menu's own `StargatePlayer`, which the map "
            "load destroys, not a level's" % (row, index, onum, outer))
    else:
        out("all %d row(s) of class %#010x are outered to `%s`: no level's player row exists yet (a level "
            "spawns it when the map finishes loading)" % (len(rows), args.klass, outer))
        return None, None, pages, nm
    out("row %#010x (object %d of %d) class %s outer %s"
        % (row, index, onum, sink.class_name(pages, nm, row) or "?", outer))
    hud = pages.u32(row + HUD_SLOT)
    if hud is None or not hud:
        out("`+%#x GameHudPtr` is %s: `SetSubtitle` ends in a null, so a `say` has nowhere to draw"
            % (HUD_SLOT, "0" if hud == 0 else "unreadable"))
        return row, None, pages, nm
    if not names.plausible_object(hud):
        out("`+%#x GameHudPtr` is %#010x, which is not an object address" % (HUD_SLOT, hud))
        return row, None, pages, nm
    oname = sink.object_name(pages, nm, hud)
    if oname != HUD_NAME:
        out("`+%#x` holds %#010x named `%s`, not `GameHud`: the slot is a field only on a `StargatePlayer` row, "
            "and this row's chain is %s"
            % (HUD_SLOT, hud, oname or "?", " -> ".join(sink.chain_names(pages, nm,
               sink.super_chain(pages, pages.u32(row + sink.CLASS_OFF)))[:4])))
        return row, None, pages, nm
    out("`+%#x GameHudPtr` holds the live `GameHud` %#010x" % (HUD_SLOT, hud))
    return row, hud, pages, nm


def line(tag, elapsed, label, addr, count, text, name=None):
    shown = "" if text is None else text
    return "[%s %6.1fs] %-18s %#010x count %s %s%s" % (
        tag, elapsed, label, addr or 0, "?" if count is None else count, repr(shown),
        "  name `%s`" % name if name else "")


def slot_hud(guest, row, pages, nm, want=None):
    """`(address, name)` for the `GameHud` the row's `+0x1008` holds now, or `(None, None)`.

    The slot is the route, so it is what a rebind has to read: on `M00` the intro's `GameHud` is destroyed
    when the cinematic hands over to gameplay and the row's slot then holds a *different* `GameHud` object.
    A read bound once keeps reading the dead one -- all three window slots read `0` at 437.5 s, which was
    reported as a message that never arrived (measured 2026-11-09). Following the slot is the difference
    between "no box ever held the marker" and "the marker went to a HUD nobody was reading".
    """
    got = u32(guest, row + HUD_SLOT)
    if not got or not names.plausible_object(got):
        return None, None
    if want is not None and got == want:
        return got, None
    return got, sink.object_name(pages, nm, got)


def watch(guest, hud, pages, nm, seconds, interval, tag, started, out=print, row=None, follow=0.0, args=None,
          rewrites=None):
    """Poll the three boxes for `seconds` and print one line per change: `[(label, address, text)]`.

    The clock that bounds the watch starts **here**, not when the process did. A boot resolves its row and its
    `GameHud` minutes in (a 240 s title wait, a settle, a 20-85 s walk), so a guard on `started` expired before
    the first poll on every real boot -- the run printed a baseline and `watched 0s` while the commands it was
    started for were still being typed, which read as "nothing paints the boxes" (measured 2026-11-08).

    `follow` (seconds, 0 = off) re-reads `row + 0x1008` on that cadence: when the slot holds a different
    `GameHud` the watch rebinds and prints the new baseline, because a level replaces its intro HUD mid-run.
    A slot that is empty for three polls is not a message result either -- the row may have been replaced with
    it -- so the row is walked for again once (`args` carries the walk's parameters; `rewrites` caps it).
    """
    now = time.monotonic()
    state = {}
    for label, addr, name, count, text in boxes(guest, hud, pages, nm):
        state[label] = (addr, count, text)
        out(line(tag, now - started, label, addr, count, text, name))
    stop = now + seconds
    changes = rebounds = 0
    miss = 0
    rewrites = 1 if rewrites is None else rewrites
    tick = follow if (follow and row) else None
    next_follow = now + tick if tick else None
    while time.monotonic() < stop:
        time.sleep(interval)
        at = time.monotonic()
        if next_follow is not None and at >= next_follow:
            next_follow = at + tick
            got, oname = slot_hud(guest, row, pages, nm, want=hud)
            if got is None:
                miss += 1
                if miss >= 3 and rewrites > 0:
                    miss = 0
                    rewrites -= 1
                    out("[%s %6.1fs] follow          `+%#x` reads no object; walking for the level's own row "
                        "(%d walk(s) left)" % (tag, at - started, HUD_SLOT, rewrites + 1))
                    if args is not None:
                        row2, hud2, pages2, nm2 = resolve(guest, args,
                                                          out=lambda s: out("[%s %6.1fs] rewalk          %s"
                                                                            % (tag, time.monotonic() - started, s)))
                        if hud2:
                            row, hud, pages, nm = row2, hud2, pages2 or pages, nm2 or nm
                            rebounds += 1
                            state = {}
                            out("[%s %6.1fs] rebind          -> %#010x (row re-walked for it)"
                                % (tag, time.monotonic() - started, hud))
            elif oname is not None and oname != HUD_NAME:
                miss = 0
                out("[%s %6.1fs] follow          `+%#x` holds %#010x named `%s`, not a `GameHud`: keeping %#010x"
                    % (tag, at - started, HUD_SLOT, got, oname or "?", hud))
            elif oname == HUD_NAME:
                miss = 0
                out("[%s %6.1fs] rebind          %#010x -> %#010x (the row's `+%#x` holds a new `GameHud`)"
                    % (tag, at - started, hud, got, HUD_SLOT))
                hud, rebounds = got, rebounds + 1
                state = {}
            if hud != 0 and state == {}:
                for label, addr, name, count, text in boxes(guest, hud, pages, nm):
                    state[label] = (addr, count, text)
                    out(line(tag, at - started, label, addr, count, text, name))
        for label, addr, name, count, text in boxes(guest, hud, pages, nm):
            if (addr, count, text) != state.get(label):
                state[label] = (addr, count, text)
                changes += 1
                out(line(tag, time.monotonic() - started, label, addr, count, text, name))
    return changes, [(label, addr, text) for label, (addr, _count, text) in sorted(state.items())], rebounds


def selftest():
    """A four-object guest in RAM: the row, its `GameHud`, two text boxes, and a row whose slot is not one."""
    class FakeRAM(object):
        def __init__(self):
            self.buf = bytearray(0x2000000)
            self.reads = 0

        def read(self, addr, size, chunk=None, attempts=1):
            self.reads += 1
            if addr < 0 or addr + size > len(self.buf):
                raise IOError("unmapped %#x+%#x" % (addr, size))
            return bytes(self.buf[addr:addr + size])

        def put(self, addr, data):
            self.buf[addr:addr + len(data)] = data

        def u32(self, addr, value):
            self.put(addr, struct.pack("<I", value))

        def name(self, slot_ptr, index, text):
            self.u32(slot_ptr + 4 * index, slot_ptr + 0x10000 + 0x40 * index)
            at = slot_ptr + 0x10000 + 0x40 * index
            self.put(at, struct.pack("<III", index, 0, 0) + text.encode("utf-16-le") + b"\x00\x00")

    def build(slot_target, target_name):
        ram = FakeRAM()
        gnames, objs = 0x006be0d8, 0x006c21b0
        table, array = 0x01000000, 0x01200000
        ram.u32(gnames, table)
        ram.u32(gnames + 4, 40)
        ram.u32(gnames + 8, 64)
        for index, text in ((7, "StargatePlayer"), (8, "GameHud"), (9, target_name), (10, "M00")):
            ram.name(table, index, text)
        ram.u32(objs, array)
        ram.u32(objs + 4, 3)
        ram.u32(objs + 8, 8)
        row, hud, win = 0x01500000, 0x01510000, 0x01520000
        ram.u32(array, 0)
        ram.u32(array + 4, row)
        ram.u32(array + 8, hud)
        ram.u32(row + 0x18, 0x01530000)          # Outer, named `M00`
        ram.u32(row + names.CLASS_OFF, PLAYER_CLASS)
        ram.u32(row + names.NAME_OFF, 7)
        ram.u32(row + HUD_SLOT, slot_target)
        ram.u32(hud + 0x18, 0)
        ram.u32(hud + 0x24, 0x0099aa00)   # not a `StargatePlayer` row: only the row carries that table
        ram.u32(hud + names.NAME_OFF, 8)
        ram.u32(hud + 0x050, win)
        ram.u32(hud + 0x054, 0)
        ram.u32(hud + 0x058, 0)
        ram.u32(win + 0x24, 0x00bcea00)
        ram.u32(win + names.NAME_OFF, 9)
        text = "AB12CD"
        ram.put(0x01600000, (text + "\x00").encode("utf-16-le"))
        ram.u32(win + TEXT_OFF, 0x01600000)
        ram.u32(win + TEXT_OFF + 4, len(text) + 1)
        ram.u32(win + TEXT_OFF + 8, len(text) + 8)
        return ram, row, hud, win

    ram, row, hud, win = build(0x01510000, "UITextBox")
    args = argparse.Namespace(at=0x006be0d8, objects=0x006c21b0, klass=PLAYER_CLASS, tail=16, deadline=30.0)
    say = []
    got_row, got_hud, _pages, _nm = resolve(ram, args, out=say.append)
    assert got_row == row and got_hud == hud, (got_row, got_hud, say)
    got = boxes(ram, got_hud)
    assert got[0][:2] == ("DialogueWindow", win), got
    assert got[0][3] == 7 and got[0][4] == "AB12CD", got
    assert got[1][1] == 0 and got[1][4] is None, got
    # the same field read as a plain FString, so the count rule is asserted on its own
    assert fstring(ram, win + TEXT_OFF) == (7, "AB12CD")
    ram.put(0x01600000, "AB\x00".encode("utf-16-le"))
    ram.u32(win + TEXT_OFF + 4, 3)               # the NUL is inside the count, not text to show
    assert fstring(ram, win + TEXT_OFF) == (3, "AB")

    # a row whose +0x1008 names something that is not a GameHud is refused, not read as one
    ram2, row2, _hud2, _win2 = build(0x01530000, "Actor")
    said = []
    got_row, got_hud, _p, _n = resolve(ram2, args, out=said.append)
    assert got_row == row2 and got_hud is None, (got_row, got_hud)
    assert any("not `GameHud`" in s for s in said), said

    # a null slot is a fact about the route, and says so
    ram.u32(row + HUD_SLOT, 0)
    said = []
    got_row, got_hud, _p, _n = resolve(ram, args, out=said.append)
    assert got_hud is None and any("is 0" in s for s in said), said

    # The watch window is measured from when the watch starts, not from when the process did: a real boot
    # resolves its level and its `GameHud` minutes in, so a guard on the process clock (`started`) expired
    # before the first poll and every run reported a baseline and `watched 0s` while its commands were still
    # being typed. This test starts the watch as if 600 s had already gone by and lands a subtitle on the
    # fifth poll, so the early-exit path fails it.
    ram, _r3, hud, win = build(0x01510000, "UITextBox")
    orig = ram.read
    polls = {"n": 0}

    def late(addr, size, chunk=None, attempts=1):
        if addr == win + TEXT_OFF:
            polls["n"] += 1
            if polls["n"] == 5:
                ram.put(0x01700000, "LATER1\x00".encode("utf-16-le"))
                ram.u32(win + TEXT_OFF, 0x01700000)
                ram.u32(win + TEXT_OFF + 4, 7)
        return orig(addr, size)

    ram.read = late
    said = []
    changes, final, _rb = watch(ram, hud, None, None, 0.8, 0.05, "t", time.monotonic() - 600, out=said.append)
    assert changes >= 1 and any("LATER1" in s for s in said), (changes, said)
    assert dict((label, text) for label, _a, text in final)["DialogueWindow"] == "LATER1", final

    # The intro's HUD is destroyed mid-run and the row's slot then holds a different `GameHud` (measured on
    # `M00`: all three windows read 0 at 437.5 s while the row lived on). A watch bound once reports that as
    # three empty boxes for the rest of the run, which is exactly the `ABSENT` this test must not allow.
    class FakePages(object):
        def __init__(self, ram):
            self.ram = ram

        def u32(self, addr):
            try:
                return struct.unpack("<I", self.ram.read(addr, 4))[0]
            except (IOError, OSError):
                return None

    table = {7: "StargatePlayer", 8: "GameHud", 9: "UITextBox"}
    ram, row, hud, win = build(0x01510000, "UITextBox")
    hud2, win2 = 0x01540000, 0x01550000
    ram.u32(hud2 + 0x18, 0)
    ram.u32(hud2 + names.CLASS_OFF, 0x0099aa00)
    ram.u32(hud2 + names.NAME_OFF, 8)
    ram.u32(hud2 + 0x050, win2)
    ram.u32(hud2 + 0x054, 0)
    ram.u32(hud2 + 0x058, 0)
    ram.u32(win2 + names.CLASS_OFF, 0x00bcea00)
    ram.u32(win2 + names.NAME_OFF, 9)
    ram.put(0x01780000, "AFTER1\x00".encode("utf-16-le"))
    ram.u32(win2 + TEXT_OFF, 0x01780000)
    ram.u32(win2 + TEXT_OFF + 4, 7)
    ram.u32(win2 + TEXT_OFF + 8, 16)
    orig, seen = ram.read, {"n": 0}

    def swap(addr, size, chunk=None, attempts=1):
        if addr == row + HUD_SLOT and size == 4:
            seen["n"] += 1
            if seen["n"] == 2:                       # the hand-over, on the second follow tick
                ram.u32(row + HUD_SLOT, hud2)
                return struct.pack("<I", hud2)
        return orig(addr, size)

    ram.read = swap
    said = []
    _changes, final, rebounds = watch(ram, hud, FakePages(ram), table, 1.2, 0.05, "f", time.monotonic(),
                                      out=said.append, row=row, follow=0.1)
    assert rebounds == 1 and any("rebind" in s for s in said), (rebounds, said)
    assert dict((label, text) for label, _a, text in final)["DialogueWindow"] == "AFTER1", final
    assert any("AFTER1" in s for s in said), said

    # A menu boot holds a `StargatePlayer` row too, outered to `MainMenu`, and it is the *newest* object while a
    # map loads. Resolving it reads a `GameHud` the map load destroys (measured 2026-11-09: row `0x06b30000`,
    # outer `MainMenu`, HUD `0x0682ab00`, three boxes empty 1 s into the watch). The outer is the level gate.
    ram4, level_row, hud4, _w4 = build(0x01510000, "UITextBox")
    array, menu_row, menu_hud = 0x01200000, 0x01560000, 0x01570000
    ram4.name(0x01000000, 11, "MainMenu")
    ram4.u32(level_row + 0x18, 0x01590000)          # the level row's own outer, `M00`
    ram4.u32(0x01590000 + names.NAME_OFF, 10)
    ram4.u32(array + 4, level_row)
    ram4.u32(array + 8, menu_row)
    ram4.u32(0x006c21b0 + 4, 3)
    ram4.u32(menu_row + 0x18, 0x01530000)
    ram4.u32(menu_row + names.CLASS_OFF, PLAYER_CLASS)
    ram4.u32(menu_row + names.NAME_OFF, 7)
    ram4.u32(menu_row + HUD_SLOT, menu_hud)
    ram4.u32(menu_hud + names.NAME_OFF, 8)
    ram4.u32(0x01530000 + names.NAME_OFF, 11)     # the row's outer, renamed `MainMenu`
    said = []
    got_row, got_hud, _p, _n = resolve(ram4, args, out=said.append)
    assert got_row == level_row and got_hud == hud4, (got_row, got_hud, said)
    assert any("MainMenu" in s for s in said), said
    print("ok   hud-text: the menu's own `StargatePlayer` row is skipped for the level's (outer `MainMenu`)")

    print("ok   hud-text: resolves the row and its `GameHud`, reads both boxes, and refuses a slot that is not "
          "one (%d stub read(s))" % ram.reads)
    print("ok   hud-text: the watch window is the watch's own clock -- a subtitle that lands after it starts "
          "is reported, %d change(s) on %d poll(s)" % (changes, polls["n"]))
    print("ok   hud-text: a watch follows the row's `+0x1008` when the level replaces its `GameHud` "
          "(%d rebind(s), and the marker on the new HUD is reported)" % rebounds)

    # A live reader must hand the CPU back after every read. The failure this guards: `objects.Guest`
    # interrupts on connect and never resumes, so a watcher started before a boot froze the guest it was
    # watching -- six boots of blank frames and a name table that never grew, while every poll answered.
    class FakeStub(object):
        def __init__(self):
            self.log = []

        def stop(self, timeout=3.0):
            self.log.append("stop")
            return b"T05"

        def resume(self):
            self.log.append("resume")

        def read(self, addr, size, chunk=0x4000, attempts=None):
            self.log.append("read")
            return struct.pack("<I", 0x1234)

    class FakeInner(object):
        def __init__(self):
            self.g = FakeStub()
            self.reads = 0

        def read(self, addr, size, chunk=0x4000, attempts=None):
            return self.g.read(addr, size)

        def close(self):
            pass

    live_inner = FakeInner()
    live = LiveGuest(inner=live_inner)
    live.read(0x006be0d8, 12)
    live.read(0x006be0d8, 12)
    want = ["read", "resume", "stop", "read", "resume"]
    assert live_inner.g.log == want, (live_inner.g.log, want)
    assert live.resumes == 2, live.resumes
    print("ok   hud-text: a live reader resumes the guest after every read (%d read(s), %d resume(s), "
          "stop/read/resume on each)" % (live.reads, live.resumes))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=None, help="xemu's GDB stub port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--at", type=lambda v: int(v, 0), default=0x006be0d8,
                    help="GNames (the triple is static; `sink-slots.py` reads it here too)")
    ap.add_argument("--objects", type=lambda v: int(v, 0), default=names.OBJ_ARRAY,
                    help="UObject::GObjObjects (the triple is static)")
    ap.add_argument("--class", dest="klass", type=lambda v: int(v, 0), default=PLAYER_CLASS,
                    help="the row class object to match (default `StargatePlayer`)")
    ap.add_argument("--tail", type=int, default=2000,
                    help="scan only the newest N object-array entries (default 2000; the row a level spawns "
                         "sits at the end). 0 walks the whole array")
    ap.add_argument("--row", type=lambda v: int(v, 0), default=None,
                    help="read this player row instead of walking the array")
    ap.add_argument("--hud", type=lambda v: int(v, 0), default=None,
                    help="read this `GameHud` instead of reading `--row + 0x1008`")
    ap.add_argument("--wait", type=float, default=0.0,
                    help="seconds to wait for the boot to look like a level before resolving (a watcher "
                         "started before the boot uses this)")
    ap.add_argument("--watch", type=float, default=0.0,
                    help="seconds to keep polling the three boxes, printing one line per change")
    ap.add_argument("--interval", type=float, default=0.2, help="poll interval while watching (default 0.2 s)")
    ap.add_argument("--follow", type=float, default=0.0,
                    help="while watching, re-read the row's `+0x1008` every SECS and rebind when it holds a "
                         "different `GameHud` (0 = off). A level replaces its intro HUD when the cinematic hands "
                         "over, and a read bound once reports three empty boxes for a HUD that is gone")
    ap.add_argument("--tag", default="hud", help="a label in the report, so two boots' logs cannot be confused")
    ap.add_argument("--tries", type=int, default=6,
                    help="resolve attempts, 5 s apart, while the row a level spawns is still appearing")
    ap.add_argument("--connect-wait", type=float, default=90.0,
                    help="seconds to wait for the stub to accept a connection (a watcher starts before the boot)")
    ap.add_argument("--deadline", type=float, default=300.0, help="seconds the resolve walk may take")
    ap.add_argument("--live", action="store_true",
                    help="leave the guest running between reads (stop/read/resume). Required when the watcher "
                         "starts before the boot: attaching alone interrupts the CPU, and a reader that never "
                         "resumes freezes the boot it is watching")
    ap.add_argument("--selftest", action="store_true", help="check the field walk and the text decode, no boot")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.port is None:
        ap.error("--port is required unless --selftest is given")

    started = time.monotonic()
    while True:
        try:
            guest = LiveGuest(args.host, args.port) if args.live else names.Guest(args.host, args.port)
            break
        except (IOError, OSError) as exc:
            if time.monotonic() - started > args.connect_wait:
                print("%s: no stub on %s:%d after %.0fs (%s): nothing was read"
                      % (args.tag, args.host, args.port, args.connect_wait, exc))
                return 3
            time.sleep(2.0)
    print("%s: stub up on %s:%d after %.1fs (%s)"
          % (args.tag, args.host, args.port, time.monotonic() - started,
             "live reader: the guest is resumed after every read" if args.live
             else "the guest is left stopped -- wrong for a watcher started before a boot, see --live"))
    row, hud = args.row, args.hud
    pages = nm = None
    if row is None:
        deadline = started + args.wait
        last = None
        fails = 0
        while True:
            try:
                data, num, cap = struct.unpack("<III", guest.read(args.at, 12))
                if names.plausible_pointer(data):
                    last = num
                if names.plausible_pointer(data) and num >= LEVEL_FLOOR:
                    print("%s: level-sized name table after %.1fs: %d names" % (args.tag, time.monotonic() - started, num))
                    break
            except (IOError, OSError):
                # A failed fetch is not a small name table: the stub answers, the guest starves it, and a run
                # that counted those as zero would report "the menu" for a boot that was never read at all.
                fails += 1
            if time.monotonic() > deadline:
                print("%s: no level within %.0fs (the name table still reads %s names, against %d on a level "
                      "and 34,800 at the menu; %d of the polls could not read it at all); nothing to read"
                      % (args.tag, args.wait, "unreadable" if last is None else "%d" % last, LEVEL_FLOOR, fails))
                return 3
            time.sleep(5.0)
        tries = 0
        while True:
            row, hud, pages, nm = resolve(guest, args, out=lambda s: print("%s: %s" % (args.tag, s)))
            if hud is not None:
                break
            tries += 1
            # A level spawns its own player row when the map finishes loading, which is **after** the name
            # table crosses its floor: this boot resolved a row outered to `MainMenu` at 316.9 s and the
            # level's row was still not among 46,169 objects at 343 s (measured 2026-11-09). So `--tries` caps
            # a run with no deadline, and a `--wait` run keeps walking until the budget is spent instead of
            # giving up 60 s after the first row appeared.
            deadline = started + args.wait if args.wait > 0 else None
            if deadline is None and tries >= args.tries:
                print("%s: %d resolve attempt(s) and no live `GameHud`; the slot is what `say` would end in, "
                      "so there is nothing to watch" % (args.tag, tries))
                return 2
            if deadline is not None and time.monotonic() > deadline:
                print("%s: %d resolve attempt(s) and no level's `GameHud` by the deadline: every row found was "
                      "the menu's, so nothing was read (a boot that never loaded a level)" % (args.tag, tries))
                return 2
            time.sleep(10.0)
    elif hud is None:
        got = u32(guest, row + HUD_SLOT)
        if not got:
            print("%s: `+%#x` on row %#010x is %s" % (args.tag, HUD_SLOT, row, got))
            return 2
        hud = got
        print("%s: row %#010x -> GameHud %#010x" % (args.tag, row, hud))

    now = time.monotonic()
    if not args.watch:
        for label, addr, name, count, text in boxes(guest, hud, pages, nm):
            print(line(args.tag, now - started, label, addr, count, text, name))
        return 0
    changes, _final, rebounds = watch(guest, hud, pages, nm, args.watch, args.interval, args.tag, started,
                                      row=row, follow=args.follow, args=args, rewrites=8)
    print("%s: watched %.0fs, %d change(s), %d rebind(s), %d stub read(s)"
          % (args.tag, args.watch, changes, rebounds,
             guest.reads if hasattr(guest, "reads") else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
