# AI: state machines, a thin order vocabulary, and spawner-driven enemies

PLAN 8.3 lists AI (`AIScript`/`AIMsgManager`) as a system to write up. Measured from the class graph, the
system has three separable parts: a bot controller tree that carries almost all of the behaviour as
UnrealScript **states**, a small order vocabulary that is native at its base, and an enemy population that
is spawned rather than placed.

    python3 tools/class-graph.py --root AIController --root AIMessage --root Order -o inventory

Output: `inventory/class_graph.csv` (61 classes with states, functions, natives, properties, map placement
and checkpoint instances). Reads committed CSVs only.

## The tree

| root | subclasses | states | functions | native |
|---|---:|---:|---:|---:|
| `AIController` | 45 | 165 | 758 | 5 |
| `BotBase` (a subtree of the above) | 26 | 137 | 558 | 4 |
| `AIMessage` | 4 | 0 | 26 | 0 |
| `Order` | 12 | 0 | 17 | 4 |

The core of it is one class: **`BotEnemy` holds 41 states, 168 functions and 92 properties** on its own.
Next by state count are `AlkeshBomberBot` 13, `BotSG` 10, `ReetouBot` 9, `TeltakBot` 8, `PteroBossBot` 7.
`BotBase` above it has 5 states and `ScriptedController` (GamePlay) 2, so the chain is
`ScriptedController` → `BotBase` → `BotEnemy` → 26 concrete bots, with behaviour accumulating in states
rather than in functions: 165 states against 758 functions across the tree, and the count per class tracks
"how many distinct combat behaviours" far better than the function count does.

Two branches sit outside the bot tree: the turret controllers (`GoaAutoTurretController` and MK2, 5 states
each, with `AirborneTurretController` and the attached Alkesh/DeathGlider/mothership variants below them)
and the ambient creatures (`TransientAmbientCreature`, 1 state).

## Squads talk in three messages

`AIMessage` has exactly four subclasses in the whole game: the base `AIMSG_Stargate` (10 functions) and
`AIMSG_Cloak`, `AIMSG_ConcentrateFire`, `AIMSG_TakeCover`. So squad coordination is a vocabulary of three
behaviours — cloak, concentrate fire, take cover — over an `AIMsgManager` that dispatches them. That is a
small, tractable surface for the rebuild compared with the controller tree.

## Orders are mostly data

`Order` (Engine) has 12 subclasses: `SGOrder` and eleven `SGOrder*` leaves.

| class | functions | properties | native |
|---|---:|---:|---:|
| `SGOrder` | 10 | 22 | 4 |
| `SGOrderAttack` | 1 | 0 | 0 |
| `SGOrderGoto` | 2 | 0 | 0 |
| `SGOrderHold` | 2 | 0 | 0 |
| `SGOrderFollow` / `OpenFire` | 1 | 0 | 0 |
| `SGOrderDecypher` / `Engineer` / `HoldFire` / `Mission` / `TakePoint` / `Use` | 0 | 0 | 0 |

Eleven of the twelve add two functions or fewer, and six add none at all: the order vocabulary is
parameterised at `SGOrder`, and the leaves exist to be named. `SGOrder` is also the one class in the tree
where a large share of the behaviour is native (4 of 10), which is why it is the 4.9 first target —
`docs/systems/UCPP_NATIVES.md` measures why those natives are not visible in the class table.

## Enemies are spawned, not placed

No AI class is placed in any of the 73 maps: `placed_in_maps` is zero for all 45 controller classes. **What the
map does name is the encounter script that spawns them.** With the T3D written and its references resolved
(`docs/T3D.md`), `IntelligentSpawner` actors carry a `SuggestedSquadScript` pointing at a class —
`AI_Jaffa_Horus_Med_single`, `AI_Jaffa_Horus_Med_Obs`, `Jaffa_Enc5`, `TSS_7A_B_Jaffa_01` — and 61 of the 73
maps carry such names (601 distinct scripts, `inventory/squad_scripts.csv`). So the chain is
`map → spawner → squad script → its default roster → bot classes`, with the first two links measured. This
supersedes the earlier claim in this file that the question was not answerable from the `.unr` at all: it is
answerable by encounter script today, and by roster once the roster classes are read — which they are: 58 of the 65 `SQUAD_*` `SquadRoster` subclasses decode (`docs/systems/SQUADS.md`, `inventory/squad_rosters.csv`), 129 members in total, 44 of them single-member rosters. What is still missing is the last hop: a roster's `Members` array is a run of two-byte serial numbers, not references, so a count does not yet name its pawns.

