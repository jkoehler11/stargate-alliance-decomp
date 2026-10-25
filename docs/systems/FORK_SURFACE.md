# The fork's script surface

PLAN 4.8 asks what Perception changed against stock Unreal Engine 2.5. On the script side that question
has an exact answer: a class whose `super` lives in a stock package (`Engine`, `Core`, `GamePlay`, `GUI`,
`UWindow`, …) is a place where the fork attaches to the engine. There are **503** such classes, against
**83** distinct stock base classes.

    python3 tools/fork-surface.py            # tables, from the committed class table
    python3 tools/fork-surface.py -o inventory   # + inventory/fork_surface.csv

Source: `inventory/classes.csv` (2,319 classes with `super`, package of super, function and property
counts). This is a class-graph measurement — it says where the fork hooks in, not what the code does.
Behaviour needs the script bodies, which stay outside this repository (AGENTS.md).

## Where the fork attaches

| fork package | classes | functions | properties |
|---|---:|---:|---:|
| `StargateGame` | 165 | 1,243 | 1,779 |
| `Sharpeffects` | 130 | 25 | 74 |
| `StargateContent` | 83 | 348 | 455 |
| `Stargateeffects` | 60 | 34 | 27 |
| `UDebugMenu` | 38 | 155 | 474 |
| `StargateMenus` | 24 | 124 | 109 |
| `StargateVehicles` | 3 | 12 | 26 |
| **total** | **503** | **1,941** | **2,944** |

`StargateGame` carries the gameplay logic: 165 classes holding 1,243 of the 1,941 functions. The two
effects packages together hold 190 classes but only 59 functions — they are class labels for emitters,
not code.

## Which engine classes were touched

| stock base | package | fork subclasses |
|---|---|---:|
| `Emitter` | Engine | 200 |
| `Actor` | Engine | 58 |
| `UWindowPageWindow` | UWindow | 23 |
| `Surface` | Engine | 21 |
| `Pawn` | Engine | 12 |
| `direction` | Engine | 10 |
| `Info` | Engine | 10 |
| `Object` | Core | 10 |
| `Triggers` | Engine | 9 |
| `GUIPage` | GUI | 9 |
| `CameraEffect` | Engine | 8 |
| `SGListBox` | GUI | 7 |
| `StaticMeshActor` | Engine | 6 |
| `Trigger` | Engine | 5 |
| `Interaction` | Engine | 5 |

The shape is what a licensed fork looks like: the engine's extension points (`Emitter`, `Actor`,
`Pawn`, `Info`, `Trigger`, `Surface`) are subclassed, and 200 of the 503 classes are effect emitters.

## Most of the surface is data, not code

| what an insertion class does | classes |
|---|---:|
| adds properties | 237 |
| adds nothing — no functions, no properties | 230 |
| carries native (ucpp) functions | 6 |

Nearly half the insertion classes are **renames that only carry defaults**: `CarpetSurface`,
`ConcreteSurface`, `DirtSurface`, `FabricSurface` and 17 other `Surface` subclasses each declare zero
functions and zero properties — they exist to swap materials. The same is true of much of
`Sharpeffects`.

That matters for effort estimates: the part of the fork that needs *script* reproduction is the 237
classes with added state, and the part that needs *native* reproduction is only these six:

| class | native functions | total functions |
|---|---:|---:|
| `SGOrder` | 4 | 10 |
| `Electrocutor` | 2 | 3 |
| `FarClipChange` | 2 | 3 |
| `SGFontInfo` | 1 | 11 |
| `ShadowManager` | 1 | 3 |
| `StargateHUD` | 1 | 57 |

## Cross-references

* The checkpoint system's writer is `AutosaveCheckpoint`, a `Trigger` subclass in `StargateGame` — one of
  the 5 `Trigger` insertions, and the class behind `docs/systems/CHECKPOINTS.md`.
* Weapons attach through a single layer: `StargateWeapon`, `StargateProjectile`, `StargateWeaponPickup`,
  `StargateWeaponAttachment`, `StargateStationaryWeapon` (all in `StargateGame`), with the concrete guns
  (`P90Prototype`, `PistolPrototype`, …) subclassing those in `StargateContent`.
* `SGOrder` is the one native-heaviest insertion class (4 of 10 functions native) — the natural first
  target for the 4.9 native reconstruction, since its script half cannot execute without the C++ half.

## Not measured

* What any of these classes do. The bodies are the script decompilation (`$WORK/UnrealScript`), which by
  policy is not committed.
* The C++ side of the fork beyond ucpp natives. Only native declarations show up in the class table;
  changes inside stock engine functions are invisible to this measurement and belong to 4.8's native half.
* A stock UE2.5 class table to diff against. Without it, "changed" means "subclassed", not "modified" —
  a class could be identical to a stock subclass and still appear here.
