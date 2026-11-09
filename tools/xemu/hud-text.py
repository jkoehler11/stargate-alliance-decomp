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
`--receipt MARKS` answers the question a box cannot: after the watch it reads the console's own `History[16]` and
prints, per marker, `RECEIPT <mark> IN HISTORY` or `NOT IN HISTORY`. A delivered line with an empty box is the
sink drawing nothing; an empty history is a keystroke that never arrived -- two failures that otherwise read
alike. The console is found from a `Client`'s own `InteractionMaster` (`--console-client`, `+0xc0`) or from the
live HUD's `PlayerConsole` field (`+0x398`, `inventory/hud_console_link.csv`) in one read, and by a whole-array
walk when neither names it (`--receipt-walk` forces the walk). The walk screens both header dwords -- a class
*table* is an object's first dword -- because keying `+0x24` alone matched nothing on a live level (`hudsay3`).
`--receipt-wait SECS` keeps polling the located object, because the commands are typed near
the end of the run.
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
RAMSCAN_PY = os.path.join(HERE, "ramscan.py")

HUD_SLOT = 0x1008                    # StargatePlayer.GameHudPtr (`sink-slots.py`, docs/XEMU.md)
PLAYER_CLASS = 0x00e47800            # `StargatePlayer`'s class table (package data: does not move per boot)
WINDOWS = ((0x050, "DialogueWindow"), (0x054, "NotificationWindow"), (0x058, "InteractionWindow"))
TEXT_OFF = 0x394                     # UIWidget.DisplayText, an FString (`census4`)
HUD_NAME = "GameHud"
MENU_OUTER = "MainMenu"              # the menu's player row: `rows_of_class` order puts it newest while a map loads
LEVEL_FLOOR = 40000                  # `sink-slots.py`: 34,800 names at the menu, 41,265 on a level -- but a
                                     # loading boot read 40,427, so this is a pre-gate and `MENU_OUTER` is the gate
OBJ_SIZE_MAX = 0x40000               # a text data block this large is not a message
# `Console`'s own counters and inline strings (`Console.uc:8-16`, measured 2026-11-06 on the receipt boot):
# `TypedStr` at +0x44, `History[16]` as sixteen 12-byte `FString`s from +0x50 to +0x110 (where `TypedStrPos`
# reads), `HistoryTop` at +0x38 and `bTyping` at +0x114.
CONSOLE_TYPED = 0x44
CONSOLE_HISTORY = 0x50
CONSOLE_STRIDE = 12
CONSOLE_SLOTS = 16
CONSOLE_TOP = 0x38
CONSOLE_B_TYPING = 0x114
CONSOLE_CLASS_NAME = "Console"        # `inventory/xbe_class_table_names.csv`: table `0x0050cb80`, size 1088
CONSOLE_TABLE = 0x0050cb80            # that table: a `.rdata` vtable, which is an object's **first** dword
CONSOLE_UCLASS = 0x00d48a00           # the `Console` `UClass` object (`ghud1` census: `275,0x00d48a00,Class,Console`),
                                      # which is what `+0x24` holds -- identities for the selftest, not a route
# `Engine/HUD.uc:27` declares one `Console`-typed field, `PlayerConsole`, and it sits at `+0x398` in every HUD
# subclass (`inventory/hud_console_link.csv`: a whole-RAM dump read the live `StargateHUD`'s field as the console
# `console-state.py` had typed into, `bTyping 1`). Base-class fields come first in UE2, so a `GameHud` subclass
# carries the field at the same offset -- which is one read where the object-array walk is 347 page reads.
CONSOLE_FIELD = 0x398
# The console is not a field of the player row -- `plr3` held no pointer on the `Console` table in its first
# 0x1400 bytes -- so the route is one hop out: `InteractionMaster` sits at `+0xc0` and that object's own fields
# name `XboxClient`, `GUIController`, `Console` and `UIXInteraction` (`docs/XEMU.md`). **The slot is `Client`'s,
# not the level row's**: it was measured on the `XboxClient` object `0x01fb9bc0` (`plr1`), which `ghud1`'s census
# names at the same address on a second level boot, while a level's own row holds null there (`hudsay3`:
# `0x08822800+0xc0`). A viewport-created console is registered as an interaction, which is why nothing names it
# directly; `--console-client` is the object this route reads.
INTERACTION_MASTER = 0x0c0
IM_BLOCK = 0x100                     # 64 dword slots screened, one 4-byte read each: cheaper than 347 page reads


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


def console_table():
    """`Console`'s class table from the disc census, or `(None, candidates)`.

    `inventory/xbe_class_table_names.csv` names 492 `.rdata` vtables from their registration evidence, and one
    of them is `Console` (object size 1088). A class table is package data, so the address is the same on every
    boot -- which is what makes the console reachable without the full object-array walk a name lookup needs.
    """
    tables = load("xemu_ramscan", RAMSCAN_PY).class_names()
    want = CONSOLE_CLASS_NAME.lower()
    hits = sorted(table for table, name in tables.items() if (name or "").split(".")[-1].lower() == want)
    return (hits[0] if len(hits) == 1 else None), hits


OUTER_OFF = 0x18                         # UObject::Outer (`hud-text.py`'s row gate reads `M00` there)


def class_table(name):
    """The one `.rdata` class table the disc census calls `name`, or `None` when it names none/several."""
    tables = load("xemu_ramscan", RAMSCAN_PY).class_names()
    want = name.lower()
    hits = sorted(table for table, got in tables.items() if (got or "").split(".")[-1].lower() == want)
    return hits[0] if len(hits) == 1 else None


