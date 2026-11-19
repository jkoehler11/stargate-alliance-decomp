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

`SGMenuInteraction` is an outlier worth naming: **294 declared properties** against 27 functions — more
properties than `PlayerController` (300 across 360 functions) and more than four times `StargateHUD`. It is
the single largest property set in any fork class. What it *holds* is a text-style table, not a page list:
**256 of the 294 are `string`s**, and the rest are 15 floats, 8 `Color`s, 4 `Font`s, 3 bools, 3 `Texture`s,
2 ints and three one-offs — **no class-typed property at all**. Its compiled defaults block carries **18
values** (four fonts, twelve colours, two cursor/arrow textures, `bSubtitlesOn`, `bVisible` and a `MapSize`),
and no property of it names a page. So the class that looked like the likeliest place for a page table is a
style sheet the interaction reads its own drawing from.

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

## The page flow: a config root, three ways in, and 59 pages

The interface's structure is not in the class graph. It is in a **string**: `Default.ini`, `UW.ini` and
`UnrealEngine2.ini` each state `MainMenuClass=StargateMenus.MainMenu`, `GUIController.GetMainMenuClass()` is
native and returns it, and `StoryModeGame`'s login path calls `GUI.OpenMenu(GUI.GetMainMenuClass())`. So the
root page is configuration read by a native, and everything below it is script or data.

Below the root a page is opened three ways, and only the first is a line of script:

* **a literal target** in a `Controller.OpenMenu("Pkg.Page")` / `ReplaceMenu` / `ClientOpenMenu` call — **50
  sites over 38 classes**, 47 of them in the game's own packages and 3 in stock `Engine`/`GUI` bodies that open
  the mid-game, password and chat menus. Another **22** sites pass a variable (`ButtonMenus[i]`,
  `MidGameMenuClass`, `GetMainMenuClass()`), so they name no page statically;
* **a data string** in the `ButtonMenus` array that `RightLockingMenuPanel` walks with
  `Controller.OpenMenu(ButtonMenus[i])`. That array is **not in the class's `TextBuffer`**: a class's
  `defaultproperties` are compiled into its record, which is why `tools/defaults-emit.py` has to render them
  (1,664 blocks out of 2,319 classes) before the flow can be read. **14 entries** come out of three classes —
  13 page names and one empty slot — and `MainMenu` holds five of them — `QuickLoadMapTempPage`, both profile
  selectors, `OptionsPage` and `ExtrasPage`. Its own button switch over `CreditsBN`/`ExtrasBN` is inside a
  block comment in the shipped text, so the array, not the code, is what the main menu's buttons are;
* **a class reference, or a config key** — the route a page takes when its *name* is never an argument. A
  class-typed property can hold a page (`Foo=class'StargateMenus.Bar'`), a body can mention the class, and
  `GUIController` opens the mid-game, demo, admin and chat-password menus with `OpenMenu(MidGameMenuClass)`
  shaped calls whose target is an `.ini` value. All three are read: **0 class references name a page**, and of
  the **30 menu-class keys in the five shipped `.ini`s**, 10 are commented out, 8 are present with no value and
  the rest are the root. That is why the closure's unreached pages are cut pages rather than a reader's gap.

The defaults start one level down the same page: `SGPageBase.InitComponent` builds its four
controller-button captions from `Engine.Localizer`'s defaults (`sSelect` → "Select", `sBack` → "Back",
`sDelete` → "Delete", `sCreateNew` → "Create New") and calls `SetVisibility(bButtonA)`…`bButtonY` on them, so a
page's button hints are a page property and not a global table — and the census of those four flags over all 59
pages is in the generated block below. **30 pages set at least one** (35 declarations of their own and 32
inherited: `OptionsEditBase`'s A+B reach three subclasses, `VirtualKeyboardPage`'s four reach `EnterFriendNamePage`
and `EnterProfileNamePage`), so **29 pages show no controller caption at all**. All 33 `InitComponent` overrides
forward to `Super`, which is what makes a declared flag a drawn caption rather than a dead value; a *press* is
answered by whichever class names its `XC_` code, and **65 of the 67 hint flags have such a handler on the page's
chain** — the two that do not are `CreateNewProfile`'s and `OptionsEditProfile`'s `bButtonA`, i.e. both pages
print "Select" with no page-class `XC_A` handler anywhere above them.

