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
the census over all 59 pages is a table now: **135 blocks over 46 pages — 130 declared once by the page that
uses them and 5 that *restate* a widget an ancestor already declares**, in **31 widget classes** (all of which
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
per widget as well: **102 of the 135 blocks state all four `Win*` values** and the other **25** state none and
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
| `Begin Object` component blocks | **135** over 46 pages |
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
| `Begin Object` component blocks | **135** over 46 pages |
| ... declared once, by the page itself | **130** |
| ... restating a widget an ancestor already declares | **5** |
| distinct widget classes | **31** |
| ... classes the corpus does not carry | **0** |
| ... stating all four `Win*` values on the page | **102** |
| ... stating none | **25**, of which **25** take theirs from the widget class's own defaults and **0** state none anywhere |

| page | widgets | with geometry | own | restated |
|---|---|---:|---:|---:|
| `StargateMenus.CreditsPage` | `GUILabel:cTempLB` | 1 | 1 | 0 |
| `StargateMenus.DeleteProfilePage` | `GUIButton:cBackground`, `GUILabel:cDesc`, `GUIButton:cNoButton`, `GUILabel:cProfile`, `GUIButton:cYesButton` | 5 | 5 | 0 |
| `StargateMenus.DeleteSavedGamePage` | `GUIButton:cBackground`, `GUILabel:cDesc`, `GUILabel:cFile`, `GUIButton:cNoButton`, `GUIButton:cYesButton` | 5 | 5 | 0 |
| `StargateMenus.ErrorPage` | `GUILabel:cDesc`, `GUIButton:cOkayButton`, `FullSlidingPanel:cPanel` | 2 | 3 | 0 |
| `StargateMenus.ExtrasPage` | `GUILabel:cTempLB` | 1 | 1 | 0 |
| `StargateMenus.HintScreen` | `ControllerButton:MyContinueBN`, `SGScrollTextBox:MyHintTextBox` | 2 | 2 | 0 |
| `StargateMenus.InGameInfoScreen` | `InGameInfoPanel:MyGameInfoPN` | 0 | 1 | 0 |
| `StargateMenus.InGameMenu` | `GUIPanel:MyBackPN`, `GUIButton:cExitBN` | 1 | 2 | 0 |
| `StargateMenus.InGameScoreboard` | `GUIListBox:ListBox1`, `GUIListBox:ListBox2`, `GUIListBox:ListBox3`, `GUIListBox:ListBox4`, `GUIListBox:ListBox5`, `FullSlidingPanel:cPanel`, `GUILabel:cTitle`, `GUILabel:cTitle2` | 5 | 7 | 1 |
| `StargateMenus.LoadGameMenu` | `SaveGamePanel:cSavePN` | 1 | 1 | 0 |
| `StargateMenus.MainMenu` | `SGLabel:cBuildStamp`, `GUIButton:cContinueBN`, `GUIButton:cExtrasBN`, `GUIButton:cMultiBN`, `GUIButton:cOptionsBN`, `RightLockingMenuPanel:cPanel`, `GUIButton:cSingleBN` | 6 | 7 | 0 |
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
| `StargateMenus.OptionsEditProfile` | `GUIButton:cAppearance`, `GUIButton:cAudio`, `GUIButton:cControls`, `moEditBox:cName`, `RightLockingMenuPanel:cPanel`, `GUIButton:cVideo` | 5 | 6 | 0 |
| `StargateMenus.OptionsEditVideo` | `moComboBox:vpBox` | 1 | 1 | 0 |
| `StargateMenus.QuestionPage` | `GUILabel:cDesc`, `GUIButton:cNoButton`, `FullSlidingPanel:cPanel`, `GUIButton:cYesButton` | 3 | 4 | 0 |
| `StargateMenus.QuickLoadMapTempPage` | `MapSelectionPanel:chPN` | 0 | 0 | 1 |
| `StargateMenus.RestartGame` | `GUIButton:cAutoLoadBN`, `GUIButton:cLoadGameBN`, `GUIButton:cRestartBN` | 3 | 3 | 0 |
| `StargateMenus.SaveGameMenu` | `SaveGamePanel:cSavePN` | 1 | 1 | 0 |
| `StargateMenus.SelectPawnPage` | `CurrentCharPanel:cCurr`, `SelectPawnPanel:cPanel`, `ControllerButton:cSelectBn` | 1 | 3 | 0 |
| `StargateMenus.SelectTeamPage` | `SelectTeamPanel:cPanel`, `ControllerButton:cSelectBn` | 1 | 2 | 0 |
| `StargateMenus.ServerSelectionPage` | `ServerSelectionPanel:chPN` | 0 | 1 | 0 |
| `StargateMenus.SinglePlayerChapterSelect` | `CurrentChapterPanel:cCurr`, `ChapterSelectionPanel:chPN` | 0 | 2 | 0 |
| `StargateMenus.SinglePlayerInGame` | `GUIButton:cRestartBN`, `GUIButton:cResumeGameBN` | 2 | 2 | 0 |
| `StargateMenus.SinglePlayerMenu` | `GUIButton:cChapterBN`, `GUIButton:cLoadBN`, `GUIButton:cNewGameBN`, `RightLockingMenuPanel:cPanel`, `GUIButton:cTutorialBN` | 4 | 5 | 0 |
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
| `StargateMenus.InGameScoreboard` | `cTitle` | `GUILabel` | `StargateMenus.SGPageBase` | `SGLabel` |
| `StargateMenus.MultiplayerMapSelect` | `chPN` | `MapSelectionPanel` | `StargateMenus.SinglePlayerChapterSelect` | `ChapterSelectionPanel` |
| `StargateMenus.MultiplayerServerSelect` | `chPN` | `MultiplayerServerPanel` | `StargateMenus.ServerSelectionPage` | `ServerSelectionPanel` |
| `StargateMenus.QuickLoadMapTempPage` | `chPN` | `MapSelectionPanel` | `StargateMenus.SinglePlayerChapterSelect` | `ChapterSelectionPanel` |
| `StargateMenus.XBoxLiveServerSelect` | `chPN` | `XBoxLiveServerSelectionPanel` | `StargateMenus.MultiplayerServerSelect` | `MultiplayerServerPanel` |

### What a panel declares inside itself

A page's block places a panel; what the panel *contains* is its own class's defaults, in a second file of the same tree. So the layout is a tree and not a list: 12 of the 31 widget classes a page names declare blocks of their own, 7 are widgets whose block declares no children, and a child class can declare blocks too (`GUIVertScrollBar` states its zone and two buttons, which is the third level).

| measure | answer |
|---|---|
| widget classes a page names | **31**, used by **135** component blocks |
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

The panels this page once listed as carried by no file are **not** classes whose block the reader refuses: each block is one tag, sitting exactly where its record's closing group ends and ending on the payload's last byte, and that tag names a child object the panel's own class owns (`ChapterList'chList'`, `GUILabel'cHint'`, `SGListBox'cListBox'`, ...). The emitter now writes each of these files: its class-level locator treats the record's closing group as a rule (`docs/CLASS_DEFAULTS.md`, *The emitter takes the guard*), so every panel with a non-empty block publishes its one tag at the group's end, and the subobject walk follows the pointer. Before that change the locator ranked a three-tag parse *inside* the record's closing group above the block at the group's end -- the same ranking the misses ledger used to call these `type-contradiction`.

| widget class | package | declared properties | reader verdict | its block's tag |
|---|---|---:|---|---|
| `GUIListBox` | GUI | 122 | `single-declared` | `List:Object` |
| `ChapterSelectionPanel` | StargateMenus | 122 | `single-declared` | `ChapterList:Object` |
| `CharacterSelectionPanel` | StargateMenus | 122 | `single-declared` | `CharList:Object` |
| `CoopPlayerPanel` | StargateMenus | 128 | `single-declared` | `PlayerListBox:Object` |
| `FullSlidingPanel` | StargateMenus | 124 | `single-declared` | `Background:Object` |
| `GameModeSelectionPanel` | StargateMenus | 121 | `single-declared` | `ModeList:Object` |
| `MultiplayerServerPanel` | StargateMenus | 129 | `empty-block` | none (empty block) |
| `ProfilePanel` | StargateMenus | 121 | `single-declared` | `ProfileList:Object` |
| `RightLockingMenuPanel` | StargateMenus | 124 | `single-declared` | `HintLB:Object` |
| `SelectPawnPanel` | StargateMenus | 122 | `single-declared` | `PawnListBox:Object` |
| `SelectTeamPanel` | StargateMenus | 123 | `single-declared` | `TeamListBox:Object` |
| `XBoxLiveServerSelectionPanel` | StargateMenus | 129 | `single-declared` | `ServerListBox:Object` |

**The trade that withholds them is measured, not asserted.** `tools/defaults-locator-guard.py` (`inventory/defaults_locator_guard.csv`, 314 rows) runs the emitter's chooser and the record-group rule over the same candidate sets. With the rule, **313** classes publish a start the emitter has none for (**227** move one, **86** had none), **313 of the 313** end on the payload's last byte and **313** hold exactly one tag; **194 of the moved** unguarded starts sat *inside* the record's closing group, so what the emitter writes for those today is the record's own bytes. **1** class loses its block, 11 of the panels above are in that set, and the rule publishes a start while the two-property floor still withholds "fully declared": a file per panel, `single-declared` in the misses ledger. `tools/defaults-emit.py` now takes the rule, so a panel with a non-empty block has a file and the tree places its list from it.

**All 140 of the page-level bindings resolve.** A page binds each widget twice: the `Begin Object ... Name=` block places it, and a property points at it (`MenuPanel=RightLockingMenuPanel'cPanel'`, `MyComponents(0)=GUIButton'cContinueBN'`). The second form points *into* a panel's block, but the object it names is a subobject of the **page** (its Outer is the page class, and its rectangle is in its own block) — so a reader that stops at the panel's contents loses those controls, which is what a dangling binding was. Following one more level places all 140 of them: 140 resolved, 0 dangling.

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

<!-- generated:list-rows -->
### A list's row: the rectangle is native, and the columns are script

A page's `Begin Object` block places the list; where each row's *columns* sit is the list's own drawing code, which the corpus carries as the `OnDrawItem` delegate body `MyOnDrawItem`. **6 bodies** are declared and **6 wirings** assign one -- **1 of them commented out** -- and the **5 live** bodies draw **15 columns**.

**The rectangle a body receives is not in the corpus.** `GUI.GUIListBase` is a `native` class, and the delegate's own declaration states the contract: the floats are *"the pos/dimension of the item to be drawn"* and the return is *"Return true to shortcircuit the native DrawItem"*. That makes the return value the row's visible difference: a **true** body draws the row itself and the native item draw is skipped (**2** bodies); a **false** body paints over the native item (**3**).

| body class | declaration | returns | columns | what it does to the row |
|---|---|---|---:|---|
| `CoopPlayerPanel` | `bool(7)` | `replace` | 4 | returns true, so the native item draw is skipped; draws the row background itself; selection is a drawn state, not a style |
| `ObjectivesPanel` | `bool(7)` | `overlay` | 1 | returns false, so the native item draw still runs; selection is a drawn state, not a style; `COLUMN_INDENT = 0.03` indents a nested row |
| `SelectTeamPanel` | `bool(7)` | `overlay` | 2 | returns false, so the native item draw still runs |
| `ServerList` | `bool(7)` | `replace` | 5 | returns true, so the native item draw is skipped; draws the selection rectangle itself, so an unselected row is the native draw; selection is a drawn state, not a style |
| `ServerSelectionListBox` | `bool(7)` | `overlay` | 3 | returns false, so the native item draw still runs; selection is a drawn state, not a style |
| `SavedGameList` | `bool(7)` | `unwired` | 2 | declaration and wiring are both inside a block comment; draws the row background itself; selection is a drawn state, not a style |

**Three placements, and only one of them is relative to the row.** A cell-table call (`GetCellLeftWidth`) places **9** columns, a screen fraction (`Canvas.ClipX * f`) places **6**, and a row fraction (`W/n`) places **2** -- the row-fraction pair is the commented body's, so **2** of these columns never draw.

| body class | # | placement | value | field | note |
|---|---:|---|---|---|---|
| `CoopPlayerPanel` | 0 | cell | `cell 0` | `Name` | aligned left |
| `CoopPlayerPanel` | 1 | cell | `cell 1` | `ID` | aligned left |
| `CoopPlayerPanel` | 2 | cell | `cell 2` | `Character` | aligned left |
| `CoopPlayerPanel` | 3 | cell | `cell 3` | `Ready` | aligned left |
| `ObjectivesPanel` | 0 | canvas | `L=0.005 W=row` | `Description` | aligned left |
| `SavedGameList` | 0 | row | `1/2` | `GetPrettyDate` | aligned left |
| `SavedGameList` | 1 | row | `1/2` | `GetTypeString` | aligned left |
| `SelectTeamPanel` | 0 | canvas | `L=0.01 W=0.22` | `TeamName` | aligned left |
| `SelectTeamPanel` | 1 | canvas | `L=0.01+0.22+0.01 W=0.05` | `NumPlayers` | only when bDisplayNumPlayers; aligned right |
| `ServerList` | 0 | cell | `cell 0` | `ServerName` | aligned left |
| `ServerList` | 1 | cell | `cell 1` | `IP` | aligned left |
| `ServerList` | 2 | cell | `cell 2` | `GameType` | aligned left |
| `ServerList` | 3 | cell | `cell 3` | `MapName` | aligned left |
| `ServerList` | 4 | cell | `cell 4` | `NumPlayers` | aligned left |
| `ServerSelectionListBox` | 0 | canvas | `L=0.015 W=0.31` | `MapName` | aligned left |
| `ServerSelectionListBox` | 1 | canvas | `L=0.015+0.31+0.01 W=0.28` | `GameTypeName` | aligned left |
| `ServerSelectionListBox` | 2 | canvas | `L=0.015+0.31+0.01+0.28+0.01 W=0.09` | `CurrentPlayers/MaxPlayers` | aligned left |

**Where the list reaches the screen: a block, a string, or nothing.** A body draws whatever list its class holds, and the layout tree holds only what a `Begin Object` block places -- **4** of the **6** classes are placed that way, **1** is created from a *string* in code, and **1** nothing the corpus states names -- no emitted block, no string in any of the 18 packages, and no `.ini` key. The `StargateMenus` classes whose block the emitter refuses sit outside the block scan.

| body class | reached by | holder |
|---|---|---|
| `CoopPlayerPanel` | block | WaitingForPlayers |
| `SavedGameList` | block | SaveGamePanel |
| `SelectTeamPanel` | block | SelectTeamPage |
| `ServerSelectionListBox` | block | ServerSelectionPanel |
| `ServerList` | none | - |
| `ObjectivesPanel` | string | InGameInfoPanel |

**The cell table is empty, so the cell-placed columns have no width.** `ColumnWidths[i] = ActualWidth() * Columns[i].Percentage` is filled by `InitializeColumns`, and `Columns` reaches a list only through `GUIMultiColumnListBox.InitBaseList`'s `if ( Columns.Length > 0 )`. **0 of 1,664** emitted defaults blocks state `Columns`, and **3 of the 3** classes that *can* -- the descendants of `GUIMultiColumnList` and `GUIMultiColumnListBox` -- have an emitted block at all, so no class that could fill the table is outside the scan. `GetCellLeftWidth` therefore answers `Width = 0` for every cell of the **2** cell-placed bodies, and each of their columns starts at the list's own `ClientBounds[0]` plus whatever row `X` the caller adds.

**The row height is native, and the corpus writes one only where a class draws its own rows.** `ItemHeight`, `ItemWidth` and `ItemsPerPage` are `var(List)` on the native `GUIListBase` -- *"subclass should set in PreDraw"*, *"is set natively"* -- and the script writes are:

| class | property | written value |
|---|---|---|
| `GUI.GUIListBase` | `ItemsPerPage` | `0` |
| `GUI.moListEditBox` | `ItemHeight` | `ActualHeight()` |
| `UDebugMenu.UDebugMenuBar` | `ItemWidth` | `W` |
| `UPreview.UPreviewMapListClient` | `ItemHeight` | `25` |
| `UWindow.UWindowComboList` | `ItemHeight` | `15` |
| `UWindow.UWindowMenuBar` | `ItemWidth` | `W` |
| `UWindow.UWindowPulldownMenu` | `ItemHeight` | `LookAndFeel.Pulldown_ItemHeight` |

No menu list is in that table: `moListEditBox` sizes the list it holds to its own height, `UPreviewMapListClient` states 25 and the stock `UWindow` widgets draw their own rows, which is why they can state one. So the row a menu list hands its body is whatever the native `PreDraw` computes -- the one number of this thread the packages do not hold.

| wiring site | form | body | receiver |
|---|---|---|---|
| `SavedGameList` | commented | `MyOnDrawItem` | `List, commented out, so the body never runs` |
| `CoopPlayerPanel` | live | `MyOnDrawItem` | `List` |
| `ObjectivesPanel` | live | `MyOnDrawItem` | `ObjectivesBox.List` |
| `SelectTeamPanel` | live | `MyOnDrawItem` | `TeamListBox.List` |
| `ServerList` | live | `MyOnDrawItem` | `self` |
| `ServerSelectionListBox` | live | `MyOnDrawItem` | `List` |

**The commented wiring is a cut row, not a reader's gap.** `SavedGameList` declares `MyOnDrawItem` inside a block comment together with both of its `InitComponent` lines, so the save-game list draws the stock item -- one column, no date/type split -- and the two-column body above never runs. It is the same shape as the pages the corpus names only inside a comment.
<!-- /generated:list-rows -->

### The native half: which slot draws a row, and what it measures

`tools/ui-list-native.py` (`make ui-list-native`, `inventory/ui_list_native.csv`) reads the drawing half out of
`default.xbe`, so none of it is a boot any more. The chain is derived rather than assumed: the wide string
`OnDrawItem` is the cached `FName` index at `0x007b8144`, exactly one body loads it (`0x00300ee0`), and that
body's direct callers are the row loops -- `GUIVertList` slot 31 (`0x00301de0`, vertical) and `GUIHorzList` slot
31 (`0x00302370`, horizontal). `GUIMultiColumnList` inherits the vertical loop unchanged, so a multi-column list
draws its rows with the plain list's code and differs only in the columns its script body places.
`GUIListBase` adds exactly one slot over `GUIComponent`, **slot 58** (`0x00301c30`), and that is the stock item
draw both loops call through `+0xe8` -- the virtual an `OnDrawItem` body shortcircuits when it returns true.

**The row is the client rect plus `ItemHeight`.** The vertical loop hands the delegate
`X = ClientBounds[0]`, `Y = ClientBounds[1] + interItemGutter/2`, `W = (int)(ClientBounds[2] - ClientBounds[0])`,
`H = ItemHeight`, and advances by `ItemHeight + interItemGutter` per row; the horizontal loop swaps in `ItemWidth`
for the width and the step and centres the strip vertically when its flag is set. `bSelected` is `Index` compared
per item, so the delegate sees the same selected row the hit test does.

**`ItemHeight` is measured every frame, not stated anywhere.** `GUIVertList`'s `PreDraw` (`0x003013c0`) asks the
item style for its text height (`TextSize`), adds the style's top and bottom border offsets and `ItemPadding`, and
then counts `ItemsPerPage` by stepping `ItemHeight + interItemGutter` down the client rect, capped at 1000. A list
that states no height therefore still has one -- which is why the corpus holds no menu list's number, and why
changing a list's `ItemStyle` changes its row height.

**The field offsets were never in a package.** `Top` is `+0x258`, `Index` `+0x25c`, `ItemsPerPage` `+0x260`,
`ItemHeight` `+0x264`, `ItemWidth` `+0x268`, `interItemGutter` `+0x26c` and `ItemPadding` `+0x270`, each witnessed
by the body that stores or reads it -- the tool fails if a claimed offset does not appear in its witness.

**`ServerList` is placed by nothing, and the box is why.** A `GUIMultiColumnListBox` creates its list in
`InitComponent` from its own `DefaultListClass`, and that string's default is `"GUI.GUIMultiColumnList"`
(`GUIMultiColumnListBox`'s defaultproperties, a class the defaults emitter refuses) -- so every multi-column list
in the game is a plain `GUIMultiColumnList`, never a page's subclass, and `StargateMenus.ServerList` is cut
content with no route to the screen.

<!-- generated:list-native -->
**The row is a class-table slot, and the chain that finds it is derived from the image.** The row loops are the callers of the one body that loads the cached `OnDrawItem` index (`0x00300ee0`); the family's tables are read slot by slot in `inventory/xbe_class_tables.csv`, so `9` classes, `11` overrides and `2` row loops come out of the same scan.

| class | slots | override | slot | body | dispatches |
|---|---|---|---|---|---|
| `GUIHorzList` | | inherited | 58 | `0x00301c30` | stock item draw |
| `GUIHorzList` | | override | 31 | `0x00302370` | calls ItemCount;calls OnColumnSized;calls OnDrawItem;calls OnSortChanged |
| `GUIVertList` | | override | 31 | `0x00301de0` | calls ItemCount;calls OnDrawItem |
| `GUIMultiColumnList` | | inherited | 58 | `0x00301c30` | stock item draw |
| `GUIVertList` | | inherited | 58 | `0x00301c30` | stock item draw |

* `h` = `ItemHeight` -- the row height is the PreDraw's number, not the corpus's
* `pitch` = `ItemHeight + interItemGutter` -- the step to the next row
* `w` = `(int)(client[2] - client[0])` -- the full client width, truncated to an int
* `x` = `client[0]` -- the row starts at the list's left edge
* `y` = `client[1] + interItemGutter/2` -- half a gutter insets the first row

| field | offset | read by |
|---|---|---|
| `Index` | `0x25c` | the selected row: compared per item to pass `bSelected` |
| `ItemHeight` | `0x264` | set from the style's text height, then used as the row's H |
| `ItemPadding` | `0x270` | read into ItemHeight once, before ItemsPerPage is counted |
| `ItemWidth` | `0x268` | the horizontal loop's item width |
| `ItemsPerPage` | `0x260` | counted up by the PreDraw's fit loop, capped at 1000 |
| `Top` | `0x258` | row loop starts at [this+0x258] and stops at Top+ItemsPerPage |
| `interItemGutter` | `0x26c` | the step between rows, and half of it insets the first |

#### The box is not a list, and its item draw is another slot 58

`GUIListBoxBase` extends `GUIMultiComponent`, not `GUIListBase`: a list box is a component that hosts a list, and its `PreDraw` draws **one** item per frame through slot 58 -- the component's slot 58 (`+0xe8`), a different body from `GUIListBase`'s slot 58 at the same offset. `2` classes override that item draw, and of the `252` slot bodies in the box tree not one loads the `OnDrawItem` index or calls its dispatcher, so a body wired onto a box would never run.

| class | slot | body | what it draws |
|---|---|---|---|
| `GUIMultiColumnListBox` | 58 | `0x00301890` | its own item draw |
| `GUIListBoxBase` | 30 | `0x002fab10` | draws one item per frame |
| `GUIListBoxBase` | 58 | `0x003015b0` | the selected row |

`GUIListBox` and `GUIScrollTextBox` add no draw of their own: their tables differ from `GUIListBoxBase`'s only in slot 3, so a body wired onto one of those classes would have nowhere to be dispatched from.

* **delegate audit** -- 252 slot bodies: no box slot body loads the OnDrawItem index or calls its dispatcher, so a body wired onto a box would never run
* **page widgets** -- 10 widgets over 6 pages: GUIListBox 5, GUIScrollTextBox 1, MapFileList 1, SGScrollTextBox 1, SavedGameList 1, ServerSelectionListBox 1; the page-owned subclasses (MapFileList, SavedGameList, ServerSelectionListBox) declare their rows on a box class but wire them to the inherited List, a GUIVertList
* **receiver audit** -- 6 wirings: every receiver resolves to a GUIListBase descendant (GUIMultiColumnList 1, GUIVertList 4, ServerList 1), so the box's missing dispatch is unreachable from the shipped corpus

#### The styled-text routine: four clip culls and no width test

The box's item draw reaches `GUIStyles.DrawText` (`0x002ff790`) twice -- once per style -- and that routine's whole suppression logic is four compares against the canvas clip rect: a cell's width is used only in the `X + W` sum of one of them and in the rect handed to the canvas, so a **zero-width cell is not shortcircuited** -- it is culled only when its anchor lies outside the clip rectangle.

| measure | answer |
|---|---|
| `clip rect` | the rect is culled against this box only: right edge past the left clip, left edge past the right clip, and the same for the vertical pair |
| `font table` | the style byte indexes the font array and the body sets the canvas's font/colour for the draw, then restores the five fields it saved |
| `styled text` | the stock item draw calls it at two sites 0x55 apart with different rect arguments; the styled text of a row is that call |
| `width use` | the width reaches one clip sum and the rect handed to the canvas (vtable +0xc4); no compare in the body tests it, so a zero-width cell is culled only when its anchor lies outside the clip rectangle |

#### The canvas's own glyph loop, and the empty rect

The style layer hands the cell's rect to `Canvas`'s text entry -- slot 49, the vtable displacement `+0xc4` (`0x00101490`) -- and that entry, not the style routine, is where the rectangle becomes a clip: it switches the four clip floats to the call's rect for the length of the pass and restores them afterwards, measures the string through slot 40, and runs the glyph pass through slot 41 (`0x00103d40`), which calls 0x001036e0.

That loop, `0x001036e0`, is what decides a zero-width cell: 8 compares cull a glyph whose anchor is past the clip's extent (or whose own right or bottom is at or below zero), and a glyph that runs past the extent is **trimmed** to it -- the width becomes `(extent - glyph) / scale`, so an extent of 0 is a quad of width 0. The tile path `0x00103400` has no float compare at all: its only early exits are two null checks.

So an empty rect paints **nothing**, and no compare in the chain tests the width: in the clip's frame the first glyph has X = 0 and is trimmed to width 0, and every later glyph has X > 0 and is culled by an extent of 0. That is the last step of the zero-width cell -- the boot the page used to ask for is not needed.

| part | slot | body | what it does |
|---|---|---|---|
| `clip swap` | - | `0x3c..0x48` | the entry writes the call's rect as an origin and an extent -- 0x44 = right - left, 0x48 = bottom - top -- for the length of the pass and puts the four values back afterwards, so the rect the style layer hands down is the rectangle every glyph below is tested against |
| `glyph loop` | - | `0x001036e0` | cull if the glyph's right <= 0, the clip's extent < the glyph's X, its bottom <= 0 or the clip's lower extent < the glyph's Y |
| `glyph pass` | 41 | `0x00103d40` | the pass calls 0x001036e0, which culls a glyph whose anchor is past the clip's right or bottom edge and trims one that runs past it |
| `measure` | 40 | `0x00103c30` | the entry calls it first and moves the draw position by the width it answers |
| `text entry` | 49 | `0x00101490` | the styled draw's canvas virtual: copies the string, zeroes the cursor, swaps the clip for the call's rect, measures and draws through +0xa0/+0xa4, then restores the four clip floats |
| `tile path` | 32 | `0x00103400` | the body the glyph loop emits through has no float compare in it: its only early exits are two null checks and it scales the quad by the canvas's four scale floats, so a zero-width quad is submitted and simply covers nothing |
| `styled-text callers` | - | `0x002ff790` | 0x002ed320, 0x002f1530, 0x002f1a10, 0x002ffa80, 0x00301890, 0x00302b40, 0x003032a0 |
| `empty rect` | - | - | the pass works in the clip's own frame: the entry zeroes the cursor, so the first glyph's advance is 0 and its X is 0 -- the anchor cull does not fire and the trim answers (0 - 0)/scale = 0, a quad of no width |

`GUIMultiColumnListBox`'s item draw reaches the styled text twice, once per style: `cell pass` at `0x00301d7e`, style +0x254; `row pass` at `0x00301dd3`, style +0x58.
<!-- /generated:list-native -->

**A list box is not a list, and the difference is which slot 58 runs.** `GUIListBoxBase` extends
`GUIMultiComponent`, not `GUIListBase` -- the two trees share the `+0xe8` offset and share nothing else.
`GUIMultiComponent`'s `PreDraw` (`0x002fab10`) calls the base `PreDraw` and then its own slot 58 **once** with the
canvas; `GUIListBoxBase` overrides that slot with `0x003015b0`, which reads the list the box hosts and paints one
row -- its `Index`, `ItemsPerPage`, `ItemHeight` and `interItemGutter` come off that object, not off a rect handed
in. `GUIMultiColumnListBox` overrides it again (`0x00301890`) with the per-column path, and `GUIListBox` and
`GUIScrollTextBox` add nothing at all: of the `63` slots `GUIMultiComponent` declares, those two classes differ
from the box root in slot 3 alone.

So a delegate body attached to a box would never run, and **none is**: the box tree's 252 slot bodies contain no
load of the `OnDrawItem` index and no call to its dispatcher. What makes that harmless is where the fork put its
bodies -- `ObjectivesPanel` and `SelectTeamPanel` own an `SGListBox` and wire `ObjectivesBox.List` / `TeamListBox.List`,
and `List` is `GUIListBox`'s own `var GUIVertList`, so the receiver is the inner list, which dispatches like any
other. `SavedGameList` and `ServerSelectionListBox`, which extend `SGListBox`, are the same shape. Ten box-typed
widgets over six pages is what the corpus places (`GUIListBox` five times on `InGameScoreboard`, the scroll-text
boxes on `HintScreen` and `TextGameInfo`, and the three page-owned subclasses), and every one of their rows is a
list-tree row.

**The zero-width cell has no shortcut.** `GUIStyles.DrawText` is `native final` with token 0, so it has no table
slot and binds by name; the stock item draw reaches it at two sites (`0x00301d7e`, `0x00301dd3`, `0x55` apart) and
the routine's whole suppression logic is four compares against the canvas clip rectangle -- `X` against the clip's
right edge, `X+W` against its left, and the same pair vertically -- followed by a style-font lookup and a canvas
state save/restore. The width is used **only** in that one `X+W` sum and in the rect handed to the canvas, so a
cell of width 0 is not shortcircuited: it is culled only when its anchor falls outside the clip rect, and
otherwise reaches the canvas as a degenerate rectangle. `CoopPlayerPanel` is the one body that passes such a width
(it is the only live body that returns true and paints its own row), and its four cell columns are the ones
`GetCellLeftWidth` answers 0 for.

The degenerate rectangle is where the answer stops being the style layer's: the canvas's own text entry takes it
(slot 49, `+0xc4` at `0x00101490`), installs it as the canvas clip, and the glyph loop under it (`0x001036e0`)
trims the first glyph to a width of 0, culls every later one against a clip extent of 0, and hands the zero-width
quad to a tile path that never tests a size. So an empty cell paints nothing, from the style layer to the tile
path, and no compare in the chain tests the width.


**The header shows the same table the body does.** `GUIMultiColumnListBox.InitBaseList` gives the header the
list it belongs to (`Header.MyList = List`), so a column heading is `MyList.Columns[i]` -- the array nothing states
-- and the header's own emitted block is four values: `SizingCol=-1`, `ClickingCol=-1`, `bAcceptsInput=True` and
`StyleName=NoBackground`. Both the column it sizes and the column it answers a click on are -1, so a multi-column
list's heading text, its drag handle and its click target are all unset in the corpus, and its own style draws no
background. What the header *does* show -- a column of empty cells under the body's own rectangle -- is a question
for the boot the next section names.

## The `Win*` units: eight natives, a fraction band, and an inverse pair

`WinLeft`/`WinTop`/`WinWidth`/`WinHeight` are script floats and `GUIComponent` converts them in C++; the eight
conversions sit in the class's own table at slots 35-42 (`0x00536648`) rather than behind their exec thunks --
each `intUGUIComponentexec*` thunk stores the `-1.0f` sentinel and tail-calls `[opcode*4 + 0x6313e8]`, an
opcode-route table, so a thunk names no slot. Read from the table, each body answers three questions the data
could not: which `Win*` field stands in for an omitted argument (the `-1.0f` at `0x004daba0`), where a fraction
stops being one (`|v| < 2.0` at `0x004d21c0`, so 2 and above are pixels), and which way the owner enters.

Slots 35-38 are the `Actual*` family and answer **pixels**: `ActualLeft = owner.ActualLeft + v *
owner.ActualWidth`, `ActualWidth = v * owner.ActualWidth`. Slots 39-42 are `Relative*` and divide instead:
`RelativeLeft = (v - owner.ActualLeft) / owner.ActualWidth`. They are inverses of one mapping rather than
siblings, which is why both families read the same four fields and call the same owner slots -- and why the
family can be named from the bytes (a body that calls its own slot on the owner is an `Actual`).

An axis becomes parent-relative only where the widget's class opts in: position reads `BoundingType` (`+0x95`)
and size `ScalingType` (`+0x96`), with 1/3 opening x or width and 2/3 opening y or height, gated by the
`bConvert*` bits on the `+0x7c` flag word (`0x200000` bound x, `0x400000` bound y, `0x800000` scale x,
`0x1000000` scale y). `GUIComponent` states `SCALE_None`, so the default is the screen, and a screen-space axis
whose convert bit is set scales by `ResX`/`ResY` (`+0x218`/`+0x21c` on the controller at `+0x28`) instead of the
parent's extent.

One defect shares the read. `RelativeHeight` (`0x002f8a30`) tests `0x800000` -- the *width's* convert bit --
where `ActualHeight` (`0x002f8520`) tests `0x1000000` (the byte is `f7 46 7c 00 00 80 00` at `0x002f8a7c`), so a
component that converts its width and not its height gets an inverse height composed against a box it never
opted into.

<!-- generated:ui-layout -->
The composition is C++ and the corpus is data; both are re-derived here.

| conversion | slot | body | direction | `Win*` field | sentinel | fraction band | type values | convert bit | calls on the owner |
|---|---|---|---|---|---|---|---|---|---|
| `ActualLeft` | 35 | `0x2f85c0` | fraction->pixel | `0x110` | -1 | 2 | 1,3 | 0x200000 | 35,37 |
| `ActualTop` | 36 | `0x2f86a0` | fraction->pixel | `0x10c` | -1 | 2 | 2,3 | 0x400000 | 36,38 |
| `ActualWidth` | 37 | `0x2f8480` | fraction->pixel | `0x114` | -1 | 2 | 1,3 | 0x800000 | 37 |
| `ActualHeight` | 38 | `0x2f8520` | fraction->pixel | `0x118` | -1 | 2 | 2,3 | 0x1000000 | 38 |
| `RelativeLeft` | 39 | `0x2f8790` | pixel->fraction | `0x110` | -1 | 2 | 1,3 | 0x200000 | 35,37 |
| `RelativeTop` | 40 | `0x2f8880` | pixel->fraction | `0x10c` | -1 | 2 | 2,3 | 0x400000 | 36,38 |
| `RelativeWidth` | 41 | `0x2f8970` | pixel->fraction | `0x114` | -1 | 2 | 1,3 | 0x800000 | 37 |
| `RelativeHeight` | 42 | `0x2f8a30` | pixel->fraction | `0x118` | -1 | 2 | 2,3 | 0x800000 | 38 |

`1339` blocks over `149` classes, each axis counted where the block states a value:

| axis | stated | fraction (`abs < 2`) | pixel (`abs >= 2`) | parent-relative |
|---|---|---|---|---|
| left | 136 | 135 | 1 | 50 |
| top | 139 | 138 | 1 | 50 |
| width | 139 | 136 | 3 | 38 |
| height | 145 | 139 | 6 | 42 |

`3` stated values sit in `[1, 2)` -- above the unit square, below the pixel band, so they still scale.
<!-- /generated:ui-layout -->

**The corpus uses the band the way the band expects.** Of the 1,339 emitted `Begin Object` blocks over 149
classes, 136-145 state each axis at all: 135/138/136/139 of those are fractions and only 1/1/3/6 are pixels, and
every one of the six pixel blocks resolves against the screen (`BoundingType` and `ScalingType` both 0). The only
block that states all four in pixels is `GUI.GUIPage`'s `RemoveMeToo` (`X=GUIImage`), which places every page at
`(38, 28)` `564x424` -- 640-2*38 by 480-2*28 -- i.e. the page frame; the other five are single axes (a 36-pixel
title-bar height, a 32-pixel label height, an `8x16` arrow pair), so the band is decided per axis and not per
block. Three values sit in `[1,2)`, all exactly `1.0`: above the unit square, below the band, so they still
scale. Fifty blocks state a `BoundingType` and 42 a `ScalingType`, which is what makes x/y 50 axes and w 38 /
h 42 axes parent-relative; the rest resolve against the screen, which is why the panel classes that state no
`Win*` of their own are the whole parent box rather than a rectangle.


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
6. **A page's layout is a data contract too, and the same target gates it.** The 135 component blocks are rows
   now — widget class, bound property, declared-versus-restated, and the geometry the page states — so a rebuilt
   page that drops a widget or slides one changes `inventory/menu_flow.csv` and `make ci` says so. The five
   restatements are the baseline to hold: `chPN` is a `ChapterSelectionPanel` on `SinglePlayerChapterSelect`, a
   `MapSelectionPanel` on the two pages that restate it, and a `MultiplayerServerPanel` →
   `XBoxLiveServerSelectionPanel` again on the server-select chain, so a rebuild that loses a restatement draws
   the wrong panel where the shipped build drew the narrow one.
7. **A list's row is a native rectangle and a script body, and `make ui-list-rows` gates the script half.** The
   fifteen columns the five live bodies draw are rows in `inventory/ui_list_rows.csv` — placement, value, field —
   so a rebuilt list that moves a column, drops one, or changes a placement kind changes that artifact and
   `make ci` says so. What it cannot gate is the cell table being *empty*: no class states `Columns`, so a rebuild
   that keeps the corpus's own defaults draws the same zero-width cells the shipped build does.

## Not measured

* **What a page draws is counted; the drawing itself is not.** The page *frame* is inherited rather than
  per-page: `SGPageBase`'s own block carries `Background=Shader'Engine.MM_main_menu_S'`,
  `BackgroundStyle=ISTY_ScaledBackground` and `StyleName=MainMenu`, and only **nine pages state anything visual
  at all — 16 rows** over 255 top-level properties (16 of 255 is the whole of a page's own visual data):
  `StyleName` on five, `Background` on three, `HintScreen`'s `BackgroundStyle`/`BoundingType`/`ScalingType`/
  `WinTop` and `NotYetImplementedPage`'s four `Win*` geometry values. The widgets themselves are **135
  `Begin Object` component blocks over 46 pages**, now a table of the widget class, the property the page binds
  it to, whether the page declares it or restates an inherited one, and how much of its own geometry it states;
  four pages still carry `SGPageBase`'s placeholder title (`Basic SG GUI Page: Insert Title`) and 9 of the 59
  pages state no defaults at all. So the *data* behind a page — its frame, its captions, its components, their
  classes and their geometry — is counted; whether a page draws the layout that data describes is what still
  needs a running build. The rows *inside* a list are the third level and are counted too (the block above,
  `docs/UNKNOWNS.md` U28): **15 columns over five bodies**, placed through a cell table, the screen or the row.
  What stays unmeasured there is the rectangle itself — `GUIListBase` is native, so the row's `X/Y/W/H` and its
  `ItemHeight` are C++. The zero-width cell is answered end to end from the image: `GUIStyles.DrawText` culls against the
  canvas clip rect and nothing else, the canvas's text entry (`+0xc4`, slot 49 at `0x00101490`) installs that rect
  as the clip, and the glyph loop below it (`0x001036e0`) trims the first glyph to a width of 0 and culls the rest,
  so an empty cell paints nothing and no boot was needed for it. What still needs a boot here is the *layout*, not
  the text: whether the composed rectangle lands where the numbers say.
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

