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

## The page flow: a config root, two edge sources, and 59 pages

The interface's structure is not in the class graph. It is in a **string**: `Default.ini`, `UW.ini` and
`UnrealEngine2.ini` each state `MainMenuClass=StargateMenus.MainMenu`, `GUIController.GetMainMenuClass()` is
native and returns it, and `StoryModeGame`'s login path calls `GUI.OpenMenu(GUI.GetMainMenuClass())`. So the
root page is configuration read by a native, and everything below it is script or data.

Below the root a page is opened two ways, and only the first is a line of script:

* **a literal target** in a `Controller.OpenMenu("Pkg.Page")` / `ReplaceMenu` / `ClientOpenMenu` call — **50
  sites over 38 classes**, 47 of them in the game's own packages and 3 in stock `Engine`/`GUI` bodies that open
  the mid-game, password and chat menus. Another **22** sites pass a variable (`ButtonMenus[i]`,
  `MidGameMenuClass`, `GetMainMenuClass()`), so they name no page statically;
* **a data string** in the `ButtonMenus` array that `RightLockingMenuPanel` walks with
  `Controller.OpenMenu(ButtonMenus[i])`. That array is **not in the class's `TextBuffer`**: a class's
  `defaultproperties` are compiled into its record, which is why `tools/defaults-emit.py` has to render them
  (1,664 blocks out of 2,319 classes) before the flow can be read. **14 entries** come out of three classes —
13 page names and one empty slot —
  and `MainMenu` holds five of them — `QuickLoadMapTempPage`, both profile selectors, `OptionsPage` and
  `ExtrasPage`. Its own button switch over `CreditsBN`/`ExtrasBN` is inside a block comment in the shipped
  text, so the array, not the code, is what the main menu's buttons are.

The defaults start one level down the same page: `SGPageBase.InitComponent` builds its four
controller-button captions from `Localizer`'s defaults (`sSelect`, `sBack`, `sDelete`, `sCreateNew`) and shows
each only when the page sets `bButtonA`…`bButtonY`, which is why a page's button hints are a page property and
not a global table.

**The links that name nothing.** Five targets do not resolve, and each fails differently: three sites name
`UCMenu.*` pages (`TeaserPage`, `LiveInteractionBox`) in a package the disc does not ship — those calls can
only return false; `GUI.JoeTest` is a `UDebugMenu` scratch page no package declares; `GUI.SGWaitingForPlayers`
sits inside an `#ifdef PS2` branch the Xbox build does not compile, and even there names no class, while the
page that exists is `StargateMenus.WaitingForPlayers`; and `OptionsEditProfile`'s fourth `ButtonMenus` entry
is spelled `"StargateMenus.OptionsEditAudio)"` — a stray `)` that makes a **typo** the menu reads as a page
that does not exist, with an empty fifth slot after it. A string-keyed menu turns a renamed page into a
missing page silently, which is why this table is worth a gate.

**The closure.** Walking the resolved edges from the ini's root splits the 59 menu pages (every class outside
the stock packages whose chain reaches `SGPageBase` or extends `GUIPage` directly) three ways — and the split
is a fact about the shipped build, not about the reader:

* **34 reachable**, at depth 0 (`MainMenu`) to 6 (`SelectPawnPage`, `MultiplayerMapSelect`): the single-player
  path runs profile → chapter → tutorial, the multiplayer path profile → menu → system link/Xbox Live →
  game select → team → character → map;
* **12 opened only from outside the menu graph** — the in-game pages `StargatePlayer` opens
  (`SinglePlayerInGame`, `InGameScoreboard`, `InGameInfoScreen`, `HintScreen`, `MultiPlayerInGame`),
  `RestartGame` from `StoryModeGame` with `LoadGameMenu` behind it, `ReturnToMainPage` behind `InGameMenu`'s
  call, the save pair `SaveGameMenu`→(`OverwriteSavedGamePage`, `DeleteSavedGamePage`),
  `SinglePlayerLoadGame`, `NotYetImplementedPage`. They are reachable in play, just not from the root;
* **13 nothing opens**: `CreditsPage` (its call is the commented-out one), `InGameMenu`, `SaveGameMenu`,
  `ServerSelectionPage`, `EnterFriendNamePage`, `ErrorPage`, `QuestionPage`, `OptionsEditBase`,
  `VirtualKeyboardPage`, `TextGameInfo`, `WaitingForPlayers`, `XBoxLiveBackDrop`, and `VSMainMenu` — the last
  with a commented `AutoLoad=` line beside the live `MainMenu` one in the ini, a second main menu the config
  keeps switched off. Several of the rest are opened by class reference from a panel or an interaction rather
  than by name.

## The HUD is a string, and the Replicator one is player-only

`Engine.GameInfo` declares `var string HUDType` and resolves it in `PostBeginPlay` with
`DynamicLoadObject(HUDType, class'Core.Class')`, logging `No HUDType specified in %s` when it is empty and
`Can't find HUD class %s` when it does not resolve. So a game mode's HUD is a **name in its defaults**, and
the corpus states four of them: `Engine.HUD` (the base), `StargateGame.StargateHUD` (deathmatch),
`StargateGame.StargateTeamHUD` (team deathmatch and story mode). **No game class names `ReplicatorHUD`.**