def is_class_default(pages, base, pkg_table):
    """`(True, outer)` when the object at `base` is a *class default object*, else `(False, outer)`.

    A CDO is allocated by the package loader while a class is deserialised, and UE2 sets its `Outer` to the
    `Package` it came from; a live object's outer is an actor, a viewport or a controller. That is a
    structural test, so it needs no name resolution: the outer's own first dword is its class table.

    This matters because a CDO's fields are the shipped `defaultproperties` -- `Console`'s default is
    `HistoryTop 0`, `History` empty and `TypedStr` empty -- so a receipt that read one answered
    `NOT IN HISTORY` about the default rather than about the keys (measured 2026-11-09, `hudsay6`: the
    client route named `0x02344b00`, the address two earlier boots recorded as the class default, after two
    submits whose frame showed no prompt either).
    """
    outer = pages.u32(base + OUTER_OFF)
    if not outer or pkg_table is None or not names.plausible_object(outer):
        return False, outer
    return holds_class(pages, outer, pkg_table)[0], outer


def console_slots(guest, base):
    """[(index, text)] for the populated `History[16]` slots of the console object at `base`."""
    out = []
    for index in range(CONSOLE_SLOTS):
        got = fstring(guest, base + CONSOLE_HISTORY + index * CONSOLE_STRIDE)
        if got and got[1]:
            out.append((index, got[1]))
    return out


def holds_class(pages, base, table):
    """`(True, offset)` when the object at `base` opens with `table`, else `(False, None)`.

    Two offsets name a class in this project's evidence: `UObject::Class` at `+0x24`, which is the walk's own
    test (`names.py --verify`), and the object's first dword, which is what `hud-link.py` screened a whole-RAM
    dump by. Both are accepted so a screen never calls a real console "another class".
    """
    for off in (sink.CLASS_OFF, 0x00):
        if pages.u32(base + off) == table:
            return True, off
    return False, None


def console_from_hud(guest, pages, hud, table):
    """`(base, why)` for the `Console` the live HUD holds, or `(None, reason)`.

    `Engine/HUD.uc:424` draws the prompt only when `(PlayerConsole != None) && PlayerConsole.bTyping`, and
    `HUD.uc:27` declares that one `Console`-typed field. `inventory/hud_console_link.csv` measured where it sits
    (`+0x398`) on a whole-RAM dump, where the field named the very console `console-state.py` had typed into.
    UE2 lays base-class fields out first, so every HUD subclass -- `StargateHUD`, `GoauldConsoleHud`, `GameHud` --
    carries the field at the same offset, and one read beats the 347 page reads of a whole-array walk.

    The pointer is screened by class (an object's first dword is its class table in this build), so a null, a
    stale address or an object of another class comes back as a reason instead of a console.
    """
    value = pages.u32(hud + CONSOLE_FIELD)
    if not value:
        return None, "holds a null `PlayerConsole`"
    if not names.plausible_object(value):
        return None, "holds %#010x, which is not an object address" % value
    ok, _off = holds_class(pages, value, table)
    if not ok:
        got = pages.u32(value + sink.CLASS_OFF)
        return None, ("names %#010x, whose class is %s, not `Console` (%#010x)"
                      % (value, "unreadable" if got is None else "%#010x" % got, table))
    return value, "via the HUD's own `PlayerConsole` field (+%#x)" % CONSOLE_FIELD


def master_slots(pages, nm, master, table, pkg_table, block=IM_BLOCK):
    """`([(offset, target, class, name, is_default, on_console)], screened)` for `master`'s block.

    One 4-byte read per dword, then four per pointer that lands in the object window. This is a *census* and
    not a first-match screen because the first match is the wrong answer on this build: `hudsay6`'s block named
    the `Console` class default (`0x02344b00`, outer a `Package`) at `+0x30`, and a route that stopped there
    read `defaultproperties` and called it the keys' verdict. Naming every pointer also makes a boot that finds
    no live console say what the block *does* hold, which is the difference between "the read has to move" and
    "the read has to move to the master's other slots".

    **A dword is a slot only when its target names a class** (measured 2026-11-09, `hudq1`): the block also
    holds the object's own text, and 21 of its 26 printed rows were UTF-16 fragment pairs (`0x007c0020` is
    `" |"`, `0x00410020` is `" A"`) whose targets resolve to no class because no object is there. Counting
    them as slots made a six-pointer block read as a twenty-six-pointer one, and every one of them was
    "classified" `class ? name ?`. `screened` is how many dwords the class test refused, so the census can say
    how much of the block it did not read as pointers instead of quietly dropping them.
    """
    rows = []
    screened = 0
    for off in range(0, block, 4):
        value = pages.u32(master + off)
        if not value or not names.plausible_object(value):
            continue
        cls = sink.class_name(pages, nm, value)
        if cls is None:                          # no class table at the target: text, not an object
            screened += 1
            continue
        default, _outer = is_class_default(pages, value, pkg_table)
        rows.append((off, value, cls,
                     sink.object_name(pages, nm, value), default,
                     holds_class(pages, value, table)[0]))
    return rows, screened


