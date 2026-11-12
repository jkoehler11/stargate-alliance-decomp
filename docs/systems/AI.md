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
  four. A rebuild driven by the text alone would silently drop those states -- but they are not lost: they are
  export-side objects with compiled bodies (*The states the text never declares are compiled*, below).
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

### The states the text never declares are compiled

The floor above is a floor on the *source*, not on the machine. A `UState` is an export like any other, and its
body is not in the class's text export at all -- it is the `Function` exports whose `Outer` is the state -- so
what the text does not declare can still be read out of the package, with no boot and no decompiler.

    python3 tools/state-exports.py --system DUMP/System -o inventory/state_exports.csv

Output: `inventory/state_exports.csv`, one row per state export **and** one row for every state a text declares
that no export carries, so neither side can hide a disagreement by being the only one counted. `bytes` sums the
function exports' serial sizes (a payload, not a script: what a body does still needs `tools/uscript-dis.py`).

<!-- generated:state-exports -->
**159 state exports over 36 classes; 14 are declared by no class text, and 6 are declared by a text and not exported.**

| class | state | functions | other children | bytes | callees the text cannot show |
|---|---|---:|---:|---:|---|
| `Birds` | `Wandering` | 3 | 0 | 454 | `BeginState`, `PickDestination`, `PickSlaveDestination` |
| `BotBase` | `Scripting` | 6 | 0 | 544 | `AbortScript`, `CompleteAction`, `EndState`, `LeaveScripting`, `Restart`, `Timer` |
| `BotEnemy` | `GetInFormation` | 6 | 0 | 467 | `BeginState`, `DoAlertSquad`, `EndState`, `FaceSquadRotation`, `Timer`, `WaitForManInFront` |
| `BotEnemy` | `MoveInFormation` | 8 | 0 | 1076 | `BeginState`, `DetermineFollowLocation`, `DoAlertSquad`, `EndState`, `Tick`, `Timer` |
| `BotEnemy` | `Scripting` | 4 | 0 | 165 | `DisplayDebug`, `DoAlertSquad`, `PawnIsInPain`, `Timer` |
| `BotEnemy` | `TakePoint` | 8 | 0 | 566 | `BeginState`, `DoAlertSquad`, `EndState`, `IsRunningScript`, `LeaderNotifySuspicious`, `MemberSuspicious` |
| `BotEnemy` | `WaitingForFormation` | 3 | 0 | 128 | `BeginState`, `DoAlertSquad`, `Timer` |
| `BotLite` | `Scripting` | 5 | 0 | 414 | `EndState`, `LeaveScripting`, `Restart`, `SetMoveTarget`, `Tick` |
| `BotRep` | `MoveInFormation` | 8 | 0 | 1389 | `BeginState`, `DetermineFollowLocation`, `EndState`, `FaceSquadRotation`, `Tick`, `Timer` |
| `BotRep` | `TakePoint` | 3 | 0 | 205 | `BeginState`, `EndState`, `Timer` |
| `CirclingBirds` | `Circling` | 4 | 0 | 630 | `BeginState`, `MaxHiddenTime`, `PickDestination`, `PickSlaveDestination` |
| `MigratingBirds` | `Migrating` | 5 | 0 | 1081 | `BeginState`, `InitialLocation`, `MaxHiddenTime`, `PickDestination`, `PickSlaveDestination` |
| `ScriptedController` | `Broken` | 1 | 0 | 65 | `BeginState` |
| `TransientAmbientCreature` | `Wandering` | 0 | 0 | 0 | — |

The 9 class(es) whose export table outran their text: `Birds`, `BotBase`, `BotEnemy`, `BotLite`, `BotRep`, `CirclingBirds`, `MigratingBirds`, `ScriptedController`, `TransientAmbientCreature`.

Declared but not exported: `ReetouBot.Ambush`, `ReetouBot.Berserk`, `ReetouBot.Ranged`, `BotSG.CoveredAttack`, `BotSG.Waiting`, `HoverTankPilotBot.TroopDrop`.
<!-- /generated:state-exports -->

