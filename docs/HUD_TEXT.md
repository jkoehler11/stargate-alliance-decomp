# Reading the HUD's text boxes while the game runs

`say hello` reaches the console (`Console.History[0]` holds it, `bTyping` 0) and draws nothing a frame can see.
`docs/HUD_MESSAGES.md` follows the route from the shipped scripts: `PlayerController.TeamMessage` -> `myHUD.Message`,
and `StargateHUD.Message` sends a `Say`/`TeamSay` to `SetSubtitle(Msg, 2.0s)`, which ends in
`GameHud.DisplayTextBox`. This page is the live read of that end: which object is written, which field, and what a
zero on it means.

    python3 tools/xemu/hud-text.py --port 12620                     # read the three boxes once
    python3 tools/xemu/hud-text.py --port 12620 --wait 300 --watch 120   # watch them change
    tools/xemu/hud-say.sh ~/sg1/work/hudsay1                        # one boot: submit markers and watch

## Why the assertion is a guest read and not a frame

Two measurements rule the frame out, and the second one is why the field has to be read *inside* two seconds:

- **A message line is smaller than the picture's own animation.** The text is ~300 x 20 px in a 1,280 x 960 frame,
  and a live 3D view's frame-to-frame noise floor measures ~40,000 px, so a whole-frame diff cannot see it and a
  threshold low enough to try would fire on the scene.
- **`DisplayText` is cleared, not just faded.** `UITextBox.DisplayTextBox` (`StargateGame/UITextBox.uc`) sets
  `StartFadeTime = Level.TimeSeconds + FadeOffsetTime + Duration` and writes `DisplayText = Text`; its `Update`
  calls `Hide(t, true)` once `TimeSeconds > StartFadeTime`, and that path ends in `DisplayText = ""`. With the
  subtitle's `Duration` of 2.0 s the text lives about two seconds. `msg1` searched a 128 MiB `pmemsave` taken ten
  seconds after the submit and found no copy of the string -- a result about the clock, not about delivery.

`console-commands.sh`'s per-command frame is taken one second after the submit, so it is *inside* the window and
still shows nothing: that is a fact about the sink, and the only way to read the sink is the field.

## The four addresses, and the gate on each

| what | object | offset | gate |
|---|---|---|---|
| `GameHudPtr` | `StargatePlayer` | `+0x1008` | the slot is a field only on a row whose chain reaches `StargatePlayer`, and the target's own `Name` must be `GameHud` |
| `DialogueWindow` | `GameHud` | `+0x050` | a plausible object pointer, read as its own `Name` |
| `NotificationWindow` | `GameHud` | `+0x054` | ditto |
| `InteractionWindow` | `GameHud` | `+0x058` | ditto |
| `DisplayText` | `UIWidget` | `+0x394` | an `FString` `(data, count, max)` whose `count` includes the NUL and whose `data` is a plausible pointer |

- **The HUD comes from the player, not from a class search.** `GameHud` is a script class and carries its native
  ancestor's (`Interaction`) class table, so no census can name an instance of it; `StargatePlayer+0x1008` is the
  route the sink reads measured (`docs/XEMU.md`, `sink-slots.py`), and the name check on the target is what keeps a
  heap neighbour from being read as a HUD.
- **The row comes from the object array.** `GObjObjects` appends, so a level's freshly spawned row is at the end:
  `--tail 2000` walks the newest entries, newest first, and `--row ADDR` skips the walk once a read has named one.
- **The boot has to be a level, not the menu.** A menu boot holds a `StargatePlayer` row outered to `MainMenu` too
  (`sink3`), so `--wait` polls `GNames` until it reads a level's size (41,265 against 34,800 at the menu) before
  resolving anything. This is the gate that keeps a failed boot from being read as "the message never arrived".
- **A hole in memory is a reading.** Every fetch is one 4 KiB-bounded request through the stub, so an unmapped page
  answers `None` for that field instead of ending a watch loop.

## What a zero means, three ways

The tool separates the three cases, and a run that cannot reach the level says so instead of reporting a message
result:

| reading | what it is |
|---|---|
| `no level within Ns (the name table still reads M names, against 41265 on a level ...)` | the boot never left the menu: **nothing** about `say` was measured |
| `+0x1008 GameHudPtr is 0` / `holds X named 'Y', not GameHud` | the route has no sink on this boot: `SetSubtitle` ends in a null or in something that is not the HUD |
| `DialogueWindow ... count 0` on a resolved HUD | the read reached the sink and the text was not there |