def consoles_from_client(pages, nm, client, table, pkg_table):
    """`(bases, why, rows, screened)` for the `Console`s a `Client`'s `InteractionMaster` holds.

    A viewport-created console is registered as an interaction, so no actor names it: `plr3` held no pointer on
    the `Console` table in its first 0x1400 bytes, while the `XboxClient` object's `InteractionMaster` at
    `+0xc0` is the object whose own fields name `XboxClient`, `GUIController`, `Console` and `UIXInteraction`
    (`docs/XEMU.md`). **The slot is `Client`'s, not the level row's**, so this takes the client object and a
    level row is not screened at all: `hudsay3` read row `0x08822800` at `+0xc0` and got null, which is a fact
    about the offset, not about a console that never opened.

    **Every slot, and only a non-default counts** (measured 2026-11-09): the default object sits in this block,
    so a live target wins even when the default comes first, and `bases` is empty only when the block holds no
    console at all. `rows` is the census itself, which the receipt prints -- a boot that finds no live console
    then names what the block holds instead of only what it lacks.
    """
    master = pages.u32(client + INTERACTION_MASTER)
    if not master:
        return [], "client %#010x holds a null `InteractionMaster` (+%#x)" % (client, INTERACTION_MASTER), [], 0
    if not names.plausible_object(master):
        return [], ("client %#010x+%#x holds %#010x, which is not an object address"
                    % (client, INTERACTION_MASTER, master)), [], 0
    rows, screened = master_slots(pages, nm, master, table, pkg_table)
    bases = [value for _off, value, _cls, _name, default, on_console in rows
             if on_console and not default]
    why = ("via the client's `InteractionMaster` %#010x: %d slot(s) on the `Console` table, %d live"
           % (master, sum(1 for row in rows if row[5]), len(bases)))
    if not any(row[5] for row in rows):
        why += ", no pointer on the `Console` table in its first %#x bytes" % IM_BLOCK
    if screened:
        why += (", %d dword(s) in the block name no class (the object's own text)" % screened)
    return bases, why, rows, screened


def typing_text(guest, base):
    """The `bTyping` byte, and a refusal to call anything but 0/1 a bool.

    `hudq1` read **3** at `+0x114` on the object the client route reached, beside `HistoryTop 0` and an empty
    `TypedStr`. A `var bool` cannot hold 3, so either the object is not the class default the outer test says it
    is, or that byte packs more than one flag -- and reading it as a dword hid both readings behind one number.
    A byte plus the bits that are set is the reading the next boot can settle: `bTyping` alone is 1 while a
    console is typing (measured four times on three heaps), and `bIgnoreKeys` is the flag that swallows the
    first keypress, so 3 is either "typing with the first key still to come" or not this field at all.
    """
    raw = guest.read(base + CONSOLE_B_TYPING, 1)
    if raw is None or len(raw) != 1:
        return "unreadable"
    value = raw[0]
    if value in (0, 1):
        return "%d" % value
    bits = [str(bit) for bit in range(8) if value & (1 << bit)]
    return "0x%02x (not a bool: bit(s) %s set)" % (value, ",".join(bits))


def outer_text(pages, nm, base, pkg_table):
    """`outer %#010x class X` and, when the test fires, `(the class default)`.

    The class name is the measurement, not the verdict: on `hudq1` **every** named slot in the master's block
    answered "a `Package`: the class default", the block's own `+0x18` included, so that phrase described the
    block and not the object it was printed beside. A live object's outer is an actor, a viewport or a
    controller, and its class name says which.
    """
    outer = pages.u32(base + OUTER_OFF)
    cls = sink.class_name(pages, nm, outer) if outer else None
    default = is_class_default(pages, base, pkg_table)[0] if outer else False
    return ("outer %#010x class %s%s" % (outer, cls or "?",
                                         " (the class default)" if default else ""))


def trace_types(guest, args, base, seconds, client=None, interval=0.25, out=print):
    """Poll one console and the block that names it, printing every change.

    Four snapshots of `bTyping` need the four keys to land inside the four gaps, and a boot that types while the
    reader is elsewhere answers "0, 0, 0, 0" for a console that opened and closed. A trace needs no timing: it
    reads the byte every `interval` while `drive.py` presses the keys on its own clock, and prints a line only
    when something moved. The client's `+0x30` slot is polled too, because a console created by the keypress
    would replace the default in the block rather than change the default's byte.
    """
    master = u32(guest, client + INTERACTION_MASTER) if client is not None else 0
    start = time.monotonic()
    last = object()
    changes = 0
    while True:
        slot = u32(guest, master + 0x30) if master else None
        raw = guest.read(base + CONSOLE_B_TYPING, 1)
        byte = None if raw is None or len(raw) != 1 else raw[0]
        typed = fstring(guest, base + CONSOLE_TYPED)
        top = u32(guest, base + CONSOLE_TOP)
        moved = None
        if slot and slot != base:
            # A console the keypress created lives where the block points now, so its own byte is the reading.
            other = guest.read(slot + CONSOLE_B_TYPING, 1)
            moved = None if other is None or len(other) != 1 else other[0]
        row = (slot, byte, typed[1] if typed else None, top, moved)
        if row != last:
            changes += 1
            out("TRACE t=%.1f slot %s bTyping %s TypedStr %s HistoryTop %s%s"
                % (time.monotonic() - start, "?" if slot is None else "%#010x" % slot,
                   typing_text(guest, base), "" if not typed or not typed[1] else repr(typed[1]), top,
                   "" if slot in (None, base) else
                   " (the block named a different object, bTyping %s there)" % (
                       "?" if moved is None else "%d" % moved)))
            last = row
        if time.monotonic() - start >= seconds:
            break
        time.sleep(interval)
    out("TRACE %d change(s) over %.0f s on %#010x" % (changes, seconds, base))
    return changes


