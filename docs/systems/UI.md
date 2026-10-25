# UI: the biggest subsystem, almost entirely script, and a correction to how the fork was counted

The interface is the largest single area in the script tree — **347 classes and 2,137 functions** if you
count the menu package, the two stock GUI packages and the HUD/menu classes in `StargateGame` — and it is
the least native-dependent: `StargateMenus` contains **zero** native functions in 98 classes.

    python3 tools/class-graph.py --root SGPageBase --root SGListBox -o inventory

## Two layers

| layer | classes | functions | natives |
|---|---:|---:|---:|
| `StargateMenus` | 98 | 334 | **0** |
| `GUI` + `UWindow` (stock) | 226 | — | most of the 88 |
| HUD and menu classes in `StargateGame` | 11 | 213 | 1 |

`StargateMenus` is a page-and-panel library: `SGPageBase` (6 functions, 12 properties) with **31
subclasses** — `XBoxLiveMenu`, `XBoxLiveFindGameMenu`, `XBoxLivePlayMenu`, `SelectPawnPage`,
`SinglePlayerSelectProfile`, `VirtualKeyboardPage`, … — plus `RightLockingPanel` 9, `LeftLockingPanel` 4,
`FullSlidingPanel` 2, `SGListBox` 7. Eleven of the 98 classes carry no functions at all.

The heavy individual classes are widgets: `VirtualKeyboardPanel` (17 functions, 29 properties — the Xbox
on-screen keyboard), `ObjectivesPanel` (14/5), `ServerSelectionListBox` (13/4), `ServerList` (9/2).

## The game-side HUD is where the weight is

| class | functions | properties | native |
|---|---:|---:|---:|
| `HUD` (Engine) | 67 | 84 | 6 |
| `StargateHUD` | 57 | 101 | 1 |
| `ReplicatorHUD` | 29 | 69 | 0 |
| `StargateTeamHUD` | 5 | 9 | 0 |

Two full player HUDs — `StargateHUD` and `ReplicatorHUD` — which means playing as a Replicator is a
first-class mode with its own interface, not a reskin. `SelectionMenu` (29 functions, 26 properties),
`GameMenu` (18/2), `WeaponMenu` (12/1) and `SquadMenu` (11/21) complete the game-side set.

`SGMenuInteraction` is an outlier worth naming: **294 properties** against 27 functions. That is an
editor-tuned configuration blob — more properties than `PlayerController` (300 across 360 functions) and
more than four times `StargateHUD`. It is the single largest property set in any fork class, and it belongs
to menu navigation rather than to gameplay.

## A correction to the fork count

`docs/systems/FORK_SURFACE.md` counts the fork's insertion points by *package*: classes in `Stargate*`
packages whose super is stock. The UI breaks that heuristic. `SGListBox` and its seven subclasses live
**inside `GUI`**, and `StargateMenus` has exactly one class whose name starts with `SG`. So the fork added
classes to stock-named packages, which means:

* the 503 insertion points are a **lower bound** on the fork's script surface, not the count;
* "starts with `SG`" is not a reliable discriminator either — `StargateMenus` would look almost empty by
  that test;
* separating stock from fork needs a stock UE2.5 class table to diff against, which the repository does not
  have (already listed as a limit in `FORK_SURFACE.md`, now with a concrete example of it biting).

## What this means for the rebuild

1. **UI is the cheapest script to port and the hardest to verify.** No natives in the menu package means
   nothing blocks compilation; but a menu that compiles and does not draw the same widget is
   indistinguishable from a correct one without a running build and a screenshot path (which
   `docs/XEMU.md` shows is currently absent).
2. **Xbox Live and co-op UI ship in the build** — `XBoxLiveMenu`, `FindGame`, `PlayMenu`, `ServerList`,
   `CoopPlayerPanel` — against 10 multiplayer maps. The multiplayer slice is real, not vestigial.
3. `ReplicatorHUD` existing at 29 functions means the Replicator play mode is reachable in the shipped
   build, which is worth knowing before treating `ReplicatorQueenBot`/`BotScarab` as cut content.

## Not measured

* What any page looks like or which flow leads where: 31 `SGPageBase` subclasses are names and functions;
  the layout data is in `StargateMenus`' script defaults and its textures, not in the class graph.
* Which HUD the game actually selects for the Replicator mode, and whether it is player- or bot-only.
* The stock/fork split inside `GUI` and `UWindow` — needs the stock class table.
