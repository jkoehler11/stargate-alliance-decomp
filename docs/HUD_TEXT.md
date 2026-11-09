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

## Measured state (2026-11-08)

**No live read has landed yet, and this page does not claim one.** Three boots ran the sequence and every one of
them captured blank frames (2,331-2,688-byte PNGs -- a black window with one green pixel -- against tens of KB for a
real 1,280 x 960 frame) and never reached a level-sized name table, so `hud-text.py` refused all three at its
`--wait` gate instead of reporting a message result. What is verified is the instrument: `--selftest` resolves a
`StargatePlayer` row to its `GameHud`, reads two boxes and refuses a slot that is not a HUD, and both scripts parse
in `make ci`. What the field holds after a `say` is still the next boot's measurement -- on a desktop that is
showing the emulator, at `--boot=240`.

**The next boot failed differently, and the two failures are now told apart (measured 2026-11-08).** A second run
had the screen to itself -- the Steam client was the only window on the display and `_NET_ACTIVE_WINDOW` was the
desktop -- and still captured blank frames: every shot a 7,556-byte PNG, black with one green pixel, five shot
steps at `0 differing pixels`, against tens of KB for a real frame. So the blank capture is a property of this
capture path rather than of what is in front of the window, and `0 differing pixels` is not evidence that the pad
presses were lost. That run also produced **no level verdict at all**: `--wait` was `boot + settle + 150` (450 s)
against a sequence that finishes in about `boot - 75 + settle + 40` s, so the watcher was killed mid-wait and its
log held neither a resolved `GameHud` nor the "no level within Ns" line. `hud-say.sh` now derives the deadline from
the sequence (`boot - 75 + settle + 30 + 12 s per command`) and prints `NO VERDICT` when a log holds neither, so
"the boot did not load a level" can never again be inferred from a run that stopped looking. It also refuses to
start while another emulator or a leftover `SG1 virtual pad` is present, because a second boot on the same GDB port
exits with `Address already in use` and `drive.py` reports that as `xemu exited immediately` — which reads like a
broken emulator and is a collision.

**And the second failure mode has a name now: `FAIL: no xemu window on :0`.** A `hud-say.sh` run started with no
emulator already up reported `waiting 75s for the title` and then that line, with `drive.py exit 3` — the emulator
never put a window on the display it was pointed at, so every capture and every window-targeted step had nothing
to act on. That is the honest reading of "blank frames on a free screen": on this desktop a boot launched over
`ssh` with `DISPLAY=:0` does not reliably get a window, which is a property of the launch, not of the game and not
of `say`. `drive.py` names it (`no xemu window on :0`) and `hud-say.sh` now reports the attempt rather than the
message.

## Selftest

`--selftest` builds a four-object guest in RAM -- the row, its `GameHud`, two text boxes -- and asserts the walk, the
`FString` decode (`count` includes the NUL; `count` 1 is the empty string), the refusal of a `+0x1008` that names a
non-`GameHud`, and the null slot. It runs in `make ci` and needs no emulator or dump:

    ok   hud-text: resolves the row and its `GameHud`, reads both boxes, and refuses a slot that is not one

The tool prints field addresses and text and writes no file; a marker in a log is game state from that boot only.