`hud-say.sh` prints `FOUND`, `ABSENT` or `UNREAD` per marker for the same reason: only the middle word is a
measurement about the message.

## The boot-failure negatives this instrument cost

Three boots went into the first version, and two of the failures are worth carrying forward because each one looks
exactly like a working run from the outside:

- **A boot whose window is behind another application captures nothing.** With a Steam game fullscreen on the same
  display, every frame came back a blank PNG (2,688 bytes, one green pixel on black), the harness's repaint gate
  failed every shot (`0 differing pixels` against a threshold of 200), and the map never loaded. The guest was
  running the whole time -- the stub answered, the pad device was bound -- so the frames were the only broken part.
  `hud-say.sh` now issues `raise` and `activate` before the first press; the frames stayed blank, so on this desktop
  a HUD-text boot wants the screen to itself.
- **180 s is too early for the first `A`.** Two boots at `--boot=180` ran the whole sequence and never left the
  title, which is the swallowed-select failure `sink14` measured at the same window; the sink boots pass first time
  at 240 s, and `hud-say.sh` defaults to it now.
- **Upper-case marker letters are not QEMU key names.** `guest key T -> invalid parameter: T` in the step log: the
  letter is dropped, the console receives a shorter command, and the run reads exactly like a message that never
  arrived. Markers are lower-case and digits, and the script refuses anything else rather than dropping it.

## Four more failures, three of them in the instrument (measured 2026-11-08)

- **The watch window was bounded by the process clock.** `--watch` looked like "watch for N seconds", but the only
  guard in the loop compared `time.monotonic() - started` -- when the *process* began -- against `--watch + 30`. A
  HUD-text boot spends 240 s on the title and 60 s on the settle before the watcher resolves anything, so the
  guard had already expired when the watch began: the run printed the `GameHud` baseline and `watched 0s` while the
  commands it was started for had not been typed yet. The loop is a function (`watch()`) that measures its own
  window, and `--selftest` now runs one that *looks* 600 s old and lands a subtitle on its fifth poll, so the old
  early exit fails the test instead of hiding behind a plausible log.
- **`focus` is measured, and it is not the blank frame.** `hud-say.sh` now sends `--step=focus=30` after `raise`
  and `activate`. Both boots of this session answered `step 4: the emulator owns the focus after 0s`, and the
  frames were still blank -- so "a HUD-text boot wants the screen to itself" is retired: a window can own the X
  focus and still capture nothing.
- **The blank frame is one frozen picture, not a dark screen.** `hud8` saved eleven frames -- title, list,
  highlight, mission, dismissed tutorial, both per-command frames and two noise pairs -- and all eleven are the
  *same* 8,021-byte PNG, md5 `1860677834e7a3c214fd295018442c91`, across four minutes and twenty-six input steps.
  The picture is 1,280 x 960 with `mean=25.9` and 516 colours: a black field with one soft green dot, no menu and
  no error dialog. A screen that never changes is a window whose pixmap never updates, which is why the noise
  floor is 0 px and every shot step fails, and a working boot differs (`sink16`'s title, highlight and mission are
  three different files tens of KB in size). This is the measurement that closes "blank frames mean the map did
  not load".
- **And the guest agreed the boot never started.** In the same boot the watcher polled `GNames` 84 times over
  420 s and never once read a plausible data pointer -- with `0 of the polls could not read it at all`, so the stub
  answered every time. A menu boot reads 34,800 names and a level 41,265; this boot read neither for seven
  minutes. `--wait` now prints the count of polls it could not read at all, because "0 unreadable polls" and "some
  unreadable polls" are different facts about a boot, and the deadline is the sequence's whole length
  (`boot + settle + 90 s + 15 s per command`) rather than a settle plus a hundred seconds.

## Measured state (2026-11-08, superseded by the 2026-11-09 section below)