**14 state exports are declared by no class text, and 13 of them carry 64 function children and 7,184 bytes of
payload** -- the states a text-driven rebuild would drop are not lost, they are export-side objects with bodies.
`BotEnemy`'s five are the four names its own decision code dispatches to plus `Scripting` (29 children, 2,402
bytes); `BotBase`, `BotLite` and `BotRep` each add a `Scripting` or a formation state; `ScriptedController`'s
second export is `Broken`, a state five other classes dispatch to by name.

Four whole classes appear **only** on the export side -- `Birds` (`Wandering`), `CirclingBirds` (`Circling`),
`MigratingBirds` (`Migrating`) and `TransientAmbientCreature` (`Wandering`) -- so the 32-class text census never
saw them at all. `TransientAmbientCreature.Wandering` carries no function: its behaviour is native, and it is the
one export in the set with nothing to read.

The residue is symmetric, which is why the two counts never quite meet: **6 names a text declares and no export
in the class's package carries** -- `ReetouBot`'s `Ambush`, `Berserk` and `Ranged`, `HoverTankPilotBot`'s
`TroopDrop`, and `BotSG`'s `CoveredAttack`/`Waiting`, which repeat states `BotEnemy` already exports. So the
class record's `states` column is a *declared* count, not an exported one: it sums to 164 over these classes
where the packages export 159, and this page quotes the export count whenever it means objects.

## What the bodies do

`tools/ai-states.py` counts a state's labels, overrides, `GotoState` targets and sleeps. The bodies are readable
too: every class ships its own source, and `tools/ai-state-body.py` walks one body's statement tree -- blocks,
labels, simple statements -- and keeps only the *kind* of each node and the identifiers it calls. A 475-line body
becomes a one-page outline, and only counts and callee names are written, so the code never enters the repo.

    python3 tools/ai-state-body.py --system DUMP/System -o inventory/ai_state_bodies.csv
    python3 tools/ai-state-body.py --system DUMP/System --outline BotEnemy.ShootFromCover

Output: `inventory/ai_state_bodies.csv`, one row per state. Two columns carry the finding: `stmts` counts the
state's **own** statements and `override_stmts` the ones inside a `function`/`event` it overrides -- an override
is a handler the engine calls, not a step the state runs, and folding the two together would make a state that
only declares `SetEnemy` read as busy. `sleep_seconds` sums literal `Sleep` arguments only: 90 of the tree's 189
sleeps take a computed one and add nothing to it.

<!-- generated:ai-state-bodies -->
**151 bodies hold 1426 simple statements (2350 more inside overrides), 189 sleeps (62.2 s of literal wait) and 268 dispatch calls; 3 loop, and 55 run to completion in one tick with no `Sleep` and no `Finish`/`Wait` call.**

