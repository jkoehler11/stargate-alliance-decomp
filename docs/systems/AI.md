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
answerable by encounter script today, and by bot class once class defaults parse.

They
appear in shipped checkpoints instead — `BotSG` 890 instances, `JaffaBotLight` 324, `ScriptedController` 70,
`AlkeshBomberBot` 18, `AttachedAlkeshTurretController` 18 — and the agent that puts them there is
`IntelligentSpawner`, which the checkpoint schema shows at 1,860 instances
(`docs/systems/CHECKPOINTS.md`).

That has a practical consequence for 5.2 and the rebuild: a mission's map file does not tell you which
enemies it has. The population lives in spawner configuration plus checkpoint state, so "which bots does
mission 7 use" is not answerable from the `.unr` alone.

## Not measured

* What any state does. 165 states is a size, not a behaviour; the state bodies are the script
  decompilation under `$WORK`, which this repository does not carry.
* `AIScript` and `AIMsgManager` internals beyond their function counts (3 and 39). `AIScript` is a
  `Keypoint` actor, not the controller, so mission AI scripting is level data — a 5.2 question.
* Whether `IntelligentSpawner` configuration is in the map, the checkpoint or both. The counts say the
  instances are in checkpoints; how the spawner was configured before the checkpoint existed is open.