**No live read had landed at this point.** Three boots ran the sequence and every one of
them captured blank frames (2,331-2,688-byte PNGs -- a black window with one green pixel -- against tens of KB for a
real 1,280 x 960 frame) and never reached a level-sized name table, so `hud-text.py` refused all three at its
`--wait` gate instead of reporting a message result. What was verified then was the instrument: `--selftest`
resolves a `StargatePlayer` row to its `GameHud`, reads two boxes and refuses a slot that is not a HUD, and both
scripts parse in `make ci`. The cause of all of it was the reader interrupting the CPU without resuming it; the
boxes were read live on 2026-11-09.

**The blank capture was the reader, not the capture path (settled 2026-11-09).** A second run
had the screen to itself -- the Steam client was the only window on the display and `_NET_ACTIVE_WINDOW` was the
desktop -- and still captured blank frames: every shot a 7,556-byte PNG, black with one green pixel, five shot
steps at `0 differing pixels`, against tens of KB for a real frame. That was written up as a property of the
capture path, and it is not. `hud-text.py` attaches to the stub, and `objects.Guest` **interrupts the CPU on
connect and never resumes it**: a watcher started one second before the boot freezes the guest at 4.5 s, before the
video is initialised. Everything the earlier runs measured follows from that -- one identical frame per run, a
`GNames` triple that answers every poll with a boot-time value, a boot that never reaches a level, and pad presses
that land on a stopped CPU. Six boots were spent on it. The reader is now `LiveGuest` (`--live`, stop/read/resume),
and the first boot with it on reaches a level and reads the boxes (next section). That run also produced **no level
verdict at all** in its predecessor: `--wait` was `boot + settle + 150` (450 s) against a sequence that finishes in
about `boot - 75 + settle + 40` s, so the watcher was killed mid-wait and its log held neither a resolved `GameHud`
nor the "no level within Ns" line. `hud-say.sh` derives the deadline from the sequence
(`boot - 75 + settle + 30 + 12 s per command`) and prints `NO VERDICT` when a log holds neither, so
"the boot did not load a level" can never again be inferred from a run that stopped looking. It also refuses to
start while another emulator or a leftover `SG1 virtual pad` is present, because a second boot on the same GDB port
exits with `Address already in use` and `drive.py` reports that as `xemu exited immediately` — which reads like a
broken emulator and is a collision.

**And the second failure mode has a name now: `FAIL: no xemu window on :0`.** A `hud-say.sh` run started with no
emulator already up reported `waiting 75s for the title` and then that line, with `drive.py exit 3` — the emulator
never put a window on the display it was pointed at, so every capture and every window-targeted step had nothing
to act on. `drive.py` names it (`no xemu window on :0`) and `hud-say.sh` reports the attempt rather than the
message. It is a separate fault from the frozen guest above; a boot can have either, and the two read differently:
a frozen guest has a window and captures the same small frame every time, while this one has no frame to capture.

**Two more boots, and what failed is named (measured 2026-11-08, `hud7` and `hud8`).** Both took the focus, attached
the keyboard at 75 s, ran the whole sequence and typed both markers; both read nothing. `hud7` stopped looking at a
300 s deadline -- after the watch fix, but with a budget that did not cover the sequence -- and `hud8` at 420 s with
the name table still uninitialized, so the deadline now derives from the whole sequence. The eleven frozen frames
above are the same boot's evidence, and `GNames`' 84 empty polls are that freeze seen from the guest: a stopped CPU
serves reads but runs no code. The field this page exists for was measured on the next boot (below).

## Measured state (2026-11-09, the boxes read live, and `say` is still absent)

The first boot whose game started while a watcher was attached (`hud-say.sh` with `--live`, one attempt, `M00` at
index 10). Read the counts, not the strings: field contents are game state from that boot, and quotable lines here
would be game data.

- **The boot runs, and the name table proves it**: stub up at 4.5 s, level-sized table at 281.7 s (40,427 names),
  47,042 objects on the next walk and 48,494 on the one that found the row, and frames of 590-644 KB. The 8 KB
  frozen frames belong to the reader that was freezing the guest, not to the capture path.
