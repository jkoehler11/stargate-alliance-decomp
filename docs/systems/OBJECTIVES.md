# Mission objectives: a vocabulary, not a system

PLAN 8.3 lists mission objectives. The measurement says the objectives are not implemented as a system at
all: there are 10 objective classes — 4 of them empty stubs — and the mission logic lives in **level
script**, expressed through a vocabulary of 22 `ACTION_*` latent scripted actions.

    python3 tools/class-graph.py --root GameObjective --root MissionObjective \
        --root LatentScriptedAction -o inventory

Output: `inventory/class_graph.csv` (32 rows). Reads committed CSVs only.

## The objective classes are mostly placeholders

`GameObjective` is a `NavigationPoint` in `StargateGame` with 10 functions and 20 properties, and it has 10
subclasses:

| class | functions | properties | what it is |
|---|---:|---:|---|
| `CTFBase` | 7 | 4 | multiplayer flag base |
| `DestroyableObjective` | 6 | 8 | objective that is destroyed rather than used |
| `LockedObjective` | 5 | 2 | objective gated behind a key/condition |
| `TriggeredObjective` | 4 | 1 | objective fired by a trigger |
| `StargateDestroyableObjective` | 0 | 0 | empty |
| `StargateLockedObjective` | 0 | 0 | empty |
| `StargateTriggeredObjective` | 0 | 0 | empty |
| `SGC_SecurityDoorObjective` | 0 | 0 | empty |
| `BlueFlagBase` / `RedFlagBase` | 0 | 0 | multiplayer team bases |

Ten classes, 22 functions total. Four are names with nothing behind them, and three more are multiplayer
flag bases subclassing the single-player objective class — the same class serves both modes, which is why
`StoryModeGame` is the only place the campaign differs.

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
designer chose the sound, the text, the point and the conditions. `MissionObjective` in `Engine` has **no
subclasses at all**, so the engine's own objective machinery is unused.

## What holds the flow together

`StoryModeGame` (super `UnrealMPGameInfo`) carries it: **51 functions, 17 properties, 3 states** — by far the
largest class in the campaign's control path. The player-facing side is `ObjectivesPanel` in
`StargateMenus` (14 functions, 5 properties, a `GUITabPanel`), fed from the objective actors' state.

So the chain is: `StoryModeGame` owns the campaign rules → level script places `ACTION_*` and objective
actors → `ObjectivesPanel` renders their state. The mission content — which objective, in which order, with
which text and sound — is **data in the `.unr`**, not code.

## The chapter list is the campaign's own index

`inventory/chapters.csv` holds 37 chapter entries with their map files: **30 present on disk, 7 missing**
(indices 1, 5, 6, 34–37). Mission 1 and the last four chapters have no map, which matches the cut-content
report (`docs/CUT_CONTENT.md`): M01 and M05 keep music but no level.

## Why this matters for the rebuild

1. **Objective logic is not a script port.** Once `StoryModeGame` and the 10 objective classes compile, the
   missions still do nothing until the level script runs — so 5.2 (T3D of level script) is on the critical
   path for anything playable, not a nice-to-have inventory step.
2. **The 4 empty stubs and 3 MP flag bases** mean the "objective system" is 3 classes of real behaviour.
   Estimating it from the class count would be wrong in the same way the vehicles package was.
3. `MissionObjective` being unused is a small but useful signal: the fork did not adopt the engine's own
   objective framework, so there is no compatibility surface to preserve there.

## Not measured

* What any `ACTION_*` does. 22 classes with properties named after their editor fields is structure; the
  behaviour is in the script bodies under `$WORK`.
* Which actions a given mission uses, and in what order — level script, i.e. the 5.2 dump.
* The 7 missing chapters: whether their levels were cut, or renamed before ship (`chapters.csv` records
  what the chapter table names, not what the designers intended).