They
appear in shipped checkpoints instead — `BotSG` 890 instances, `JaffaBotLight` 324, `ScriptedController` 70,
`AlkeshBomberBot` 18, `AttachedAlkeshTurretController` 18 — and the agent that puts them there is
`IntelligentSpawner`, which the checkpoint schema shows at 1,860 instances
(`docs/systems/CHECKPOINTS.md`).

That has a practical consequence for 5.2 and the rebuild: a mission's map file does not tell you which
enemies it has. The population lives in spawner configuration plus checkpoint state, so "which bots does
mission 7 use" is not answerable from the `.unr` alone.

## What the states do

The premise this page closed on -- that the state bodies are only in an extracted tree -- is wrong in one
direction: every class ships its own `ScriptText`, the original UnrealScript source, as a `TextBuffer` export
(`docs/PACKAGE_SCRIPT_TEXT.md`), and a `state` block is part of that text. So the state machines are readable
from the packages themselves, and `tools/ai-states.py` reads them with no boot, no decompiler and no compiler.

    python3 tools/ai-states.py --system DUMP/System -o inventory/ai_states.csv

Output: `inventory/ai_states.csv`, one row per state the tree's text declares, with its labels, the functions it
overrides, its `GotoState`/`Goto` targets and its latent `Sleep`s; the tables below are generated from it.
**151 states over 32 classes**, against **161 `UState` exports** for the same 32 classes.

The machine is a park-and-dispatch design, not a chain. Ten states are `auto` -- the state a controller is in
when it spawns -- and eight of the ten are called `Waiting` (the turret's is `Idle`, `HerdAnimal`'s `grazing`).
From there the decision layer is the class's own *functions*, not its states: **127 `GotoState` calls sit outside
every state body**, in `WhatToDoNext`, `SetEnemy` and the timers, which is why the in-degree table is dominated by
a handful of states that most of the tree can be pushed into.

Cover combat is the largest behaviour in the game's AI: `BotEnemy.ShootFromCover` is 475 lines with 19 function
overrides, `CoveredAttack` 446 with ten latent sleeps, against a median state of 57 lines. `BotEnemy` alone holds
36 states, 211 overrides and 60 labels. `ScriptedController.Scripting` (496 lines) is the biggest state in the
tree and the most-named one -- the mission-scripting state every scripted bot hands control back to.

Two findings change how the rest of this page should be read.

* **The shipped text is a floor on the machine, not its ceiling.** Five classes carry more `UState` exports than
  their text declares (`BotEnemy` 36/41, `BotRep` 2/4, `BotBase` 4/5, `BotLite` 4/5, `ScriptedController` 1/2),
  and four of the ten missing names -- `GetInFormation`, `MoveInFormation`, `TakePoint`, `WaitingForFormation` --
  are declared by no class's text anywhere in the corpus, while `BotEnemy`'s own decision code dispatches to all
  four. A rebuild driven by the text alone would silently drop those states.
* **A subclass that redeclares an inherited state name gets a second state.** `AlkeshBomberBot` declares its own
  `Scripting` and `Waiting`; every `GotoState('Scripting')` in the tree resolves to `ScriptedController`'s, so the
  bomber's two are entered by nothing this read can see. A state name is not a key until it is resolved along the
  `super` chain (and folded for case: `HerdAnimal` declares `grazing`, its own dispatcher says `Grazing`), which
  is what the tool does before it counts an edge.

<!-- generated:ai-states -->
**151 states over 32 classes: 22 declare a literal `GotoState`, 4 a dynamic one, 216 labels, 189 latent sleeps; the classes' own functions add 127 `GotoState` calls outside every state.**
| class | states in text | states in record | labels | `GotoState` |
|---|---:|---:|---:|---:|
| `AirborneTurretController` | 1 | 1 | 0 | 0 |
| `AlkeshBomberBot` | 13 | 13 | 21 | 5 |
| `BotBase` | 4 | 5 | 3 | 3 |
| `BotEnemy` | 36 | 41 | 60 | 17 |
| `BotGoauld` | 1 | 1 | 0 | 0 |
| `BotGuide` | 3 | 3 | 3 | 0 |
| `BotLite` | 4 | 5 | 5 | 5 |
| `BotRep` | 2 | 4 | 3 | 1 |
| `BotSG` | 10 | 10 | 19 | 5 |
| `BotScarab` | 2 | 2 | 5 | 5 |
| `DeathGliderBot` | 5 | 5 | 9 | 1 |
| `ExtreniteAutoTurretController` | 3 | 3 | 0 | 4 |
| `GoaAutoTurretController` | 5 | 5 | 0 | 6 |
| `GoaAutoTurretMK2Controller` | 5 | 5 | 1 | 6 |
| `GoauldDroneBot` | 4 | 4 | 7 | 3 |
| `GoauldKillerDroidBot` | 2 | 2 | 0 | 1 |
| `HaakenBot` | 2 | 2 | 4 | 0 |
| `HaakenLeaderBot` | 1 | 1 | 2 | 0 |
| `HerdAnimal` | 2 | 2 | 7 | 2 |
| `HoverTankPilotBot` | 1 | 1 | 1 | 0 |
| `JaffaBotHeavy` | 4 | 4 | 8 | 6 |
| `JaffaBotLight` | 3 | 3 | 6 | 3 |
| `JaffaBotMedium` | 2 | 2 | 3 | 2 |
| `MindControlledBot` | 1 | 1 | 2 | 0 |
| `PteroBossBot` | 7 | 7 | 10 | 4 |
| `PteroBot` | 3 | 3 | 5 | 0 |
| `ReetouBot` | 9 | 9 | 15 | 0 |
| `ReetouProtectorBot` | 2 | 2 | 1 | 0 |
| `ScriptedController` | 1 | 2 | 1 | 6 |
| `ScriptedTriggerController` | 1 | 1 | 1 | 3 |
| `SuperSoldierBot` | 4 | 4 | 2 | 2 |
| `TeltakBot` | 8 | 8 | 12 | 5 |