| class | state | stmts | overrides | if | loop | sleeps | sleep s | calls | `GotoState` | top callees |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `BotEnemy` | `CoveredAttack` | 67 | 7 | 47 | 0 | 10 | 0 | 131 | 1 | `Sleep:10`, `CoverInfo:8`, `WhatToDoNext:8`, `FRand:7` |
| `BotSG` | `CoveredAttack` | 58 | 0 | 16 | 0 | 7 | 0 | 70 | 0 | `Sleep:7`, `WhatToDoNext:7`, `Goto:6`, `log:5` |
| `BotSG` | `RestFormation` | 51 | 10 | 46 | 0 | 6 | 0.35 | 121 | 2 | `log:8`, `Pawn:7`, `ShouldCrouch:7`, `Goto:6` |
| `ScriptedController` | `Scripting` | 39 | 19 | 42 | 0 | 1 | 2 | 127 | 6 | `CompleteAction:8`, `Log:7`, `DrawText:6`, `GotoState:6` |
| `BotEnemy` | `Suspicious` | 32 | 6 | 27 | 0 | 6 | 4.2 | 56 | 0 | `Goto:6`, `Sleep:6`, `ShouldCrouch:5`, `StargatePawn:4` |
| `JaffaBotHeavy` | `MelleeCharge` | 28 | 8 | 8 | 0 | 3 | 5.1 | 49 | 4 | `Goto:4`, `GotoState:4`, `JaffaBase:4`, `FinishMelleeChargeAttack:3` |
| `BotEnemy` | `CoveredMove` | 26 | 6 | 10 | 0 | 4 | 0.6 | 43 | 0 | `Sleep:4`, `StopFastRun:4`, `DoCoveredFire:3`, `FireWeaponAt:3` |
| `BotSG` | `ConcentrateFire` | 26 | 4 | 17 | 0 | 4 | 0.1 | 57 | 1 | `Sleep:4`, `SquadAISG:4`, `FRand:3`, `log:3` |
| `BotSG` | `TacticalMove` | 26 | 8 | 19 | 0 | 3 | 0.22 | 79 | 1 | `FRand:10`, `vect:6`, `FMax:4`, `StopFiring:4` |
| `JaffaBotLight` | `Sniper` | 26 | 7 | 14 | 0 | 3 | 1.5 | 58 | 1 | `Enable:6`, `Disable:4`, `FireWeaponAt:4`, `LineOfSightTo:4` |
| `BotEnemy` | `ActivateAlarm` | 23 | 3 | 10 | 0 | 3 | 1.5 | 35 | 0 | `GetUserPosition:5`, `WhatToDoNext:5`, `Goto:3`, `Sleep:3` |
| `BotEnemy` | `InteractUseObject` | 23 | 1 | 7 | 0 | 2 | 1.5 | 25 | 0 | `Goto:4`, `WhatToDoNext:4`, `Log:3`, `sleep:3` |
| `BotEnemy` | `CoveredHide` | 22 | 4 | 15 | 0 | 3 | 0.1 | 59 | 1 | `Enable:6`, `MustHoldFire:4`, `FireWeaponAt:3`, `Goto:3` |
| `BotGuide` | `ReturnToPlayer` | 22 | 1 | 12 | 0 | 4 | 2 | 46 | 0 | `ACTION_SGWaitForPlayer:9`, `GetMyPlayer:6`, `Sleep:4`, `WhatToDoNext:4` |
| `GoauldDroneBot` | `Pursuit` | 22 | 8 | 9 | 1 | 2 | 0 | 45 | 2 | `PointReachable:6`, `FRand:4`, `MoveTo:4`, `WhatToDoNext:3` |
<!-- /generated:ai-state-bodies -->

### The code is in the overrides, not in the state body

The 151 bodies hold **1,426 own statements against 2,350 inside 570 overrides** -- 1.6x as much handler code as
state code. 29 states have no own statements at all (a set of overrides plus a label), and 38 neither dispatch
(`GotoState`) nor jump to a label, so their successor is decided entirely outside them.

The idiom is a **short body that sets fields, takes one action and hands off**, not a script with a loop: the
whole tree's state bodies contain **three `while`/`for` statements and zero `switch`**, against 1,419
assignments and 641 conditional branches, and a state repeats by `Goto('Begin')` (169 label jumps) or by being
re-entered, not by iterating. 55 of the 151 run to completion in one tick -- no `Sleep` and no `Finish`/`Wait`
call -- which is what makes the dispatch layer, rather than the bodies, the thing a rebuild has to get right.

### `BotEnemy.ShootFromCover` -- 475 lines, 16 own statements, 19 overrides

Sixteen statements, three of them inside one `if`: `Target = Enemy`, `CheckBurning()`, `ShouldStartHacking()`;
if the state is meant to hold position and `ShouldAdvance()` agrees, fire, wait and ask `WhatToDoNext()`; then
`Sleep`, `UpdateCoverStatus()`, `SelectNextCoverState()`, `HideAction = COVEREDFIRE_NoValidCover`,
`UpdateCrouchToShoot()`, `FireWeaponAt()`, `MoveTo(...)`, `Sleep`, `WhatToDoNext()`, `Goto('Begin')`.

So one pass is: choose a cover action in a helper, fire at the current target, walk to the destination the helper
chose, wait briefly, hand control to the class-level dispatcher -- which is free to leave the state -- and
restart. There is **not one `GotoState` in the body or in any of its 19 overrides**: the state never chooses its
own successor. The behaviour is in the handlers: `Timer` (choose an attack mode or fire, then
`SelectNextCoverState` and re-arm), the four squad-cover entry points (`SquadCoverRequest`, `SquadCoverFinish`,
`AssignToCovering`, `ReleaseFromCovering`), `NotifyTakeHit`/`ReceiveWarning` accumulating
`EnemyDamageAccumulator`, `NotifyHitWall` re-aiming 96 units off the normal it hit, and `SelectNextCoverState`'s
fallback / hold-fire / `IsHealthLow` branches over `PickCoverDestination` and `PickForwardDestination`. Three
`Sleep`s totalling 0.6 s: the state waits on its timer and on its moves, not on fixed delays.