That HUD is selected by the possession, not by the game mode. `StargateContent.ReplicatorPossess.Trigger`
swaps the controller's HUD with `DynamicLoadObject("StargateGame.ReplicatorHUD", class'Core.Class')` and its
scoreboard with `StargateGame.TeamScoreBoard`, and it does so **inside a test for a player** —
`EventInstigator != None && EventInstigator.IsPlayerPawn()`, with its own log line `replicator was triggered by
a player` — so a Replicator possessed by a bot keeps the game mode's HUD. On release the same body restores
`Level.Game.HUDType` and `Level.Game.ScoreBoardType`. The Replicator interface is therefore a
**player-possession** interface: present, reachable, and gated on a player trigger, not on the game type.

<!-- generated:menu-flow -->
**50 of the 72 `OpenMenu`-family sites carry a literal page target, 47 of those are in the game's own packages, and 6 name a class their own package does not export; 34 of the 59 menu pages are reachable from the build's own `MainMenuClass`.**

| measure | answer |
|---|---|
| packages read | **18** |
| classes carrying text | **2319** |
| classes that open a page | **38** |
| `OpenMenu`-family literal sites | **50** |
| ... in the game's own packages | **47** |
| ... inside an `#ifdef`/`#ifndef` branch | **2** |
| ... in a branch this build does not compile | **1** |
| ... all sites of that family | **72** |
| ... whose argument is not a literal | **22** |
| `ButtonMenus[n]` strings | **14** |
| sites inside a comment (cut pages) | **37** |
| `MainMenuClass` roots | **1** |
| pages reachable from a root | **34** |
| pages opened only from outside the graph | **12** |
| pages nothing opens | **13** |
| `HUDType=` assignments | **4** |
| runtime HUD swaps | **1** |

### The dead links

| target | stated by | verdict | what it is |
|---|---|---|---|
| `GUI.JoeTest` | `UDebugMenu.UDebugMenuBar` | absent | - |
| `GUI.SGWaitingForPlayers` | `StargateGame.StoryModeGame` | absent | branch not compiled by this build |
| `UCMenu.LiveInteractionBox` | `GUI.UIXInteraction` | absent-package | stock engine body |
| `UCMenu.LiveInteractionBox` | `GUI.UIXInteraction` | absent-package | stock engine body |
| `UCMenu.TeaserPage` | `GUI.GUIController` | absent-package | stock engine body |
| `StargateMenus.OptionsEditAudio)` | `StargateMenus.OptionsEditProfile` | typo | index 3 |

### Menu calls the build may not compile

| site | target | branch |
|---|---|---|
| `StargateGame.StoryModeGame` | `GUI.SGWaitingForPlayers` | `#ifdef PS2` |

### The pages, from the root down