- **The row and the HUD resolve by walking, never by a carried address.** Two `--tail=2000` walks found no
  `StargatePlayer` while the guest was still on the list (42,438 and 47,042 objects); the third, at 48,494, found
  `0x08842800` (object 47,885) with outer `M00` and `+0x1008 -> GameHud 0x09312100`. The retries are load-bearing:
  the row does not exist until the level has spawned it, so a single walk at the name-table floor is a menu read
  (`sink9`'s trap, measured again here).
- **All three boxes read live, and `DialogueWindow` carries the level's own dialogue.** It held `count 14` -- the
  intro card's line, already quoted in `docs/XEMU.md` -- then four speaker turns at counts 35, 33, 50 and 56, each
  in the same object `0x09737300` and each replacing the previous one while the watch polled. `NotificationWindow`
  and `InteractionWindow` held the class placeholder `default` (count 8) at the same `+0x394`. That is U15's field
  read on a live level: the offset, the `FString` shape (`count` includes the NUL, so 14 is 13 units of text) and
  the `GameHud+0x050` window map are confirmed by traffic the game produced itself.
- **`say sg1txt1` and `addnotification sg1txt2` are still ABSENT, and the same boot says why that is not the
  reader.** At 437.5 s all three window slots read `0x00000000` -- the `GameHud` the read had resolved was
  destroyed, so the level changed under it -- and the three shots around the two commands did not repaint (521 px
  against a 2,254 threshold). The commands were typed into the intro, whose end tears the HUD down; the boxes the
  read was watching were gone before they landed. **Next**: submit after that hand-over (a settle past the intro,
  then the console) and read the two markers in the window that follows -- timing, not a new instrument.

## Measured state (2026-11-09, the hand-over is the menu's row dying, and the level's HUD reads live)

- **The name table's floor is not the level gate; the row's own `Outer` is.** The boot read 40,427 names at
  281.7 s -- above the 40,000 floor -- and resolved `row 0x06b30000` (object 41,474 of 42,401) **outered to
  `MainMenu`**: the menu's own `StargatePlayer`, which the map load destroys. Its `GameHud` `0x0682ab00` was gone
  one second into the watch (all three window slots `0x00000000` at 317.9 s), two follow ticks later `+0x1008`
  held a `ColorModifier`, then an `Other`, then no object, and the walk at 343.2 s found no `StargatePlayer` among
  the last 2,000 of 46,169 objects. So the earlier reading -- "the intro's `GameHud` was destroyed as the intro
  handed over" -- is precisely *the map load destroying the menu's row and its HUD*; the level's own row does not
  exist yet at that moment. `resolve` now skips a row outered `MainMenu`, and a `--wait` run keeps walking (10 s
  apart) until its budget instead of giving up 60 s after the first row appeared.
- **The level's row resolves by the same walk, later and outered to the map.** `0x08832800` (object 47,885 of
  48,494) with outer `M00` and `+0x1008 -> GameHud 0x09362100`; the first attempt at 291 s found nothing in the
  last 2,000 of 42,438, which is why the retries are load-bearing.
- **The boxes read the level's own dialogue, and the notification box paints the game's objective banner.** One
  line in `DialogueWindow` `0x097c7300` (`count 14`), then speaker turns at counts 35, 33, 50, 56 and 53, and at
  479.2 s `NotificationWindow` `0x097c6e00` held `count 45` with an objective banner -- the game's own
  `AddNotification` output arriving in the same field `addnotification` would write. Counts, not strings: the
  text is game content.
- **The four markers are still ABSENT, and the boot says why it is not the reader this time.** The command steps
  ran after the level's HUD was resolved, and `drive.py` reports `4 shot step(s) did not repaint` with a noise
  floor of `0 differing pixels` -- the tutorial modal, which is up on `M00` and freezes the picture, so the
  keystrokes had nowhere to go (`docs/XEMU.md`: the panel holds input until `A` dismisses it). The next
  measurement is the same sequence with the modal dismissed *after* the resolve, then the markers.

## Selftest

`--selftest` builds a four-object guest in RAM -- the row, its `GameHud`, two text boxes -- and asserts the walk, the
`FString` decode (`count` includes the NUL; `count` 1 is the empty string), the refusal of a `+0x1008` that names a
non-`GameHud`, the null slot, and that a watch reports a subtitle that lands after it starts on a clock that already
looks ten minutes old. It runs in `make ci` and needs no emulator or dump:

    ok   hud-text: resolves the row and its `GameHud`, reads both boxes, and refuses a slot that is not one
    ok   hud-text: the watch window is the watch's own clock -- a subtitle that lands after it starts is reported
    ok   hud-text: a live reader resumes the guest after every read (stop/read/resume on each)

The tool prints field addresses and text and writes no file; a marker in a log is game state from that boot only.
