# Mission objectives: a vocabulary, not a system

PLAN 8.3 lists mission objectives. The measurement says there are **two** objective families that do not
touch, and the one the campaign ships is the engine's own — while the fork's parallel family is never placed.
The mission logic itself lives in the **level's data**, expressed through a vocabulary of `ACTION_*` latent
scripted actions -- and **82 distinct `ACTION_*` classes are placed** in the shipped maps, **18,283**
placements over `GamePlay` 45, `StargateGame` 21, `StargateContent` 13 and `StargateVehicles` 3
(`inventory/map_script.csv`). The 22-class table below counts the class *tree* and is a different population
from what the maps carry, so neither replaces the other. The logic is *not* a compiled level script, which no
map in this build carries at all (`docs/MAP_SCRIPTS.md`: 0 of 73 maps hold a `Function`, `State` or
`TextBuffer` export).

Body-level description of the class the game actually uses is in `docs/systems/mission-objectives.md`
(written from the decompiled bodies); this file is the structural comparison and the content counts.

    python3 tools/class-graph.py --root GameObjective --root MissionObjective \
        --root LatentScriptedAction -o inventory

Output: `inventory/class_graph.csv` (32 rows). Reads committed CSVs only.

## The family the game uses is the engine's

`Engine.MissionObjective` (10 functions, 14 properties) has **no subclasses** — and that says nothing about
whether it is used. It is placed **261 times** across the 73 maps, by far the most-placed mission class in
the build. It is the single-player objective system, used directly rather than through a hierarchy.

## The family the fork built is never placed

`GameObjective` is a `NavigationPoint` in `StargateGame` with 10 functions and 20 properties, and 10
subclasses:

| class | functions | properties | placed in maps |
|---|---:|---:|---:|
| `CTFBase` | 7 | 4 | 0 |
| `DestroyableObjective` | 6 | 8 | 0 |
| `LockedObjective` | 5 | 2 | 0 |
| `TriggeredObjective` | 4 | 1 | 0 |
| `StargateDestroyableObjective` / `StargateLockedObjective` / `StargateTriggeredObjective` / `SGC_SecurityDoorObjective` | 0 | 0 | 0 |
| `BlueFlagBase` / `RedFlagBase` | 0 | 0 | 0 |

Zero placements across all 73 maps, including the 10 multiplayer ones. So the fork spent 22 functions on a
parallel objective hierarchy that the shipped content does not use: four of the classes are empty names, and
the three "real" ones (`Destroyable`, `Locked`, `Triggered`) are never instantiated. That is the opposite of
what the class names suggest, and it is only visible from the placement data.

## The vocabulary the missions are actually built from

| root | classes | functions | properties |
|---|---:|---:|---:|
| `LatentScriptedAction` (`ACTION_*`) | 22 | 75 | 83 |
| `GameObjective` | 10 | 22 | 15 |
| `MissionObjective` (Engine) | 0 | 0 | 0 |

The 22 actions are the mission-scripting language, and the ones carrying state are the presentation and
timing ones:

| action | functions | properties |
|---|---:|---:|
| `ACTION_PlaySGSound` | 8 | 17 |
| `ACTION_Talk` | 5 | 15 |
| `ACTION_SGWaitForPlayer` | 4 | 14 |
| `ACTION_TeleportSpecificPawn` | 1 | 6 |
| `ACTION_SquadWander` | 2 | 4 |
| `ACTION_TeleportToPoint` | 1 | 4 |
| `ACTION_DisplaySGText` | 2 | 3 |
| `ACTION_WaitForConditions` | 3 | 3 |

83 properties over 22 classes means the actions are parameterised in the editor, not in script — the level
designer chose the sound, the text, the point and the conditions. And unlike the objective classes, the
actions *are* placed, heavily:

| action | placed instances |
|---|---:|
| `ACTION_Talk` | 719 |
| `ACTION_PlaySGSound` | 121 |
| `ACTION_WaitForConditions` | 24 |

## What holds the flow together

`StoryModeGame` (super `UnrealMPGameInfo`) carries it: **51 functions, 17 properties, 3 states** — by far the
largest class in the campaign's control path. The player-facing side is `ObjectivesPanel` in
`StargateMenus` (14 functions, 5 properties, a `GUITabPanel`), fed from the objective actors' state.

So the chain is: `StoryModeGame` owns the campaign rules → the level's placed `ACTION_*` and objective
actors carry the mission → `ObjectivesPanel` renders their state. The mission content — which objective, in which order, with
which text and sound — is **data in the `.unr`**, not code.

## The chapter list is the campaign's own index

`inventory/chapters.csv` holds 37 chapter entries with their map files: **30 present on disk, 7 missing**
(indices 1, 5, 6, 34–37). Mission 1 and the last four chapters have no map, which matches the cut-content
report (`docs/CUT_CONTENT.md`): M01 and M05 keep music but no level.

## Why this matters for the rebuild

1. **Objective logic is not a script port.** Once `StoryModeGame` and the 10 objective classes compile, the
   missions still do nothing until the placed actions run — so 5.2 (the maps' T3D, which is where those
   actions and the sequences that drive them live) is on the critical path for anything playable, not a
   nice-to-have inventory step. There is no level script to port: `docs/MAP_SCRIPTS.md` measures 0 script
   exports over all 73 maps, so this half of a mission is data, not code.
2. **The fork's `GameObjective` family can be deprioritised**: 22 functions and 10 classes that no shipped
   map instantiates. Porting it before the engine's `MissionObjective` would be working on the wrong class.
3. **`MissionObjective` is the compatibility surface.** 261 placed actors mean the rebuild must reproduce
   the engine class's behaviour exactly, including whatever the placed actions set on it.

## Not measured

* What any `ACTION_*` does. 22 classes with properties named after their editor fields is structure; the
  behaviour is in the script bodies under `$WORK`.
* The order *within* a sequence, and whether a sequence is activated at all. Which actions a mission uses is
  measured (`docs/MAP_ACTION_WIRING.md`: 16,886 of the 18,510 placed actions are named by a sequence's `Actions`
  array, 1,624 by none), and it is not a level script: no map carries one (`docs/MAP_SCRIPTS.md`).
* The 7 missing chapters: whether their levels were cut, or renamed before ship (`chapters.csv` records
  what the chapter table names, not what the designers intended).