### `BotEnemy.CoveredAttack` -- 446 lines, 67 own statements, 7 overrides

`CoveredAttack` spends its code in the body instead of the handlers, and carries **four labels of its own** --
`Begin`, `Move`, `FinishedAttack`, `NoCover` -- so it is the clearest case of a state that is a small machine:
it opens with a `DoCoveredFire()`, then `Begin` aims and starts the move, `Move` decides whether the pawn has
arrived (and whether it can camp), strafes around the cover with `FindBestPathToward`, or bails to `NoCover`;
`FinishedAttack` fires, waits a random moment and hands back; `NoCover` fires, marks the hide action invalid and
asks `WhatToDoNext()`.

All ten of its sleeps take **computed** arguments -- no literal wait at all -- and `PickAttackDestination` is
where the body's 38 conditionals concentrate: it traces the enemy's perpendicular left and right at
`TACTICALMOVEDIST` (or an advanced-move distance), inverts the side when the first trace is blocked, and falls
back to standing still when both are. The seven handlers are the event surface, and one of them is the state's
only `GotoState`: `SeePlayer` re-targets through `SetEnemy`, and if it can attack it fires from where it stands
and leaves the state.

### `ScriptedController.Scripting` -- 496 lines, 39 own statements, 19 overrides

Biggest body in the tree and the one every scripted bot returns to, and it is not a behaviour loop but an
**action interpreter**. `InitForNextAction()` takes the sequence script and calls `SetActions()`, resetting
`bEventReceived`; the body then handles the fire/alt-fire flags, a `PerformSpecialMove` branch, and a three-way
goal: `MoveToGoal` (set movement physics, `WaitForLanding`, then label `KeepMoving`: `SetMoveTarget`,
`MayShootTarget`, `MoveToward`, and on a failed path find `FailedScriptPathFindCount++`, a `Sleep` and a
`GotoState`), `TurnToGoal` (`FinishRotation`, then `CompleteAction()`), or a face-and-shoot branch.

**`CompleteAction()` is the exit vocabulary**: eight calls, from `Trigger`, `Timer`, `AnimEnd`,
`LIPSincAnimEnd`, a reached destination and a finished rotation. The action therefore advances on whichever event
its `CompleteOn*` flag names, and the body's six `GotoState` calls sit in `CompleteAction`, `LeaveScripting`,
`SetMoveTarget` and the bad-path branch. Its `DisplayDebug` override draws the script's own state as text on the
HUD (`DrawText`) -- the one place in the AI tree where a state body is also a debug instrument.

### What this changes for the rebuild

The three bodies say the same thing at three scales: the state is a *frame*, and the behaviour is in the
overrides and in the class-level dispatcher that pushes the controller from outside. A port that reproduces the
151 bodies and stops would produce controllers that mostly park. That also re-orders the missing-state negative:
the five classes whose `UState` exports outnumber their text lose behaviour when their handlers are dropped,
not when a state body is, which is why `WhatToDoNext` and `SetEnemy` are worth reading before the states.

## Not measured

* What a body's expressions *resolve to*. The outline above names the callees and counts the branches, but not
  what `CanAttack()`, `PointReachable()` or a `CoverInfo` accessor answer at runtime, and 90 of the 189 sleeps
  wait on a computed duration. The first needs a boot with the decision logged per tick; the second only a named
  constant per wait.
* `AIScript` and `AIMsgManager` internals beyond their function counts (3 and 39). `AIScript` is a
  `Keypoint` actor, not the controller, so mission AI scripting is level data — a 5.2 question.
* Whether the five names a text declares and no chain exports exist in the image. `BotSG`'s `CoveredAttack` and
  `Waiting` are inherited states its text repeats, but `ReetouBot`'s three and `HoverTankPilotBot`'s `TroopDrop`
  are exported nowhere on their chain; settling it needs a name-index xref per package, since a `GotoState`
  literal naming a state does not prove the object exists.

* Whether `IntelligentSpawner` configuration is in the map, the checkpoint or both. The counts say the
  instances are in checkpoints; how the spawner was configured before the checkpoint existed is open.