def receipt(guest, args, marks, out=print, wait=0.0, hud=None, row=None, client=None):
    """Read the console's own `History[16]` and say, per marker, whether its submit ran.

    A text box that never held the marker has two causes that look identical from the box: the keys never
    reached the console (a modal holding input, a press into an unfocused window, a dropped character), or the
    command ran and the sink it writes drew nothing. `Console.History` separates them: `Console.uc`'s `IK_Enter`
    branch copies `TypedStr` into `History[HistoryTop]` and only then calls `ConsoleCommand`, so a line in a slot
    is proof the submit ran, and an empty history after the typing window is proof the keys did not arrive.

    **Three routes, cheapest first.** A `Client` object's `InteractionMaster` (`+0xc0`, `--console-client`)
    names the console inside its field block within a few reads (`console_from_client`); the live HUD's
    `PlayerConsole` (`+0x398`) names it in one read, but only a HUD that has linked its console does;
    `--receipt-walk` skips both and is the control. The whole-array walk is the last resort and screens **both**
    header dwords (`sink.rows_holding`): the table `inventory/xbe_class_table_names.csv` names is a `.rdata`
    vtable, which is the object's *first* dword, while `+0x24` holds the `UClass` **object** -- so the walk this
    receipt used to run answered `[]` on an array that held the console (`hudsay3`: 48,646 entries, no `Console`
    row, on a boot whose `hud`/`say` frames repainted). The `--tail` shortcut (the newest 2,000 objects) cannot
    see it either: `xemu/ghud1/cls.csv` puts the only `Console` instance at **index 36,148 of 48,646**.
    `--receipt-tail 0` (the default) walks every entry.

    `wait` polls the located object -- not the array -- because the commands are typed near the end of a 500 s
    sequence and a receipt that starts early has to outlast them.
    """
    table, hits = console_table()
    if table is None:
        out("CONSOLE none: the census names %d `Console` class table(s)%s, so the walk has no key"
            % (len(hits), " (" + ", ".join("%#010x" % h for h in hits) + ")" if hits else ""))
        return {}
    data, num, _cap = sink.table(guest, args.at, "name table")
    odata, onum, _omax = sink.table(guest, args.objects, "object array")
    pages = sink.Pages(guest, probe=data, deadline=args.deadline)
    pkg_table = class_table("Package")
    # The name table is needed by every route now: the census names each slot's class and its outer's class, and
    # `typing_text`/`outer_text` are printed for a console found by the HUD field and by the array walk too.
    nm = names.Names(guest, data, num)
    window = args.receipt_tail or None          # 0 = walk the whole array, which is where the console is
    phrase = "the newest %d" % window if window else "any"
    route = None
    bases = []
    # Three routes, cheapest first: a `Client`'s `InteractionMaster` (two reads), the HUD's own `PlayerConsole`
    # (one read, but only a HUD that has linked its console names it), and the object-array walk (347 page
    # reads). `--receipt-walk` skips the first two, which is the control for a receipt that read one object.
    client = client if client is not None else getattr(args, "console_client", None)
    # A fixed object is the only route that costs nothing: `--console-at` is how the four-state read asks the
    # *same* console four times in one boot, and the answer is the byte, not a census.
    fixed = getattr(args, "console_at", None)
    if fixed is not None and not args.receipt_walk:
        bases, route, client = [fixed], "`--console-at` (one object, no route and no walk)", None
    if client is not None and not args.receipt_walk:
        # The census needs the name table: this is the one route that reads class and object names, and it is
        # the route that has to say *which* the block holds when none of them is a live console.
        found, why, rows, screened = consoles_from_client(pages, nm, client, table, pkg_table)
        master = pages.u32(client + INTERACTION_MASTER)
        for off, value, cls, got, default, on_console in rows:
            out("MASTER %#010x+%#04x -> %#010x class %s name %s, %s%s"
                % (master, off, value, cls, got or "?", outer_text(pages, nm, value, pkg_table),
                   ", on the `Console` table" if on_console else ""))
        out("MASTER %#010x: %d object pointer(s) in its first %#x bytes%s"
            % (master, len(rows), IM_BLOCK,
               "" if not screened else
               ", %d dword(s) that name no class (the block's own text, not slots)" % screened))
        if found:
            bases, route = found, why
        elif getattr(args, "no_walk", False):
            # A census is often the whole question (which object does the block hold?), and the 347-page walk
            # that follows it answers a different one 227 s later. `--no-walk` keeps the answer that exists.
            out("CONSOLE client: %s -- stopping, as `--no-walk` asks: no array walk" % why)
            return {}
        else:
            # Nothing live in the block: the verdict has to come from the array, so say what the block held
            # and walk -- never answer about the default object it may contain.
            out("CONSOLE client: %s -- falling back to the object-array walk" % why)
    elif row is not None and not args.receipt_walk:
        # The row's `+0xc0` is `Client`'s slot: reading it off a `StargatePlayer` row measures whatever occupies
        # the offset there (`hudsay3` got null), so the row is not screened and the reason says why.
        out("CONSOLE player: row %#010x is not screened for a console -- `+%#x` `InteractionMaster` is a `Client` "
            "slot (measured on `XboxClient`), so name that object with `--console-client`"
            % (row, INTERACTION_MASTER))
    if not bases and hud is not None and not args.receipt_walk:
        base, why = console_from_hud(guest, pages, hud, table)
        if base:
            bases, route = [base], why
        else:
            out("CONSOLE hud %#010x+%#x: %s -- falling back to the object-array walk"
                % (hud, CONSOLE_FIELD, why))
    stop = time.monotonic() + max(0.0, wait)
    seen = {}
    verdicts = {}
    walks = 0
    while True:
        if bases:
            live = [base for base in bases if holds_class(pages, base, table)[0]]
            if len(live) != len(bases):
                out("CONSOLE %d of %d object(s) no longer open with the `Console` class table: destroyed between "
                    "reads" % (len(bases) - len(live), len(bases)))
            bases = live
        if not bases and fixed is not None:
            # A fixed address that no longer opens with the class table is the answer (destroyed, or never a
            # console), not a reason to spend 347 page reads looking for a different object.
            out("CONSOLE --console-at %#010x: does not open with the `Console` table (%#010x) -- a wrong address "
                "or an object destroyed since; no walk" % (fixed, table))
            break
        if not bases:
            walks += 1
            found = sink.rows_holding(guest, pages, odata, onum, table, tail=window)
            if not found:
                out("CONSOLE none in %s of %d object(s) (walk %d): no object opens with the `Console` table "
                    "(%#010x) or carries it at `+0x24` -- a console that never opened, or one outside the "
                    "window; this says where it is not" % (phrase, onum, walks, table))
            bases = [base for _index, base in found]
            route = "object array (%s of %d)" % (phrase, onum)
        for base in bases:
            typed = fstring(guest, base + CONSOLE_TYPED)
            slots = console_slots(guest, base)
            if base not in seen:
                out("CONSOLE %#010x (%s): HistoryTop %s, bTyping %s, TypedStr %s, %d populated slot(s), %s"
                    % (base, route, u32(guest, base + CONSOLE_TOP), typing_text(guest, base),
                       "empty" if not typed or not typed[1] else repr(typed[1]), len(slots),
                       outer_text(pages, nm, base, pkg_table)))
            for slot, text in slots:
                if seen.get(base, {}).get(slot) != text:
                    out("HIST [%d] %#010x count %d %r"
                        % (slot, base + CONSOLE_HISTORY + slot * CONSOLE_STRIDE, len(text) + 1, text))
            seen.setdefault(base, {}).update(dict(slots))
            for mark in marks:
                hit = [slot for slot, text in slots if mark in text]
                if hit and mark not in verdicts:
                    verdicts[mark] = hit[0]
        if not marks or all(mark in verdicts for mark in marks):
            break
        if time.monotonic() >= stop:
            break
        time.sleep(2.0)
    out("RECEIPT read %d console object(s) by %s: %d array walk(s), %d page read(s), %.1f s"
        % (len(seen), route or "nothing", walks, pages.reads, pages.spent))
    for mark in marks:
        if mark in verdicts:
            out("RECEIPT %s IN HISTORY (%d walk(s), route %s): the submit ran, so the console's own history holds "
                "the line -- a box that never held it is the sink drawing nothing, not a lost keystroke"
                % (mark, walks, route or "nothing"))
        elif not seen:
            # `seen` is empty only when no walk ever read a `Console` object: `History` was not looked at, so a
            # verdict about the keys would be a verdict about the search window instead.
            out("RECEIPT %s UNMEASURED (%d walk(s), route %s, %s of %d object(s)): no `Console` object was read, "
                "so its `History` was never looked at -- this is where the search stopped, not what the keys did"
                % (mark, walks, route or "nothing", phrase, onum))
        elif seen and all(is_class_default(pages, base, pkg_table)[0] for base in seen):
            # `Console`'s default is `HistoryTop 0` with an empty `History`, so this object answers
            # `NOT IN HISTORY` whatever the keys did: the read never touched a live console.
            out("RECEIPT %s UNMEASURED (%d walk(s), route %s): every `Console` object read has a `Package` "
                "outer, so each is a class's default object and its `History` is the shipped default -- "
                "nothing here is about the keys" % (mark, walks, route or "nothing"))
        else:
            out("RECEIPT %s NOT IN HISTORY (%d walk(s), route %s): no submit ran, so the keys never reached the "
                "console -- the modal, the focus or a dropped character, not the sink"
                % (mark, walks, route or "nothing"))
    return verdicts


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
        for index, text in ((7, "StargatePlayer"), (8, "GameHud"), (9, target_name), (10, "M00"),
                            (12, "Console")):
            ram.name(table, index, text)
        # The `Console` class object's own name index: `sink.class_name` resolves an object's class through it, and
        # the census now refuses a target that names no class, so every fake guest needs it.
        ram.u32(CONSOLE_UCLASS + names.NAME_OFF, 12)
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
    args = argparse.Namespace(at=0x006be0d8, objects=0x006c21b0, klass=PLAYER_CLASS, tail=16, deadline=30.0,
                              receipt_tail=0, receipt_walk=False)
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

    # The receipt is the question a box cannot answer: a marker that a submit delivered but no box painted
    # (a sink drawing nothing) against one that never reached the console (keys into a modal). `Console.History`
    # separates them, and it is reachable from the census's `Console` class table rather than a name walk.
    assert console_table()[0] == 0x0050cb80, console_table()
    ram5, _row5, _hud5, _win5 = build(0x01510000, "UITextBox")
    array5, con = 0x01200000, 0x015a0000
    ram5.name(0x01000000, 12, "Console")
    ram5.u32(0x006c21b0 + 4, 4)
    ram5.u32(array5 + 0x0c, con)
    ram5.u32(con + 0x00, 0x0050cb80)              # a class *table* is the object's first dword (`hud-link.py`)
    ram5.u32(con + names.CLASS_OFF, CONSOLE_UCLASS)   # `+0x24` is the `UClass` *object*, not a table address
    ram5.u32(con + names.NAME_OFF, 12)
    ram5.u32(con + CONSOLE_TOP, 1)
    ram5.u32(con + CONSOLE_B_TYPING, 0)
    ram5.put(0x017a0000, "say sg1txt1\x00".encode("utf-16-le"))
    ram5.u32(con + CONSOLE_HISTORY, 0x017a0000)
    ram5.u32(con + CONSOLE_HISTORY + 4, 12)
    ram5.u32(con + CONSOLE_HISTORY + 8, 16)
    said = []
    got = receipt(ram5, args, ["sg1txt1", "sg1txt2"], out=said.append, wait=0.0)
    assert got == {"sg1txt1": 0}, (got, said)
    assert any("RECEIPT sg1txt1 IN HISTORY" in s for s in said), said
    assert any("RECEIPT sg1txt2 NOT IN HISTORY" in s for s in said), said
    assert any("HistoryTop 1" in s and "bTyping 0" in s for s in said), said
    # `bTyping` is read as a byte, and a value that no `var bool` can hold is reported as what it is: `hudq1`
    # read 3 at `+0x114` on the object the client route reached, beside `HistoryTop 0` and an empty `TypedStr`.
    ram5.put(con + CONSOLE_B_TYPING, b"\x03")
    said = []
    receipt(ram5, args, [], out=said.append, wait=0.0)
    assert any("bTyping 0x03 (not a bool: bit(s) 0,1 set)" in s for s in said), said
    ram5.u32(con + CONSOLE_B_TYPING, 0)
    # A console that never opened is a walk that found nothing, and it must not read as a lost keystroke.
    ram6, _r6, _h6, _w6 = build(0x01510000, "UITextBox")
    said = []
    assert receipt(ram6, args, ["sg1txt1"], out=said.append, wait=0.0) == {}, said
    assert any("CONSOLE none in any of" in s for s in said), said
    # A `Console` whose `Outer` is a `Package` is the class's default object: its `History` is the shipped
    # `defaultproperties`, so `NOT IN HISTORY` there would be a verdict about the default (measured 2026-11-09,
    # `hudsay6`: the client route named `0x02344b00` -- two earlier boots' class-default address -- and read
    # `HistoryTop 0` after two submits).
    pkg = class_table("Package")
    assert pkg, "the disc census names no `Package` class table"
    ram8, _r8, hud8, _w8 = build(0x01510000, "UITextBox")
    ram8.u32(con + 0x00, 0x0050cb80)
    ram8.u32(con + names.CLASS_OFF, CONSOLE_UCLASS)
    ram8.u32(con + OUTER_OFF, 0x01800000)         # Outer -> an object whose class table is `Package`
    ram8.u32(0x01800000, pkg)
    ram8.u32(0x01200000 + 4 * 3, con)             # a fourth object: the default
    ram8.u32(0x006c21b0 + 4, 4)
    said = []
    assert receipt(ram8, args, ["sg1txt1"], out=said.append, wait=0.0) == {}, said
    assert any("outer 0x01800000 class ? (the class default)" in s for s in said), said
    assert any("RECEIPT sg1txt1 UNMEASURED" in s and "class's default object" in s for s in said), said
    # An array where the console is *older* than a tail window: the shortcut the first live receipt used said
    # `NOT IN HISTORY` about its window, so the window is now the whole array by default -- and the HUD's own
    # `PlayerConsole` is one read where the walk is 347 page reads.
    ram7, row7, hud7, _w7 = build(0x01510000, "UITextBox")
    ram7.u32(0x006c21b0 + 4, 400)                 # 400 objects, the console at index 5
    ram7.u32(0x01200000 + 4 * 5, con)
    ram7.u32(con + 0x00, 0x0050cb80)
    ram7.u32(con + names.CLASS_OFF, CONSOLE_UCLASS)
    ram7.put(0x017a0000, "say sg1txt1\x00".encode("utf-16-le"))
    ram7.u32(con + CONSOLE_TOP, 1)
    ram7.u32(con + CONSOLE_HISTORY, 0x017a0000)
    ram7.u32(con + CONSOLE_HISTORY + 4, 12)
    ram7.u32(con + CONSOLE_HISTORY + 8, 16)
    args.receipt_tail = 100                       # newest 100 of 400: indices 300-399, so the console is outside it
    said = []
    got = receipt(ram7, args, ["sg1txt1"], out=said.append, wait=0.0)
    assert got == {} and any("UNMEASURED" in s for s in said), (got, said)
    args.receipt_tail = 0                         # the whole array finds it
    said = []
    got = receipt(ram7, args, ["sg1txt1"], out=said.append, wait=0.0)
    assert got == {"sg1txt1": 0}, (got, said)
    assert any("object array (any of 400)" in s for s in said), said
    # The cheap route: the HUD's `+0x398` names the console, and the receipt reads it in a walk-free pass.
    ram7.u32(hud7 + CONSOLE_FIELD, con)
    said = []
    got = receipt(ram7, args, ["sg1txt1"], out=said.append, wait=0.0, hud=hud7)
    assert got == {"sg1txt1": 0}, (got, said)
    assert any("PlayerConsole" in s and "0 array walk" in s for s in said), said
    # A HUD whose field is stale or of another class is a reason, not a console: it falls back to the walk.
    ram7.u32(hud7 + CONSOLE_FIELD, 0x01520000)
    said = []
    got = receipt(ram7, args, ["sg1txt1"], out=said.append, wait=0.0, hud=hud7)
    assert got == {"sg1txt1": 0}, (got, said)
    assert any("not `Console`" in s and "falling back" in s for s in said), said
    # The client route: the console is one hop from a `Client`'s `InteractionMaster` at `+0xc0`, and it is
    # *screened* out of that object's own field block rather than assumed at an offset -- the first live boot's
    # HUD field held a non-pointer (`0x00050975`).
    ram7.u32(hud7 + CONSOLE_FIELD, 0x01520000)     # the HUD route still wrong: the client route has to carry it
    master = 0x01540000
    client, client_master = 0x01540000, 0x01550000
    ram7.u32(client + INTERACTION_MASTER, master)
    ram7.u32(master + 0x08, con)
    said = []
    got = receipt(ram7, args, ["sg1txt1"], out=said.append, wait=0.0, hud=hud7, client=client)
    assert got == {"sg1txt1": 0}, (got, said)
    assert any("InteractionMaster" in s and "0 array walk" in s for s in said), said
    # A master whose block names no console is a reason, and the receipt still falls through to the HUD/walk.
    ram7.u32(master + 0x08, 0)
    ram7.u32(client + INTERACTION_MASTER, client_master)
    said = []
    got = receipt(ram7, args, ["sg1txt1"], out=said.append, wait=0.0, hud=None, client=client)
    assert got == {"sg1txt1": 0}, (got, said)
    assert any("no pointer on the `Console` table" in s for s in said), said
    # A master whose block holds the class default *and* a live console: `hudsay6`'s shape, where the first
    # match was the default. The live target wins, and both are named so the census is the evidence.
    ram9, _r9, _h9, _w9 = build(0x01510000, "UITextBox")
    c9, m9, live9 = 0x01540000, 0x01550000, 0x015b0000
    ram9.u32(c9 + INTERACTION_MASTER, m9)
    ram9.u32(m9 + 0x08, con)                      # first match: a `Console`-classed default object
    ram9.u32(con + 0x00, 0x0050cb80)
    ram9.u32(con + names.CLASS_OFF, CONSOLE_UCLASS)
    ram9.u32(con + OUTER_OFF, 0x01800000)
    ram9.u32(0x01800000, pkg)                     # its outer is a `Package`, so it is the default
    ram9.u32(m9 + 0x0c, live9)                    # second match: a live console
    ram9.u32(live9 + 0x00, 0x0050cb80)
    ram9.u32(live9 + names.CLASS_OFF, CONSOLE_UCLASS)
    ram9.u32(live9 + OUTER_OFF, 0x01510000)       # a live object's outer is not a `Package`
    ram9.u32(0x01200000 + 4 * 3, con)             # the array holds only the default: the census is the route
    ram9.u32(0x006c21b0 + 4, 4)
    said = []
    got = receipt(ram9, args, ["sg1txt1"], out=said.append, wait=0.0, client=c9)
    assert got == {}, (got, said)
    assert not any("UNMEASURED" in s for s in said), said
    assert any("2 slot(s) on the `Console` table, 1 live" in s for s in said), said
    assert any("-> 0x015a0000" in s and "outer 0x01800000 class ? (the class default)" in s for s in said), said
    assert any("-> 0x015b0000" in s and "class default" not in s for s in said), said
    assert any("2 object pointer(s)" in s and "name no class" not in s for s in said), said
    # A dword whose target names no class is the block's own text, not a slot: `hudq1` printed 21 of them as
    # `class ? name ?` beside six real pointers (`0x007c0020` is the UTF-16 pair `" |"`).
    ram9.u32(m9 + 0x10, 0x007c0020)
    said = []
    receipt(ram9, args, ["sg1txt1"], out=said.append, wait=0.0, client=c9)
    assert not any("-> 0x007c0020" in s for s in said), said
    assert any("1 dword(s) that name no class" in s for s in said), said
    # And a block that holds only the default is a reason to walk, never a `NOT IN HISTORY`.
    ram9.u32(m9 + 0x0c, 0)
    said = []
    got = receipt(ram9, args, ["sg1txt1"], out=said.append, wait=0.0, client=c9)
    assert any("0 live" in s and "falling back to the object-array walk" in s for s in said), said
    assert not any("RECEIPT sg1txt1 NOT IN HISTORY" in s for s in said), said

    # A client that names no master says so instead of reading through a null.
    ram7.u32(client + INTERACTION_MASTER, 0)
    said = []
    receipt(ram7, args, ["sg1txt1"], out=said.append, wait=0.0, hud=None, client=client)
    assert any("null `InteractionMaster`" in s for s in said), said
    # And a level *row* is not screened: `+0xc0` is `Client`'s slot, so a null there is a fact about the offset,
    # not about a console that never opened (`hudsay3`: row `0x08822800` held null while the game was repainting).
    ram7.u32(row7 + INTERACTION_MASTER, 0)
    said = []
    receipt(ram7, args, ["sg1txt1"], out=said.append, wait=0.0, hud=None, row=row7)
    assert any("is a `Client` slot" in s and "not screened" in s for s in said), said
    assert not any("null `InteractionMaster`" in s for s in said), said
    # The walk screens both header dwords: a class *table* is the object's first dword, so keying `+0x24` alone
    # answers `[]` on an array that holds the console -- the `hudsay3` negative, reproduced with no guest.
    pages7 = sink.Pages(ram7)
    assert sink.rows_of_class(ram7, pages7, 0x01200000, 400, CONSOLE_TABLE) == [], "the old route's negative"
    assert sink.rows_holding(ram7, pages7, 0x01200000, 400, CONSOLE_TABLE) == [(5, con)]
    print("ok   hud-text: a marker's submit is read off `Console.History` (delivered) and a console that never "
          "opened is a walk that found nothing, not a lost keystroke")

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
    ap.add_argument("--deadline", type=float, default=-1.0,
                    help="seconds the page cache may live, bounding the resolve walk and the watch's re-reads "
                         "(default: derived from --wait + --watch + 60, at least 300 -- a fixed 300 died at "
                         "479 s of a 520 s sequence and the commands after it were typed unread)")
    ap.add_argument("--live", action="store_true",
                    help="leave the guest running between reads (stop/read/resume). Required when the watcher "
                         "starts before the boot: attaching alone interrupts the CPU, and a reader that never "
                         "resumes freezes the boot it is watching")
    ap.add_argument("--receipt", metavar="MARKS", default=None,
                    help="comma-separated markers: after the watch, read the `Console`'s own `History[16]` and "
                         "report each marker as delivered (its submit ran) or not (the keys never reached the "
                         "console). A box that never held a delivered line is the sink drawing nothing")
    ap.add_argument("--receipt-wait", type=float, default=60.0,
                    help="seconds the receipt may keep polling for a marker to appear (default 60: the commands "
                         "are typed near the end of the sequence and a read that starts early must outlast them)")
    ap.add_argument("--receipt-tail", type=int, default=0,
                    help="array entries the receipt may scan, newest first (default 0 = the whole array: the "
                         "console sits at index 36,148 of 48,646, so a 2,000-entry window cannot see it)")
    ap.add_argument("--receipt-walk", action="store_true",
                    help="skip the HUD's own `PlayerConsole` field (+0x398) and locate the console by walking "
                         "the object array -- the control for a receipt that reads one object in one read")
    ap.add_argument("--console-client", type=lambda v: int(v, 0), default=None, metavar="ADDR",
                    help="the `Client` object whose `InteractionMaster` (+0xc0) names the console. The slot is "
                         "`Client`'s, not the level row's: `XboxClient` sat at `0x01fb9bc0` on two level boots "
                         "(`plr1`, `ghud1`), and a level row holds null at `+0xc0` (`hudsay3`)")
    ap.add_argument("--console-at", type=lambda v: int(v, 0), default=None, metavar="ADDR",
                    help="read this `Console` object and nothing else: no route and no array walk. The "
                         "four-state read (before/after `Tab`, after a key, after `ret`) uses it so each state "
                         "costs a handful of page reads instead of the 347 a fallback walk spends")
    ap.add_argument("--trace", type=float, default=0.0, metavar="SECS",
                    help="after the read, poll the same object (and the client's `+0x30` slot, with "
                         "`--console-client`) for SECS and print a line per change: the byte's own curve, which "
                         "needs no agreement between the reader's clock and the keys' timing. Needs `--live`, "
                         "because a non-live reader leaves the guest stopped")
    ap.add_argument("--no-walk", action="store_true",
                    help="never fall back to the object-array walk: print what the client's block holds and "
                         "stop. The fallback costs 347 page reads (~227 s) and turns a census into a boot-ender")
    ap.add_argument("--selftest", action="store_true", help="check the field walk and the text decode, no boot")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.port is None:
        ap.error("--port is required unless --selftest is given")
    # The page cache's deadline used to be a flat 300 s, which expired in the middle of a real sequence:
    # `hudf2` died at 479 s with a 520 s run still typing, and the two commands after it were never read.
    # A caller that knows its own clock still passes `--deadline`; this default scales with the run instead.
    if args.deadline < 0:
        args.deadline = max(300.0, args.wait + args.watch + 60.0)

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
    if args.console_at is not None or (args.console_client is not None and args.receipt is not None
                                       and hud is None):
        # A console read needs no level and no row: it is one object (or one block), read now. Reading it
        # through the level gate below would refuse a title screen, which is exactly where the four-state read
        # happens -- and `--receipt ""` (an explicit empty mark list) is how a caller asks for the census alone.
        marks = [mark.strip() for mark in (args.receipt or "").split(",") if mark.strip()]
        receipt(guest, args, marks, out=lambda s: print("%s: %s" % (args.tag, s)), wait=0.0)
        if args.trace:
            trace_types(guest, args, args.console_at, args.trace, client=args.console_client,
                        out=lambda s: print("%s: %s" % (args.tag, s)))
        return 0
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
    marks = [mark.strip() for mark in (args.receipt or "").split(",") if mark.strip()]
    receipt_out = lambda s: print("%s: %s" % (args.tag, s))
    if not args.watch:
        for label, addr, name, count, text in boxes(guest, hud, pages, nm):
            print(line(args.tag, now - started, label, addr, count, text, name))
        if marks or args.receipt is not None:
            receipt(guest, args, marks, out=receipt_out, wait=args.receipt_wait, hud=hud, row=row,
                    client=args.console_client)
        return 0
    changes, _final, rebounds = watch(guest, hud, pages, nm, args.watch, args.interval, args.tag, started,
                                      row=row, follow=args.follow, args=args, rewrites=8)
    print("%s: watched %.0fs, %d change(s), %d rebind(s), %d stub read(s)"
          % (args.tag, args.watch, changes, rebounds,
             guest.reads if hasattr(guest, "reads") else 0))
    # The receipt is the second question, and it is asked after the typing: a box that never held the marker is
    # either a submit that ran into a sink drawing nothing or keys that never arrived, and only `History` says
    # which. It runs here, in the same boot, because a dump taken afterwards cannot tell the two apart either.
    if marks:
        # A level replaces its `GameHud` when the intro hands over (`watch --follow` rebinds mid-run), so the
        # console is read from the row's slot *now* rather than from the HUD the watch started on.
        final = u32(guest, row + HUD_SLOT) if row else None
        receipt(guest, args, marks, out=receipt_out, wait=args.receipt_wait, hud=final or hud, row=row,
                 client=args.console_client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