| page | verdict | depth | openers |
|---|---|---:|---:|
| `StargateMenus.MainMenu` | reachable | 0 | 0 |
| `StargateMenus.ExtrasPage` | reachable | 1 | 1 |
| `StargateMenus.MultiplayerSelectProfile` | reachable | 1 | 1 |
| `StargateMenus.OptionsPage` | reachable | 1 | 1 |
| `StargateMenus.QuickLoadMapTempPage` | reachable | 1 | 2 |
| `StargateMenus.QuitPage` | reachable | 1 | 3 |
| `StargateMenus.SinglePlayerSelectProfile` | reachable | 1 | 1 |
| `StargateMenus.DeleteProfilePage` | reachable | 2 | 1 |
| `StargateMenus.EnterProfileNamePage` | reachable | 2 | 1 |
| `StargateMenus.MultiplayerMenu` | reachable | 2 | 1 |
| `StargateMenus.OptionsEditProfile` | reachable | 2 | 1 |
| `StargateMenus.SinglePlayerMenu` | reachable | 2 | 1 |
| `StargateMenus.CreateNewProfile` | reachable | 3 | 1 |
| `StargateMenus.OptionsEditAppearance` | reachable | 3 | 1 |
| `StargateMenus.OptionsEditAudio` | reachable | 3 | 1 |
| `StargateMenus.OptionsEditControls` | reachable | 3 | 1 |
| `StargateMenus.OptionsEditVideo` | reachable | 3 | 1 |
| `StargateMenus.SinglePlayerChapterSelect` | reachable | 3 | 2 |
| `StargateMenus.SinglePlayerNewGame` | reachable | 3 | 1 |
| `StargateMenus.SinglePlayerTutorial` | reachable | 3 | 1 |
| `StargateMenus.SystemLinkMenu` | reachable | 3 | 1 |
| `StargateMenus.XBoxLiveMenu` | reachable | 3 | 1 |
| `StargateMenus.MultiplayerGameSelect` | reachable | 4 | 2 |
| `StargateMenus.MultiplayerServerSelect` | reachable | 4 | 1 |
| `StargateMenus.XBoxLiveFriendsMenu` | reachable | 4 | 1 |
| `StargateMenus.XBoxLivePlayMenu` | reachable | 4 | 1 |
| `StargateMenus.XBoxLiveSignOutConfirm` | reachable | 4 | 1 |
| `StargateMenus.MultiplayerCharSelect` | reachable | 5 | 2 |
| `StargateMenus.NoMatchFoundPage` | reachable | 5 | 2 |
| `StargateMenus.SelectTeamPage` | reachable | 5 | 3 |
| `StargateMenus.XBoxLiveFindGameMenu` | reachable | 5 | 1 |
| `StargateMenus.XBoxLiveServerSelect` | reachable | 5 | 3 |
| `StargateMenus.MultiplayerMapSelect` | reachable | 6 | 2 |
| `StargateMenus.SelectPawnPage` | reachable | 6 | 1 |
| `StargateMenus.CreditsPage` | unopened | - | 0 |
| `StargateMenus.DeleteSavedGamePage` | outside | - | 1 |
| `StargateMenus.EnterFriendNamePage` | unopened | - | 0 |
| `StargateMenus.ErrorPage` | unopened | - | 0 |
| `StargateMenus.HintScreen` | outside | - | 1 |
| `StargateMenus.InGameInfoScreen` | outside | - | 1 |
| `StargateMenus.InGameMenu` | unopened | - | 0 |
| `StargateMenus.InGameScoreboard` | outside | - | 1 |
| `StargateMenus.LoadGameMenu` | outside | - | 1 |
| `StargateMenus.MultiPlayerInGame` | outside | - | 1 |
| `StargateMenus.NotYetImplementedPage` | outside | - | 2 |
| `StargateMenus.OptionsEditBase` | unopened | - | 0 |
| `StargateMenus.OverwriteSavedGamePage` | outside | - | 1 |
| `StargateMenus.QuestionPage` | unopened | - | 0 |
| `StargateMenus.RestartGame` | outside | - | 1 |
| `StargateMenus.ReturnToMainPage` | outside | - | 1 |
| `StargateMenus.SaveGameMenu` | unopened | - | 0 |
| `StargateMenus.ServerSelectionPage` | unopened | - | 0 |
| `StargateMenus.SinglePlayerInGame` | outside | - | 1 |
| `StargateMenus.SinglePlayerLoadGame` | outside | - | 1 |
| `StargateMenus.TextGameInfo` | unopened | - | 0 |
| `StargateMenus.VSMainMenu` | unopened | - | 0 |
| `StargateMenus.VirtualKeyboardPage` | unopened | - | 0 |
| `StargateMenus.WaitingForPlayers` | unopened | - | 0 |
| `StargateMenus.XBoxLiveBackDrop` | unopened | - | 0 |

### The HUD, which is a string

| stated by | form | target | resolves |
|---|---|---|---|
| `Engine.GameInfo` | hud | `Engine.HUD` | class |
| `StargateGame.StargateDeathMatch` | hud | `StargateGame.StargateHUD` | class |
| `StargateGame.StoryModeGame` | hud | `StargateGame.StargateTeamHUD` | class |
| `StargateGame.TeamDeathMatch` | hud | `StargateGame.StargateTeamHUD` | class |
| `StargateContent.ReplicatorPossess` | hud_swap | `StargateGame.ReplicatorHUD` | class |
<!-- /generated:menu-flow -->

## What this means for the rebuild

1. **UI is the cheapest script to port and the hardest to verify.** No natives in the menu package means
   nothing blocks compilation; but a menu that compiles and does not draw the same widget is
   indistinguishable from a correct one without a running build and a screenshot path (which
   `docs/XEMU.md` shows is currently absent).
2. **Xbox Live and co-op UI ship in the build** — `XBoxLiveMenu`, `FindGame`, `PlayMenu`, `ServerList`,
   `CoopPlayerPanel` — against 10 multiplayer maps. The multiplayer slice is real, not vestigial.
3. `ReplicatorHUD` existing at 29 functions means the Replicator play mode is reachable in the shipped
   build, which is worth knowing before treating `ReplicatorQueenBot`/`BotScarab` as cut content.
4. **The page graph is now a table, so a rebuild can be checked against it.** A rebuilt `StargateMenus` that
   drops a page, renames one without its string, or loses the `ButtonMenus` defaults changes the edge set and
   `make ci` says so. The five unresolved targets are the baseline: a rebuild that makes them resolve is a
   behaviour change, not a fix, unless the change is deliberate.

## Not measured

* **What any page draws.** The flow is measured; the layout is not — textures, styles and control geometry
  are property values (`StyleName`, `Background`, `WinWidth`…) whose *drawing* needs a running build.
* The pages opened by class reference rather than by name (the 13 the closure cannot reach): which panel
  or interaction holds the reference is a per-class property read this census does not do.
* The stock/fork split inside `GUI` and `UWindow` — needs the stock class table.
