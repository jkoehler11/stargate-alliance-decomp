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

## The receipt: `Console.History` says whether the submit ran

A box that never held the marker has two causes that look identical from the box: the keys never reached the
console (a modal holding input, a press into an unfocused window, a dropped character), or the command ran and
the sink it writes drew nothing. `--receipt MARKS` asks the guest which one, in the same boot, after the typing:

    tools/xemu/hud-say.sh ~/sg1/work/hudsay1                  # every run of the harness carries the receipt now
    python3 tools/xemu/hud-text.py --port 12620 --receipt sg1txt1,sg1txt2 --receipt-wait 90

`Console.uc`'s `IK_Enter` branch copies `TypedStr` into `History[HistoryTop]` and only then calls
`ConsoleCommand`, so a line in a slot is proof the submit ran, and an empty history after the typing window is
proof the keys did not arrive. The slots are `FString`s at `+0x50`, stride 12, through `+0x110`; the counters are
printed beside them (`HistoryTop`, `bTyping`, `TypedStr`) because a populated history plus a line still being typed
is not a submit (`console-state.py` measured that distinction on the 2026-11-06 boot). The class key is
`0x0050cb80` from `inventory/xbe_class_table_names.csv` (object size 1088); a class table is package data, so the
address does not move per boot.

**Two routes to the object, cheapest first (measured 2026-11-09).** The live HUD holds its console in one
`Console`-typed field, `PlayerConsole` (`Engine/HUD.uc:27`), at `+0x398`, and `inventory/hud_console_link.csv`
measured that field on a whole-RAM dump naming the very console `console-state.py` had typed into (`bTyping 1`).
UE2 lays base-class fields out first, so the offset is the same in every HUD subclass (`StargateHUD`,
`GoauldConsoleHud`, `GameHud`). The receipt reads the pointer the HUD holds *now*, screens it by class (`+0x24`
`UObject::Class`, or the object's first dword — the word `hud-link.py` screened the dump by), and falls back to the
object array when the field is null or names another class. `--receipt-walk` skips the field and forces the walk:
the control that shows the one-read route read the same object.

| reading | what it is |
|---|---|
| `RECEIPT <mark> IN HISTORY` | the submit ran: a box that never held the line is the message path drawing nothing |
| `RECEIPT <mark> NOT IN HISTORY` | no submit ran: the keys never reached the console (modal, focus, dropped character) |
| `CONSOLE none in <window> of M object(s)` | the console never opened, or it is outside the walk's window: delivery is unmeasured, not absent |
| `RECEIPT read N console object(s) by <route>` | the cost line: which route found the console, how many array walks and page reads it took |

**A miss must not become a verdict (measured 2026-11-09, `hudr2`).** The first receipt walked the *newest*
`--tail` objects, and on a loaded `M00` (48,646 objects) that window held no `Console` at all: 18 walks of the
newest 2,000 printed `CONSOLE none`, and the first version then answered `NOT IN HISTORY` for both markers -- a
claim about the keys drawn from a search that never read the object. It now says `RECEIPT <mark> UNMEASURED` when
no route read a `Console`, with `hud-say.sh` reporting `UNREAD` rather than `NOT DELIVERED` for it.

**Where the console actually is (measured 2026-11-09, no boot).** A saved census of a loaded level
(`xemu/ghud1/cls.csv`, 47,742 named objects) holds **one** `Console` instance, at **index 36,148 of 48,646**, and
the HUD's own field named the same object (`0x02344b00`) in `findtext4` (`inventory/hud_console_link.csv`). So the
console is mid-array: outside a 2,000-entry tail window and outside a 2,000-entry head window alike, which is why
neither shortcut could ever have found it. The receipt therefore prefers the HUD field (one read) and walks the
**whole** array when that fails (`--receipt-tail` restores a window; `0`, the default, walks everything).

`hud-say.sh` prints `DELIVERED` / `NOT DELIVERED` / `UNREAD` beside each marker's box verdict for that reason, and
its `FOUND` test reads the box lines only — the receipt's own lines carry the marker, and a `FOUND` that was really
a `RECEIPT` line would report the message path as painting when it painted nothing.

Two costs are part of the instrument. The receipt runs *after* the watch, so `drive.py`'s `--linger` has to hold
the stub (and the emulator) up past it: the harness derives `120 s + 30 s per command`, because the reader is
killed two seconds after `drive.py` exits and a cut-off receipt reads as an unmeasured one. And `--receipt-wait`
(90 s in the harness) keeps polling the slots, because the commands are typed at the very end of the sequence and
a receipt that starts early would read an empty console and call it a lost keystroke.

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

## Measured state (2026-11-09, the panel is dismissed and the boxes stay empty)

- **The dismissal has to be its own press, and the two shots around it prove it landed.** `hud-say.sh` now presses
  the pad's `A` once `--post` has passed the intro's hand-over, immediately before the first command, and brackets
  it with `shot=05-precommands` / `shot=06-dismissed`. On `hudm1/try1` that pair read 40,532 and 34,096 differing
  pixels, and `06-dismissed` is gameplay -- the "Press the X button to open the door / A Continue" panel is gone.
  The `pad=a` inside the mission sequence is *not* that press: it lands on the intro card ("10 days later /
  Press A"), which waits on its own clock, and its shot reads 0 px.
- **The page cache's 300 s deadline, not the watch window, ended the read early.** `hudf2`'s watch died at 479 s
  (`the read passed its 300s deadline after 58 page read(s)`) with two of its four commands still to be typed, and
  the report called every marker ABSENT. `hud-text.py`'s `--deadline` now derives from `--wait + --watch + 60`
  (at least 300 s), `hud-say.sh` passes the run's own budget, and a log that still hits it reports `UNREAD` with
  the reason instead of turning "not read" into "not there".
- **All four markers are still ABSENT with the panel gone**: `say sg1txt1` (shot 6,289 differing px),
  `addnotification sg1txt2` (8,466), `say sg1txt3` (272) and `addnotification sg1txt4` (0). The level's three boxes
  were read live throughout -- intro turns at counts 14, 35, 33, 50, 56 and 53, and the game's own
  `New Objective : Open the door for the doctor` banner at count 45 (480.4 s) -- and at 677.7-678.5 s all three
  slots read `0` (`DialogueWindow` `0x09737300` -> `0x00000000`): that `GameHud` was destroyed while the boot was
  still running. So "the modal swallowed the keys" is retired as the explanation; what is still unknown is whether
  the four commands were *executed* at all.
- **Next: a submit receipt in the same boot.** `Engine.Console.KeyType` copies `TypedStr` into `History[HistoryTop]`
  on a submit, before it clears the line, so one `History[16]` read after the last command splits "the keys never
  arrived" from "the command ran and drew nothing" -- the two cases this boot cannot yet tell apart
  (`docs/UNKNOWNS.md`, U15). The boxes' death at 677 s also bounds the watch: a marker typed after it has no sink.

## Measured state (2026-11-09, the HUD's field held a number and the walk outlived the linger)

- **The level's HUD does not name its console at `+0x398`.** `hudsay2/try1` resolved the level's row `0x08842800`
  (object 47,885 of 48,494, outer `M00`) at 360 s and `+0x1008 -> GameHud 0x09382100`, read six box changes off it,
  and then the receipt read that HUD's `PlayerConsole` as **`0x00050975`** -- not an object address, so the
  one-read route printed `CONSOLE hud 0x09382100+0x398: holds 0x00050975, which is not an object address` and fell
  back. `inventory/hud_console_link.csv` measured `+0x398` on `findtext4`'s HUD (a `StargateHUD` that had typed into
  its console); a level's own HUD is a different state, and the field is a *candidate*, not a link.
- **A whole-array walk does not fit a 180 s linger.** The fallback was still reading when the stub stopped:
  `the stub stopped answering at page 0x012e0000: 0x07730000, which read at the start, no longer does -- the guest
  is gone` -- both markers printed `UNREAD` with no `History` read at all, so this boot's submit question went
  unanswered. `hud-say.sh`'s derived linger is now `300 + 30/command` s, and the failure is a fact about the walk's
  cost (347 page reads) rather than about the console.
- **The panel press is not deterministic in either direction.** The same pair that read 40,532 and 34,096 px on
  `hudm1` read **0 and 0** here, and both command shots read 0 px, on a boot whose boxes were demonstrably live
  (six changes through 458.8 s). So "the press landed" cannot be assumed from one run, and a 0 px command shot is
  not evidence the keys were dropped -- which is exactly why the receipt, not a frame, is the instrument.
- **The route to read now is one hop from the row.** The console is registered as an interaction when the viewport
  is made, so it is not a field of the player row; `PlayerController.InteractionMaster` at `+0xc0` (measured at
  `0x02406720` on `plr3`, whose own fields name `XboxClient`, `GUIController`, `Console` and `UIXInteraction`) is
  the object that has it. `console_from_player` screens that object's first `0x100` bytes for a pointer whose
  target opens with the `Console` class table (`0x0050cb80`) -- at most 66 reads, independent of the array index --
  and the receipt tries it before the HUD field and the walk. Its reason strings distinguish a null master, a
  master that is not an object, and a master with no console pointer, so a miss names what to read next.

## Measured state (2026-11-09, the walk keyed a class pointer with a class table, and `+0xc0` is a `Client` slot)

- **`hudsay3` answered every console route with a miss, on a boot that was demonstrably live.** `hudsay3/try1`
  loaded `M00`, resolved the level's row `0x08822800` (object 47,885 of 48,494, outer `M00`) and
  `+0x1008 -> GameHud 0x092e2100`, and read seven box changes off it -- the intro's turns at counts 14, 35, 33, 50,
  56 and 53 (through 464.4 s), then the game's own `New Objective : Open the door for the doctor` at 479.3 s. The
  frame pair `05-precommands`/`06-dismissed` repainted 42,959 / 32,704 px, so the dismissal landed here (it read
  0 / 0 on `hudsay2`). All three console routes then missed: the row's `InteractionMaster` (`+0xc0`) read **null**,
  the level HUD's `PlayerConsole` (`+0x398`) read `0x00050967`, a number and not an address, and a whole-array walk
  of **48,646** objects read **zero** `Console` objects (347 page reads, 215.1 s), so both markers came back
  `UNMEASURED`.
- **The walk's zero is the instrument's, not the guest's.** The walk screened `UObject::Class` (`+0x24`) against
  `0x0050cb80` -- the `Console` **class table** in `.rdata` (`inventory/xbe_class_table_names.csv`, 1088 bytes) --
  and a table is the object's **first** dword: `hud-link.py` measured the typed-into `Console` opening
  `00 50 cb 80 ...` on a whole-RAM dump, which is how `inventory/hud_console_link.csv` found `+0x398` at all. `+0x24`
  holds the `UClass` *object*, not a table address: `ghud1`'s census names it `275,0x00d48a00,Class,Console` and the
  instance `36148,0x02344b00,Console,Console`, and `names.py --verify` measured `+0x24` as a `UClass` pointer. So the
  walk could only ever match an object whose class *object* sits in `.rdata`, and the selftest agreed with it because
  it wrote the table to `+0x24` too. `sink.rows_holding` now compares both dwords in one pass (pages are cached, so
  the second compare costs no read), and the selftest writes the live shape instead: table at `+0x00`, `UClass` at
  `+0x24`, with the old route's `[]` asserted beside the new route's hit.
- **`InteractionMaster` at `+0xc0` is `Client`'s slot, not the level row's.** It was measured on the `XboxClient`
  object `0x01fb9bc0` (`plr1`), and `ghud1`'s census names the same address on a second level boot -- but `hudsay3`
  read row `0x08822800`, a `StargatePlayer`, at `+0xc0` and got null, which is a fact about that offset on that
  class rather than about a console. The route now takes the client object (`--console-client`, `hud-say.sh
  --console-client=ADDR`) and a level row is not screened: the receipt prints that the slot is `Client`'s instead of
  reporting a null master as a console that never opened.
- **Instruments**: `tools/xemu/sink-slots.py` (`rows_holding`, `walk` takes an offset tuple, both routes' assertions
  in `--selftest`), `tools/xemu/hud-text.py` (`console_from_client`, `--console-client`, the row caveat, the
  both-dword walk, two selftest cases), `tools/xemu/hud-say.sh` (pass-through), `docs/UNKNOWNS.md` U15. No artifact
  and no new file.
- **Next**: one `hud-say.sh` run -- `--console-client=0x01fb9bc0` or the fixed walk -- and its
  `RECEIPT ... IN HISTORY` / `NOT IN HISTORY` is the submit verdict the last three boots have been circling; if the
  submits ran, what is left is the `SetSubtitle`/`SetNotifications` guard.

## Selftest

`--selftest` builds a four-object guest in RAM -- the row, its `GameHud`, two text boxes -- and asserts the walk, the
`FString` decode (`count` includes the NUL; `count` 1 is the empty string), the refusal of a `+0x1008` that names a
non-`GameHud`, the null slot, and that a watch reports a subtitle that lands after it starts on a clock that already
looks ten minutes old. The receipt has its own cases: a console at index 5 of 400 is `UNMEASURED` under a
100-entry tail window and `IN HISTORY` under the whole-array walk; the HUD's `+0x398` reads it with **0** array
walks; a `+0x398` that names another class prints the reason and falls back instead of reading the wrong object;
and a `Client`'s `+0xc0` finds a console one hop out (`InteractionMaster` -> the screen), says why when that master
holds no `Console` pointer, says a null master is null, and refuses to screen a level row at that offset; and the
whole-array walk finds a console keyed on a class *table*, which the `+0x24`-only walk cannot. It runs in `make ci` and needs no emulator
or dump:

    ok   hud-text: resolves the row and its `GameHud`, reads both boxes, and refuses a slot that is not one
    ok   hud-text: the watch window is the watch's own clock -- a subtitle that lands after it starts is reported
    ok   hud-text: a live reader resumes the guest after every read (stop/read/resume on each)

The tool prints field addresses and text and writes no file; a marker in a log is game state from that boot only.