**A page's widgets are its own; only the frame is shared.** The `Begin Object` blocks hold a page's layout, and
the census over all 59 pages is a table now: **114 blocks over 46 pages — 110 declared once by the page that
uses them and 4 that *restate* a widget an ancestor already declares**, in **31 widget classes** (all of which
the corpus carries). Inheritance carries the *frame and the captions*, not the controls: `SGPageBase`'s five
components (`cTitle`, `cButtonA`…`cButtonY`) are declared once and re-declared by no page, so every page draws
them and adds its own. The options pages make the split plain: `OptionsEditBase` declares A+B and its three
subclasses (`OptionsEditAudio`, `OptionsEditVideo`, `OptionsEditAppearance`) inherit both — yet they share **not
one widget**: Audio declares three `moEditBox` rows, Video one `moComboBox`, Appearance a
`CharacterSelectionPanel`+`CurrentCharPanel` pair, and the base itself none; `OptionsEditProfile` and
`OptionsEditControls`, which extend `SGPageBase` directly, add one `RightLockingMenuPanel` and nothing at all. A
subclass replaces an inherited control the way it replaces a default: by restating its `Name=` with a narrower
class, which is what
`MultiplayerMapSelect` and `QuickLoadMapTempPage` do to `SinglePlayerChapterSelect`'s `chPN`
(`ChapterSelectionPanel` → `MapSelectionPanel`), and `MultiplayerServerSelect` → `XBoxLiveServerSelect` do again
on top of that (`ServerSelectionPanel` → `MultiplayerServerPanel` → `XBoxLiveServerSelectionPanel`). Geometry is
per widget as well: **83 of the 114 blocks state all four `Win*` values** and the other **25** state none and
take theirs from the widget class's own defaults (`GUI.GUIButton`, `GUILabel`, …) — **not one** component is
left without a position anywhere on its chain.

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
* **13 nothing opens**, and the reason is measured rather than inferred: a page can also be reached by a
  *class* reference (`Foo=class'StargateMenus.Bar'` in a defaults block, or `class'..'` in a body) or through
  an `.ini` key, so all three were read. **No class reference in the corpus names a page** — zero of 1,664
  emitted defaults blocks and zero of 2,319 class bodies — and **every menu-class key in the shipped `.ini`s
  is the root, commented out, or empty**: `MidGameMenuClass`, `DemoMenuClass`, `AdminMenuClass` and
  `ChatPasswordMenuClass` are present with no value in both `DefUser.ini` and `User.ini`, which is what the
  22 computed `OpenMenu(MidGameMenuClass)`-shaped sites read. Three of the thirteen are named **only inside a
  comment**: `CreditsPage` (MainMenu's shipped body is inside a block comment), `SaveGameMenu`
  (`SinglePlayerInGame`'s call is commented out — and `OverwriteSavedGamePage` and `DeleteSavedGamePage` sit
  behind it, so that whole save sub-tree hangs off a comment) and `WaitingForPlayers` (`StoryModeGame`'s call
  is commented, next to an `#ifdef PS2` site that names the class `GUI.SGWaitingForPlayers` instead). The other
  **10 are named nowhere at all** — not by a call, a string, a class reference or a config key:
  `InGameMenu`, `EnterFriendNamePage`, `ErrorPage`, `QuestionPage`, `OptionsEditBase`, `ServerSelectionPage`,
  `VirtualKeyboardPage`, `TextGameInfo`, `XBoxLiveBackDrop` and `VSMainMenu` (the last with a commented
  `AutoLoad=` beside the live `MainMenu` one, a second main menu the config keeps switched off).


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
**50 of the 72 `OpenMenu`-family sites carry a literal page target, 47 of those are in the game's own packages, and 6 name a class their own package does not export; 34 of the 59 menu pages are reachable from the build's own `MainMenuClass` and the rest split three ways -- 0 are named only by a string that is not a call, 3 only inside a comment, and 10 nothing names at all.**

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
| class references naming a page (defaults + bodies) | **0** |
| strings naming a page outside a menu call | **39** |
| ... of those inside a comment | **32** |
| ... that name no package at all | **7** |
| menu-class keys in the shipped `.ini`s | **30** |
| ... present with no value | **8** |
| ... commented out | **10** |
| pages reachable from a root | **34** |
| pages opened only from outside the graph | **12** |
| pages named only by a non-call string | **0** |
| pages named only inside a comment | **3** |
| pages nothing names at all | **10** |
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
| `StargateMenus.DeleteSavedGamePage` | outside | - | 1 |
| `StargateMenus.HintScreen` | outside | - | 1 |
| `StargateMenus.InGameInfoScreen` | outside | - | 1 |
| `StargateMenus.InGameScoreboard` | outside | - | 1 |
| `StargateMenus.LoadGameMenu` | outside | - | 1 |
| `StargateMenus.MultiPlayerInGame` | outside | - | 1 |
| `StargateMenus.NotYetImplementedPage` | outside | - | 2 |
| `StargateMenus.OverwriteSavedGamePage` | outside | - | 1 |
| `StargateMenus.RestartGame` | outside | - | 1 |
| `StargateMenus.ReturnToMainPage` | outside | - | 1 |
| `StargateMenus.SinglePlayerInGame` | outside | - | 1 |
| `StargateMenus.SinglePlayerLoadGame` | outside | - | 1 |
| `StargateMenus.CreditsPage` | comment-only | - | 0 |
| `StargateMenus.SaveGameMenu` | comment-only | - | 0 |
| `StargateMenus.WaitingForPlayers` | comment-only | - | 0 |
| `StargateMenus.EnterFriendNamePage` | unopened | - | 0 |
| `StargateMenus.ErrorPage` | unopened | - | 0 |
| `StargateMenus.InGameMenu` | unopened | - | 0 |
| `StargateMenus.OptionsEditBase` | unopened | - | 0 |
| `StargateMenus.QuestionPage` | unopened | - | 0 |
| `StargateMenus.ServerSelectionPage` | unopened | - | 0 |
| `StargateMenus.TextGameInfo` | unopened | - | 0 |
| `StargateMenus.VSMainMenu` | unopened | - | 0 |
| `StargateMenus.VirtualKeyboardPage` | unopened | - | 0 |
| `StargateMenus.XBoxLiveBackDrop` | unopened | - | 0 |

### The config keys, which are the computed calls' targets

| file | key | value | verdict |
|---|---|---|---|
| DefUser.ini | `adminmenuclass` | - | empty value |
| DefUser.ini | `chatpasswordmenuclass` | - | empty value |
| DefUser.ini | `demomenuclass` | - | empty value |
| DefUser.ini | `midgamemenuclass` | - | empty value |
| Default.ini | `autoload` | `StargateMenus.MainMenu` | class |
| Default.ini | `autoload` | `StargateMenus.VSMainMenu` | commented out |
| Default.ini | `connectingmenuclass` | `Stargategame.SGServerLoading` | package name differs in case: StargateGame |
| Default.ini | `defaultplayermenu` | `UDebugMenu.UDebugRootWindow` | class |
| Default.ini | `initialmenuclass` | `GUI.MyMainMenu` | commented out |
| Default.ini | `mainmenuclass` | `StargateMenus.MainMenu` | commented out |
| Default.ini | `mainmenuclass` | `StargateMenus.MainMenu` | class |
| UW.ini | `autoload` | `StargateMenus.MainMenu` | class |
| UW.ini | `connectingmenuclass` | `Stargategame.SGServerLoading` | package name differs in case: StargateGame |
| UW.ini | `defaultplayermenu` | `UDebugMenu.UDebugRootWindow` | class |
| UW.ini | `disconnectmenuclass` | `GUI2K4.UT2K4DisconnectOptionPage` | commented out; no such package on disk |
| UW.ini | `disconnectmenuclass` | `GUI.SGDisconnectedMenu` | commented out |
| UW.ini | `loadingclass` | `GUI2K4.UT2K4SP_LadderLoading` | commented out; no such package on disk |
| UW.ini | `mainmenuclass` | `StargateMenus.MainMenu` | class |
| UW.ini | `singleplayermenuclass` | `GUI2K4.UT2K4SP_Main` | commented out; no such package on disk |
| UnrealEngine2.ini | `autoload` | `StargateMenus.MainMenu` | class |
| UnrealEngine2.ini | `autoload` | `StargateMenus.VSMainMenu` | commented out |
| UnrealEngine2.ini | `connectingmenuclass` | `Stargategame.SGServerLoading` | package name differs in case: StargateGame |
| UnrealEngine2.ini | `defaultplayermenu` | `UDebugMenu.UDebugRootWindow` | class |
| UnrealEngine2.ini | `initialmenuclass` | `GUI.MyMainMenu` | commented out |
| UnrealEngine2.ini | `mainmenuclass` | `StargateMenus.MainMenu` | commented out |
| UnrealEngine2.ini | `mainmenuclass` | `StargateMenus.MainMenu` | class |
| User.ini | `adminmenuclass` | - | empty value |
| User.ini | `chatpasswordmenuclass` | - | empty value |
| User.ini | `demomenuclass` | - | empty value |
| User.ini | `midgamemenuclass` | - | empty value |

### What names the pages no call names

| page | holder | form | verdict |
|---|---|---|---|
| `StargateMenus.CreditsPage` | `StargateMenus.MainMenu` | comment | inside a comment |
| `StargateMenus.SaveGameMenu` | `StargateMenus.SinglePlayerInGame` | comment | inside a comment |
| `StargateMenus.WaitingForPlayers` | `StargateGame.StoryModeGame` | comment | inside a comment |

7 further value(s) look like a page name but name something else — `KeyName=MainMenu`, `StyleName=MainMenu`, which resolves to no package — and are in the artifact with an `unqualified` verdict rather than counted here.

### The controller hints each page shows

A page's hints are its `bButtonA`…`bButtonY` defaults, and 30 of the 59 pages set at least one: **67 hint rows**, 35 declared by the page itself and 32 inherited. The other **29 pages set none** and therefore draw no controller caption at all.

The inherited ones come from `StargateMenus.LoadGameMenu` (2 flags); `StargateMenus.OptionsEditBase` (6 flags); `StargateMenus.OptionsEditProfile` (2 flags); `StargateMenus.ServerSelectionPage` (4 flags); `StargateMenus.SinglePlayerChapterSelect` (4 flags); `StargateMenus.SinglePlayerSelectProfile` (6 flags); `StargateMenus.VirtualKeyboardPage` (8 flags).

| hints | pages | which |
|---|---:|---|
| A B | 19 | `StargateMenus.CreateNewProfile`, `StargateMenus.LoadGameMenu`, `StargateMenus.MultiplayerCharSelect`, `StargateMenus.MultiplayerGameSelect`, `StargateMenus.MultiplayerMapSelect`, `StargateMenus.MultiplayerServerSelect`, `StargateMenus.OptionsEditAppearance`, `StargateMenus.OptionsEditAudio`, `StargateMenus.OptionsEditBase`, `StargateMenus.OptionsEditProfile`, `StargateMenus.OptionsEditVideo`, `StargateMenus.QuickLoadMapTempPage`, `StargateMenus.SelectPawnPage`, `StargateMenus.SelectTeamPage`, `StargateMenus.ServerSelectionPage`, `StargateMenus.SinglePlayerChapterSelect`, `StargateMenus.SinglePlayerLoadGame`, `StargateMenus.WaitingForPlayers`, `StargateMenus.XBoxLiveServerSelect` |
| A B X Y | 4 | `StargateMenus.EnterFriendNamePage`, `StargateMenus.EnterProfileNamePage`, `StargateMenus.OptionsPage`, `StargateMenus.VirtualKeyboardPage` |
| B | 4 | `StargateMenus.InGameInfoScreen`, `StargateMenus.MultiplayerMenu`, `StargateMenus.SinglePlayerMenu`, `StargateMenus.SystemLinkMenu` |
| A B Y | 2 | `StargateMenus.MultiplayerSelectProfile`, `StargateMenus.SinglePlayerSelectProfile` |
| A B X | 1 | `StargateMenus.SaveGameMenu` |

| flag | caption | from |
|---|---|---|
| `bButtonA` | **Select** | `Engine.Localizer` |
| `bButtonB` | **Back** | `Engine.Localizer` |
| `bButtonX` | **Delete** | `Engine.Localizer` |
| `bButtonY` | **Create New** | `Engine.Localizer` |

| page | hints | declared |
|---|---|---|
| `StargateMenus.CreateNewProfile` | A=Select B=Back | StargateMenus.OptionsEditProfile |
| `StargateMenus.EnterFriendNamePage` | A=Select B=Back X=Delete Y=Create New | StargateMenus.VirtualKeyboardPage |
| `StargateMenus.EnterProfileNamePage` | A=Select B=Back X=Delete Y=Create New | StargateMenus.VirtualKeyboardPage |
| `StargateMenus.InGameInfoScreen` | B=Back | own |
| `StargateMenus.LoadGameMenu` | A=Select B=Back | own |
| `StargateMenus.MultiplayerCharSelect` | A=Select B=Back | own |
| `StargateMenus.MultiplayerGameSelect` | A=Select B=Back | own |
| `StargateMenus.MultiplayerMapSelect` | A=Select B=Back | StargateMenus.SinglePlayerChapterSelect |
| `StargateMenus.MultiplayerMenu` | B=Back | own |
| `StargateMenus.MultiplayerSelectProfile` | A=Select B=Back Y=Create New | StargateMenus.SinglePlayerSelectProfile |
| `StargateMenus.MultiplayerServerSelect` | A=Select B=Back | StargateMenus.ServerSelectionPage |
| `StargateMenus.OptionsEditAppearance` | A=Select B=Back | StargateMenus.OptionsEditBase |
| `StargateMenus.OptionsEditAudio` | A=Select B=Back | StargateMenus.OptionsEditBase |
| `StargateMenus.OptionsEditBase` | A=Select B=Back | own |
| `StargateMenus.OptionsEditProfile` | A=Select B=Back | own |
| `StargateMenus.OptionsEditVideo` | A=Select B=Back | StargateMenus.OptionsEditBase |
| `StargateMenus.OptionsPage` | A=Select B=Back X=Delete Y=Create New | StargateMenus.SinglePlayerSelectProfile |
| `StargateMenus.QuickLoadMapTempPage` | A=Select B=Back | StargateMenus.SinglePlayerChapterSelect |
| `StargateMenus.SaveGameMenu` | A=Select B=Back X=Delete | own |
| `StargateMenus.SelectPawnPage` | A=Select B=Back | own |
| `StargateMenus.SelectTeamPage` | A=Select B=Back | own |
| `StargateMenus.ServerSelectionPage` | A=Select B=Back | own |
| `StargateMenus.SinglePlayerChapterSelect` | A=Select B=Back | own |
| `StargateMenus.SinglePlayerLoadGame` | A=Select B=Back | StargateMenus.LoadGameMenu |
| `StargateMenus.SinglePlayerMenu` | B=Back | own |
| `StargateMenus.SinglePlayerSelectProfile` | A=Select B=Back Y=Create New | own |
| `StargateMenus.SystemLinkMenu` | B=Back | own |
| `StargateMenus.VirtualKeyboardPage` | A=Select B=Back X=Delete Y=Create New | own |
| `StargateMenus.WaitingForPlayers` | A=Select B=Back | own |
| `StargateMenus.XBoxLiveServerSelect` | A=Select B=Back | StargateMenus.ServerSelectionPage |

29 pages show no controller hint: `CreditsPage`, `DeleteProfilePage`, `DeleteSavedGamePage`, `ErrorPage`, `ExtrasPage`, `HintScreen`, `InGameMenu`, `InGameScoreboard`, `MainMenu`, `MultiPlayerInGame`, `NoMatchFoundPage`, `NotYetImplementedPage`, `OptionsEditControls`, `OverwriteSavedGamePage`, `QuestionPage`, `QuitPage`, `RestartGame`, `ReturnToMainPage`, `SinglePlayerInGame`, `SinglePlayerNewGame`, `SinglePlayerTutorial`, `TextGameInfo`, `VSMainMenu`, `XBoxLiveBackDrop`, `XBoxLiveFindGameMenu`, `XBoxLiveFriendsMenu`, `XBoxLiveMenu`, `XBoxLivePlayMenu`, `XBoxLiveSignOutConfirm`.

By page verdict, the 30 hinting pages are comment-only 2, outside 3, reachable 21, unopened 4 and the 29 silent ones break down as comment-only 1, outside 9, reachable 13, unopened 6.

### What a page's own defaults state

| measure | answer |
|---|---|
| pages with a defaults block of their own | **50** of 59 (9 state nothing) |
| top-level properties over those pages | **255** |
| ... `TitleString`, the page's caption | **39**, and **4** pages keep `SGPageBase`'s placeholder |
| `Begin Object` component blocks | **114** over 46 pages |
| properties that draw something (`StyleName`, `Background`, `BackgroundStyle`, the four `Win*` geometry values) | **16** over 9 pages |
| `URL`, a page whose default is a map | **1** page |

| page | what it states |
|---|---|
| `StargateMenus.DeleteProfilePage` | `StyleName` |
| `StargateMenus.DeleteSavedGamePage` | `StyleName` |
| `StargateMenus.HintScreen` | `Background`, `BackgroundStyle`, `BoundingType`, `ScalingType`, `WinTop` |
| `StargateMenus.InGameScoreboard` | `Background` |
| `StargateMenus.NoMatchFoundPage` | `StyleName` |
| `StargateMenus.NotYetImplementedPage` | `WinHeight`, `WinLeft`, `WinTop`, `WinWidth` |
| `StargateMenus.SinglePlayerChapterSelect` | `URL` |
| `StargateMenus.TextGameInfo` | `Background` |
| `StargateMenus.VSMainMenu` | `StyleName` |
| `StargateMenus.XBoxLiveSignOutConfirm` | `StyleName` |

### The widgets a page declares

A page's `Begin Object` blocks are its layout: each block names a widget class and the property the page binds it to, and the four `Win*` values a block states are that widget's place on the page. The base's own five (`cTitle`, `cButtonA`..`cButtonY`) are declared once, so a page draws its inherited frame plus whatever it adds — and a subclass can replace an inherited widget outright by restating its `Name=` with a different class.

| measure | answer |
|---|---|
| `Begin Object` component blocks | **114** over 46 pages |
| ... declared once, by the page itself | **110** |
| ... restating a widget an ancestor already declares | **4** |
| distinct widget classes | **31** |
| ... classes the corpus does not carry | **0** |
| ... stating all four `Win*` values on the page | **83** |
| ... stating none | **25**, of which **25** take theirs from the widget class's own defaults and **0** state none anywhere |

| page | widgets | with geometry | own | restated |
|---|---|---:|---:|---:|
| `StargateMenus.CreditsPage` | `GUILabel:cTempLB` | 1 | 1 | 0 |
| `StargateMenus.DeleteProfilePage` | `GUIButton:cBackground`, `GUILabel:cDesc`, `GUIButton:cNoButton`, `GUILabel:cProfile`, `GUIButton:cYesButton` | 5 | 5 | 0 |
| `StargateMenus.DeleteSavedGamePage` | `GUIButton:cBackground`, `GUILabel:cDesc`, `GUILabel:cFile`, `GUIButton:cNoButton`, `GUIButton:cYesButton` | 5 | 5 | 0 |
| `StargateMenus.ErrorPage` | `FullSlidingPanel:cPanel` | 0 | 1 | 0 |
| `StargateMenus.ExtrasPage` | `GUILabel:cTempLB` | 1 | 1 | 0 |
| `StargateMenus.HintScreen` | `ControllerButton:MyContinueBN`, `SGScrollTextBox:MyHintTextBox` | 2 | 2 | 0 |
| `StargateMenus.InGameInfoScreen` | `InGameInfoPanel:MyGameInfoPN` | 0 | 1 | 0 |
| `StargateMenus.InGameMenu` | `GUIPanel:MyBackPN`, `GUIButton:cExitBN` | 1 | 2 | 0 |
| `StargateMenus.InGameScoreboard` | `GUIListBox:ListBox1`, `GUIListBox:ListBox2`, `GUIListBox:ListBox3`, `GUIListBox:ListBox4`, `GUIListBox:ListBox5`, `FullSlidingPanel:cPanel` | 5 | 6 | 0 |
| `StargateMenus.LoadGameMenu` | `SaveGamePanel:cSavePN` | 1 | 1 | 0 |
| `StargateMenus.MainMenu` | `SGLabel:cBuildStamp`, `RightLockingMenuPanel:cPanel` | 1 | 2 | 0 |
| `StargateMenus.MultiPlayerInGame` | `GUIButton:cOptionsBN`, `GUIButton:cResumeGameBN` | 2 | 2 | 0 |
| `StargateMenus.MultiplayerCharSelect` | `CharacterSelectionPanel:cChar`, `CurrentCharPanel:cCurr` | 0 | 2 | 0 |
| `StargateMenus.MultiplayerGameSelect` | `CurrentGameModePanel:cCurr`, `GameModeSelectionPanel:gmPN` | 0 | 2 | 0 |
| `StargateMenus.MultiplayerMapSelect` | `MapSelectionPanel:chPN` | 0 | 0 | 1 |
| `StargateMenus.MultiplayerMenu` | `GUIButton:cEdit`, `GUILabel:cHint`, `GUIButton:cLive`, `GUIButton:cSplit`, `GUIButton:cSystem` | 5 | 5 | 0 |
| `StargateMenus.MultiplayerServerSelect` | `MultiplayerServerPanel:chPN` | 0 | 0 | 1 |
| `StargateMenus.NoMatchFoundPage` | `GUIButton:cBackground`, `GUILabel:cDesc`, `GUILabel:cDesc2`, `GUIButton:cOkButton` | 4 | 4 | 0 |
| `StargateMenus.NotYetImplementedPage` | `GUIButton:cOkBN`, `GUIButton:cQuitBackground`, `GUILabel:cQuitDesc` | 1 | 3 | 0 |
| `StargateMenus.OptionsEditAppearance` | `CharacterSelectionPanel:cChar`, `CurrentCharPanel:cCurr` | 0 | 2 | 0 |
| `StargateMenus.OptionsEditAudio` | `moEditBox:cMusic`, `moEditBox:cSoundFX`, `moEditBox:cVoice` | 3 | 3 | 0 |
| `StargateMenus.OptionsEditProfile` | `RightLockingMenuPanel:cPanel` | 0 | 1 | 0 |
| `StargateMenus.OptionsEditVideo` | `moComboBox:vpBox` | 1 | 1 | 0 |
| `StargateMenus.QuestionPage` | `FullSlidingPanel:cPanel` | 0 | 1 | 0 |
| `StargateMenus.QuickLoadMapTempPage` | `MapSelectionPanel:chPN` | 0 | 0 | 1 |
| `StargateMenus.RestartGame` | `GUIButton:cAutoLoadBN`, `GUIButton:cLoadGameBN`, `GUIButton:cRestartBN` | 3 | 3 | 0 |
| `StargateMenus.SaveGameMenu` | `SaveGamePanel:cSavePN` | 1 | 1 | 0 |
| `StargateMenus.SelectPawnPage` | `CurrentCharPanel:cCurr`, `SelectPawnPanel:cPanel`, `ControllerButton:cSelectBn` | 1 | 3 | 0 |
| `StargateMenus.SelectTeamPage` | `SelectTeamPanel:cPanel`, `ControllerButton:cSelectBn` | 1 | 2 | 0 |
| `StargateMenus.ServerSelectionPage` | `ServerSelectionPanel:chPN` | 0 | 1 | 0 |
| `StargateMenus.SinglePlayerChapterSelect` | `CurrentChapterPanel:cCurr`, `ChapterSelectionPanel:chPN` | 0 | 2 | 0 |
| `StargateMenus.SinglePlayerInGame` | `GUIButton:cRestartBN`, `GUIButton:cResumeGameBN` | 2 | 2 | 0 |
| `StargateMenus.SinglePlayerMenu` | `RightLockingMenuPanel:cPanel` | 0 | 1 | 0 |
| `StargateMenus.SinglePlayerSelectProfile` | `CurrentProfilePanel:cCurr`, `ProfilePanel:cProfile` | 0 | 2 | 0 |
| `StargateMenus.SinglePlayerTutorial` | `GUIButton:cBackground`, `GUIButton:cNoButton`, `GUILabel:cQuestion`, `GUIButton:cYesButton` | 3 | 4 | 0 |
| `StargateMenus.SystemLinkMenu` | `GUILabel:cHint`, `GUIButton:cHost`, `GUIButton:cJoin` | 3 | 3 | 0 |
| `StargateMenus.TextGameInfo` | `GUITitleBar:MyHeader`, `GUIButton:cReturnToGameButton`, `GUIScrollTextBox:caScrolltextBox`, `GUILabel:laScrollTextBoxLabel` | 3 | 4 | 0 |
| `StargateMenus.VSMainMenu` | `GUILabel:cHint`, `GUIButton:cLoadBN`, `GUIButton:cNewGameBN`, `GUIButton:cQuitButton`, `GUITitleBar:cTitle`, `GUILabel:cVersion` | 5 | 6 | 0 |
| `StargateMenus.VirtualKeyboardPage` | `VirtualKeyboardPanel:cKeyboardPN` | 1 | 1 | 0 |
| `StargateMenus.WaitingForPlayers` | `CoopPlayerPanel:myCoop` | 0 | 1 | 0 |
| `StargateMenus.XBoxLiveFindGameMenu` | `GUIButton:cFind`, `GUILabel:cGamer`, `GUILabel:cStatus` | 3 | 3 | 0 |
| `StargateMenus.XBoxLiveFriendsMenu` | `GUIButton:cFriends`, `GUILabel:cGamer`, `GUILabel:cStatus` | 3 | 3 | 0 |
| `StargateMenus.XBoxLiveMenu` | `GUIButton:cFriends`, `GUILabel:cGamer`, `GUIButton:cHost`, `GUIButton:cPlayers`, `GUIButton:cSignInOut`, `GUILabel:cStatus` | 6 | 6 | 0 |
| `StargateMenus.XBoxLivePlayMenu` | `GUIButton:cFind`, `GUILabel:cGamer`, `GUIButton:cHost`, `GUIButton:cInstant`, `GUILabel:cStatus` | 5 | 5 | 0 |
| `StargateMenus.XBoxLiveServerSelect` | `XBoxLiveServerSelectionPanel:chPN` | 1 | 0 | 1 |
| `StargateMenus.XBoxLiveSignOutConfirm` | `GUIButton:cNoButton`, `GUIButton:cQuitBackground`, `GUILabel:cQuitDesc`, `GUIButton:cYesButton` | 4 | 4 | 0 |

| page | widget | class | ancestor | its class |
|---|---|---|---|---|
| `StargateMenus.MultiplayerMapSelect` | `chPN` | `MapSelectionPanel` | `StargateMenus.SinglePlayerChapterSelect` | `ChapterSelectionPanel` |
| `StargateMenus.MultiplayerServerSelect` | `chPN` | `MultiplayerServerPanel` | `StargateMenus.ServerSelectionPage` | `ServerSelectionPanel` |
| `StargateMenus.QuickLoadMapTempPage` | `chPN` | `MapSelectionPanel` | `StargateMenus.SinglePlayerChapterSelect` | `ChapterSelectionPanel` |
| `StargateMenus.XBoxLiveServerSelect` | `chPN` | `XBoxLiveServerSelectionPanel` | `StargateMenus.MultiplayerServerSelect` | `MultiplayerServerPanel` |

### What a panel declares inside itself

A page's block places a panel; what the panel *contains* is its own class's defaults, in a second file of the same tree. So the layout is a tree and not a list: 12 of the 31 widget classes a page names declare blocks of their own, 7 are widgets whose block declares no children, and a child class can declare blocks too (`GUIVertScrollBar` states its zone and two buttons, which is the third level).

| measure | answer |
|---|---|
| widget classes a page names | **31**, used by **114** component blocks |
| ... declaring their own children | **12** |
| ... widgets, whose block declares no children | **7** |
| ... with no emitted block at all | **12** |
| nested children | **28** over **14** classes (depth 2 **24**, depth 3 **4**) |
| ... stating all four `Win*` values | **18** |
| ... stating none | **10**, all resolved on the child's own class chain |
| ... a rectangle that runs past the unit square | **13** of the 18 that state one |

| panel class | package | children | child classes | stating a rectangle |
|---|---|---:|---:|---:|
| `ControllerButton` | GUI | 1 | 1 | 0 |
| `GUIScrollTextBox` | GUI | 2 | 2 | 0 |
| `GUIVertScrollBar` | GUI | 4 | 3 | 0 |
| `SGScrollTextBox` | GUI | 1 | 1 | 0 |
| `CurrentChapterPanel` | StargateMenus | 3 | 2 | 3 |
| `CurrentCharPanel` | StargateMenus | 2 | 2 | 2 |
| `CurrentGameModePanel` | StargateMenus | 2 | 2 | 2 |
| `CurrentProfilePanel` | StargateMenus | 2 | 2 | 2 |
| `InGameInfoPanel` | StargateMenus | 1 | 1 | 1 |
| `MapSelectionPanel` | StargateMenus | 3 | 3 | 3 |
| `SaveGamePanel` | StargateMenus | 1 | 1 | 0 |
| `ServerSelectionPanel` | StargateMenus | 4 | 2 | 4 |
| `VirtualKeyboardPanel` | StargateMenus | 2 | 2 | 1 |

The 7 classes that declare no children are widgets, not empty panels: `GUIButton`, `GUILabel`, `GUIPanel`, `GUITitleBar`, `SGLabel`, `moComboBox`, `moEditBox`. Their block states properties (`Caption`, `StyleName`, the rectangle), never a child.

The panels the tree does not carry are not script-built: each has a defaults *block* in its package (121-129 declared properties) that the reader refuses at the start, so the emitted tree has no file for it and its contents stay unknown. `inventory/class_defaults_misses.csv` holds the reader's own verdict, joined here per class.

| widget class | package | declared properties | reader verdict | read before refusing |
|---|---|---:|---|---:|
| `GUIListBox` | GUI | 122 | `single-declared` | 1 |
| `ChapterSelectionPanel` | StargateMenus | 122 | `type-contradiction` | 1 |
| `CharacterSelectionPanel` | StargateMenus | 122 | `type-contradiction` | 1 |
| `CoopPlayerPanel` | StargateMenus | 128 | `type-contradiction` | 1 |
| `FullSlidingPanel` | StargateMenus | 124 | `single-declared` | 1 |
| `GameModeSelectionPanel` | StargateMenus | 121 | `type-contradiction` | 1 |
| `MultiplayerServerPanel` | StargateMenus | 129 | `empty-block` | 45 |
| `ProfilePanel` | StargateMenus | 121 | `type-contradiction` | 1 |
| `RightLockingMenuPanel` | StargateMenus | 124 | `type-contradiction` | 1 |
| `SelectPawnPanel` | StargateMenus | 122 | `type-contradiction` | 1 |
| `SelectTeamPanel` | StargateMenus | 123 | `type-contradiction` | 1 |
| `XBoxLiveServerSelectionPanel` | StargateMenus | 129 | `type-contradiction` | 1 |

**What the `Win*` numbers are relative to.** The base `GUIComponent` states **2** of the four (`WinWidth`, `WinHeight`) and a panel class states none, so every panel *is* its container: the two values the base does state make its rectangle its parent's whole box. Two measurements then say the numbers are read against that box and not against the screen. First, 13 of the 18 children that state a rectangle run past the unit square on one axis — nothing clamps them, so the range is not a screen fraction the engine keeps inside the display. Second, 12 widget classes are used by more than one page and 5 of those are placed at more than one page-side rectangle, while the children's numbers are one set — a class has one defaults block, so a child's `Win*` cannot be a screen position, only an offset inside whichever instance box the parent draws. The screen position is the composed product down the tree, which is what `GUIComponent`'s own native body computes and what `docs/UNKNOWNS.md` U27 still wants decoded.

### The wiring, and the pages that replace it

| measure | answer |
|---|---|
| pages replacing one of the four wiring functions | **34** of 59 |
| ... `InitComponent` | **33**, of which **33** call `Super.InitComponent` |
| ... replacing it without the `Super` call | **0** |
| ... `InternalOnXControllerEvent` | **18** pages handle the controller themselves |

The `InitComponent` overrides: `CreateNewProfile`, `HintScreen`, `InGameInfoScreen`, `InGameMenu`, `InGameScoreboard`, `LoadGameMenu`, `MainMenu`, `MultiplayerCharSelect`, `MultiplayerGameSelect`, `MultiplayerMenu`, `OptionsEditAppearance`, `OptionsEditAudio`, `OptionsEditBase`, `OptionsEditProfile`, `OptionsEditVideo`, `OptionsPage`, `QuestionPage`, `RestartGame`, `SaveGameMenu`, `SelectPawnPage`, `SelectTeamPage`, `ServerSelectionPage`, `SinglePlayerChapterSelect`, `SinglePlayerInGame`, `SinglePlayerSelectProfile`, `TextGameInfo`, `VSMainMenu`, `VirtualKeyboardPage`, `WaitingForPlayers`, `XBoxLiveFindGameMenu`, `XBoxLiveFriendsMenu`, `XBoxLiveMenu`, `XBoxLivePlayMenu`.

| page | replaces | codes its own body names |
|---|---|---|
| `StargateMenus.CreateNewProfile` | `InternalOnXControllerEvent` | XC_Y |
| `StargateMenus.HintScreen` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.InGameInfoScreen` | `InternalOnXControllerEvent` | no XC_ code |
| `StargateMenus.LoadGameMenu` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.MultiplayerCharSelect` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.MultiplayerGameSelect` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.MultiplayerSelectProfile` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.OptionsEditBase` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.OptionsEditProfile` | `InternalOnXControllerEvent` | XC_Y |
| `StargateMenus.OptionsPage` | `InternalOnXControllerEvent` | XC_A, XC_X |
| `StargateMenus.SaveGameMenu` | `InternalOnXControllerEvent` | XC_A, XC_X |
| `StargateMenus.SelectPawnPage` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.SelectTeamPage` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.ServerSelectionPage` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.SinglePlayerChapterSelect` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.SinglePlayerSelectProfile` | `InternalOnXControllerEvent` | XC_A, XC_Y |
| `StargateMenus.VirtualKeyboardPage` | `InternalOnXControllerEvent` | XC_A, XC_X, XC_Y |
| `StargateMenus.WaitingForPlayers` | `InternalOnXControllerEvent` | XC_A |
| `StargateMenus.VSMainMenu` | `MyKeyEvent` | no XC_ code |

Every one of the 33 `InitComponent` overrides forwards to `Super`, so the four captions are wired on every page that declares a flag — the flags are a real caption census, not a declaration the base never reads.

On the handler side the base's own body answers `XC_B`/`XC_BACK` with `CloseMenu(true,true)`, which is why Back is the one hint a page can trust without a handler of its own. For the other three the flag states the caption only: **65 of the 67 hint flags** have a class on the page's chain whose handler names the code, and the other **2** name none — that is where a press falls to the focused component's delegate, or to nothing the class tree states. The rows below are the page's own body, so a page that inherits `XC_A` from a mid class counts for that class.

| page | flag with no handler on its chain |
|---|---|
| `StargateMenus.CreateNewProfile` | `bButtonA` |
| `StargateMenus.OptionsEditProfile` | `bButtonA` |

### `SGMenuInteraction`, the 294-property class, is a style table

| measure | answer |
|---|---|
| declared properties | **294** |
| ... class-typed | **0** |
| ... the five commonest types | string 256, float 15, Color 8, Font 4, bool 3 |
| values in its emitted defaults block | **18** |
| properties naming a page | **0** |

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
5. **The hints are a visible contract, and they are the page's own.** 67 `bButtonA`…`bButtonY` flags over 30
   pages decide which of four captions each page prints, and the class tree names a handler for 65 of them. A
   rebuilt page that drops a flag loses a caption the shipped build drew — a regression no compiler sees — so
   `make ui-menu-flow` is the target that keeps that half honest alongside the flow.
6. **A page's layout is a data contract too, and the same target gates it.** The 114 component blocks are rows
   now — widget class, bound property, declared-versus-restated, and the geometry the page states — so a rebuilt
   page that drops a widget or slides one changes `inventory/menu_flow.csv` and `make ci` says so. The four
   restatements are the baseline to hold: `chPN` is a `ChapterSelectionPanel` on `SinglePlayerChapterSelect`, a
   `MapSelectionPanel` on the two pages that restate it, and a `MultiplayerServerPanel` →
   `XBoxLiveServerSelectionPanel` again on the server-select chain, so a rebuild that loses a restatement draws
   the wrong panel where the shipped build drew the narrow one.

## Not measured

* **What a page draws is counted; the drawing itself is not.** The page *frame* is inherited rather than
  per-page: `SGPageBase`'s own block carries `Background=Shader'Engine.MM_main_menu_S'`,
  `BackgroundStyle=ISTY_ScaledBackground` and `StyleName=MainMenu`, and only **nine pages state anything visual
  at all — 16 rows** over 255 top-level properties (16 of 255 is the whole of a page's own visual data):
  `StyleName` on five, `Background` on three, `HintScreen`'s `BackgroundStyle`/`BoundingType`/`ScalingType`/
  `WinTop` and `NotYetImplementedPage`'s four `Win*` geometry values. The widgets themselves are **114
  `Begin Object` component blocks over 46 pages**, now a table of the widget class, the property the page binds
  it to, whether the page declares it or restates an inherited one, and how much of its own geometry it states;
  four pages still carry `SGPageBase`'s placeholder title (`Basic SG GUI Page: Insert Title`) and 9 of the 59
  pages state no defaults at all. So the *data* behind a page — its frame, its captions, its components, their
  classes and their geometry — is counted; whether a page draws the layout that data describes is what still
  needs a running build.
* **Why the ten are dead is a *where*, not a *why*.** The corpus names none of them — measured over every
  class body, every emitted defaults block and every menu key in the five shipped `.ini`s — but which editor
  pass cut them, and whether the cut pages' bodies still compile against the classes around them, is a
  question about the build's history that the build does not state. `InGameMenu` is the interesting one: the
  only page nothing names, while its own body opens `QuitPage` and `ReturnToMainPage`, so the in-game pause
  menu is present, complete and unreachable — the `MidGameMenuClass=` key it would be reached through is
  empty.
* **The one live non-root config value names a non-page.** `ConnectingMenuClass=Stargategame.SGServerLoading`
  resolves (the package differs only in case) to a class whose chain is `Vignette` via `SGLoadingPageBase`, so
  the connecting "menu" is a cutscene class and the page family has no member for it. Whether it draws a
  menu-shaped frame is a question for a running build.
* The stock/fork split inside `GUI` and `UWindow` — needs the stock class table.