States by in-degree — how many states or class dispatchers name each one with a literal `GotoState`, each counted where the state is declared (a subclass naming an inherited state counts on the ancestor):

| state | declared by | in-degree | named from |
|---|---|---:|---|
| `Scripting` | `ScriptedController` | 11 | `BotBase`, `BotEnemy`, `BotLite`, `BotSG` … |
| `Hunt` | `BotEnemy` | 5 | `BotEnemy`, `JaffaBotLight`, `JaffaBotMedium` |
| `Waiting` | `BotEnemy` | 5 | `BotEnemy`, `JaffaBotHeavy`, `SuperSoldierBot` |
| `StaggeredAssist` | `BotEnemy` | 4 | `BotEnemy`, `BotRep`, `BotSG`, `SuperSoldierBot` |
| `Buried` | `BotScarab` | 3 | `BotScarab` |
| `Open` | `GoaAutoTurretController` | 3 | `ExtreniteAutoTurretController`, `GoaAutoTurretController` |
| `ChasePlayer` | `SuperSoldierBot` | 2 | `SuperSoldierBot` |
| `Close` | `GoaAutoTurretController` | 2 | `ExtreniteAutoTurretController`, `GoaAutoTurretController` |
| `CloseIdle` | `GoaAutoTurretController` | 2 | `GoaAutoTurretController` |
| `ConcentrateFire` | `BotSG` | 2 | `BotSG` |
| `CoveredAttack` | `BotEnemy` | 2 | `BotEnemy` |
| `CoveredHide` | `BotEnemy` | 2 | `BotEnemy` |

`GotoState` targets that are no state of the calling class's chain — engine states and the exports whose text is missing (below): `Broken` ×5, `GameEnded` ×2, `GetInFormation` ×1, `Invulnerable` ×2, `MoveInFormation` ×2, `PlayerWalking` ×1, `Surfaced` ×1, `TakePoint` ×2, `WaitingForFormation` ×1.

30 of 151 states neither name another state nor are named by a literal `GotoState` from either side (e.g. `Shoot`, `Chasing`, `GroundAttack`, `Scripting`, `Waiting`, `BotBeingElectrocuted`) — a state a native body or a `GotoState` with a computed argument enters, which is the part of the machine this read cannot see.

5 class(es) carry more `UState` **exports** than their text declares — `BotBase` 4/5, `BotEnemy` 36/41, `BotLite` 4/5, `BotRep` 2/4, `ScriptedController` 1/2 — and every extra name is a state the class only *references* (`GotoState`, `IsInState`, `case`): the shipped text is a floor on the machine, not its ceiling.
<!-- /generated:ai-states -->

Thirty of the 151 states are neither named by a literal `GotoState` nor name one. They are entered by a computed
target or from a native body, and that is the residue this reading leaves -- the same shape as the rest of this
page: a count is measurable, an entry point sometimes is not.

## Not measured

* What a state body *computes*. The graph above is structure -- which state can hand control to which --
  and a label, an override and a sleep are countable; whether a body does what its name says is a per-state read
  of the 151 bodies, and the three combat states worth reading first are `BotEnemy.ShootFromCover`,
  `CoveredAttack` and `ScriptedController.Scripting`.
* `AIScript` and `AIMsgManager` internals beyond their function counts (3 and 39). `AIScript` is a
  `Keypoint` actor, not the controller, so mission AI scripting is level data — a 5.2 question.
* Whether `IntelligentSpawner` configuration is in the map, the checkpoint or both. The counts say the
  instances are in checkpoints; how the spawner was configured before the checkpoint existed is open.
