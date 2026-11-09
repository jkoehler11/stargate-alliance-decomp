# The HUD message route, from the shipped scripts

`say hello` reaches the console (`History[]` holds it, `bTyping` 0) and draws nothing. The console is not the
question: `PlayerController.TeamMessage` hands the string to `myHUD.Message`, and **each HUD subclass decides what
`Message` does with it**. This page is that decision for every HUD the disc ships, measured from the `ScriptText`
each class carries, with no boot.

    python3 tools/hud-messages.py --src $WORK/UnrealScript      # make hud-messages

The tool reads the extracted tree (`tools/pkg-script-text.py --all --write $WORK/UnrealScript`), follows `extends`
names to find every class under `Engine.HUD`, and prints each message-point body it declares: the sink calls in
order, whether it calls `Super`, and whether the body touches `TextMessages[]`. It prints class, function and call
names with line numbers -- never source text -- and writes no file. `--selftest` needs no dump and runs in `make ci`.

## The route, class by class (measured 2026-11-08, 2,317 named classes in the tree)

Five classes reach `Engine.HUD`; four of them declare `Message`, one feeds it, and no two agree on the sink.

| class | `Message` | sink |
|---|---|---|
| `Engine.HUD` (912) | `AddTextMessage` | the engine's `TextMessages[8]` buffer |
| `GoauldConsoleHud` (608) | `LocalizedMessage` -> `AddTextMessage` | the same buffer |
| `ReplicatorHUD` (534) | `LocalizedMessage` -> `AddTextMessage` | the same buffer |
| `StargateHUD` (1151) | `LocalizedMessage` -> `SetSubtitle(Msg, 2.0s)` for `Say`/`TeamSay`, `AddNotification` for everything else | `GameHud.DisplayTextBox` / `NotificationManager` |
| `StargateTeamHUD` | none: it extends `StargateHUD` and inherits its `Message` | -- |

- **The one feed is `PlayerController.TeamMessage`** (line 1543, `myHUD.Message(PRI, S, Type)`); `ClientMessage`
  hands its string to `TeamMessage` (1505), which is the path the console's `say` takes. The tool reports exactly
  **one** call site of `myHUD.Message(...)` in the whole tree, so the route has no second entrance.
- **`StargateHUD` is the only HUD that does not touch the engine's buffer**, and `AddTextMessage` does not appear in
  its shipped text at all -- not called, and not commented out either (0 occurrences of the identifier in
  `StargateHUD.uc`). Earlier notes said "commented out"; the text does not carry the call in any form.
- **`StargateHUD.SetSubtitle` (265) hands the text to `GameHud.DisplayTextBox(SubTitleText, Duration)` (309)**, not
  to the HUD's own canvas. `GameHud` is the object the sink read found live at `StargatePlayer+0x1008`
  (`docs/XEMU.md`, `sink15`), so a subtitle is an assertion on that object as much as on the frame.

## `TextMessages[]` is filled by three HUDs and painted by none

`HUD.AddTextMessage` fills the eight-slot buffer and `HUD.DisplayMessages` is the only body that draws it (933:
`C.DrawText(TextMessages[i].Text, false)`). All four HUDs that override `DisplayMessages` -- `HUD` included -- do so
**without `Super`**, so on every shipped HUD the engine's painter is replaced:

| class | `DisplayMessages` | draws `TextMessages[]`? | `Super`? |
|---|---|---|---|
| `Engine.HUD` (933) | own draw loop | yes | n/a |
| `GoauldConsoleHud` (888) | `NotificationManager.DisplayMessages` | no | no |
| `ReplicatorHUD` (814) | `NotificationManager.DisplayMessages` | no | no |
| `StargateHUD` (1284) | `NotificationManager.DisplayMessages` | no | no |

So the buffer is **write-only** on the two console HUDs: they fill `TextMessages[]` and then override the only
function that would paint it. The correct statement of the negative is therefore narrower than "nothing fills or
paints it": *nothing paints it on any shipped HUD, and only `StargateHUD` also never fills it.*

## The assertion a console probe can make

`say <marker>` on a mission is a **2.0 s** subtitle (`SetSubtitle(Msg, 2.0000000, PRI.GetHumanReadableName(),
icon)`) rendered through `GameHud.DisplayTextBox`, so a frame taken within 2 s of the submit must differ from the
pre-submit frame -- `tools/xemu/console-commands.sh` already takes its per-command frame one second after the
submit, inside that window. What a zero on `say` would then mean is a fact about `GameHud.DisplayTextBox`, not about
delivery or timing:

- `god` and `addnotification` take the `AddNotification` branch instead: `StargatePlayer.NotificationManager` ->
  `GameHud.SetNotifications`, which is conditional on both objects being non-`None` (both were live on `sink15`).
- `TextMessages[]` is not readable as evidence on a mission for the opposite reason from the one recorded in U15:
  `StargateHUD` never writes it.

The boot that would settle the frame half -- one `say <marker>` with a 1 s frame -- is not run by this page; the
static route above is what a boot would have to agree with, and the two branches are distinguishable by which object
grew: `GameHud`'s text box for `say`, `NotificationManager`'s list for `god`.
