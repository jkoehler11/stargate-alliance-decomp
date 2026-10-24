# Native notes: `stargategame` and `stargatevehicles`

The two game-specific modules are 10 source files, 43 functions — the smallest of the twelve modules
and the only ones that are the game's own code rather than engine code. This note records what they
contain, which is not what the plan expected.

## Method

```
tools/ghidra/export-c.sh DUMP GHIDRA_HOME stargategame_,stargatevehicles_
  # imports default.xbe, carves sections, applies symbols/xbox_functions.csv (13,737 names),
  # decompiles the selected functions to $(WORK)/native/*.c and writes an index CSV
tools/ucpp-registrations.py $(WORK)/native -o symbols/xbe_overrides.csv
make ghidra        # applies symbols/xbe_overrides.csv after the assert pass
```

The C is game-derived: it stays under the git-ignored `work/` tree. What is committed is the name map
and the measurements below.

## Finding 1: most of these functions are generated class registration

39 of the 43 functions have the same shape:

```c
iVar2 = FUN_0003af60(0, 0xecc, 4, 0, 0, 0, 0, L"AlkeshBomber", ..., L"System", 0x4084004, ...);
if (iVar2 == 0)
  FUN_00058a30("ReturnClass", "...\\stargatevehicles\\src\\AlkeshBomber.cpp", 3);
```

They are the per-class registration stubs of Perception's `ucpp` script-to-C++ compiler: the class
name, the outer package (`System`), the object size and the class flags are the build's own literals,
and `ReturnClass` is the generated assert. That is evidence, not inference, so each one is named
`ucpp_Register_<Class>` in `symbols/xbe_overrides.csv` (39 rows, with object size, assert line and
source file).

The object sizes are the class's serialised layout size in bytes — 3,788 for `AlkeshBomber`, 4,344 for
`StargatePlayer`, 3,344 for `StargateVehicle`, 72 for `SGScreenBlur`. That number is a free cross-check
for the `UObject` layout work (PLAN 4.3) and for the property counts in `inventory/classes.csv`.

| class | object size | source file | address |
|---|---|---|---|
| `SGScreenBlur` | 72 | `SGCameraEffects.cpp` | `0x00317380` |
| `SGScreenMotionBlur` | 72 | `SGCameraEffects.cpp` | `0x00317430` |
| `SGScreenEmissiveWithBloom` | 68 | `SGCameraEffects.cpp` | `0x003174e0` |
| `SGScreenMenuPause` | 84 | `SGCameraEffects.cpp` | `0x00317ba0` |
| `SGScreenSniperOverlay` | 84 | `SGCameraEffects.cpp` | `0x00317d70` |
| `SGScreenDOF` | 112 | `SGCameraEffects.cpp` | `0x00319620` |
| `SGScreenRadialBlur` | 80 | `SGCameraEffects.cpp` | `0x003196d0` |
| `SGScreenEmissive` | 104 | `SGCameraEffects.cpp` | `0x003197f0` |
| `SGScreenBloom` | 144 | `SGCameraEffects.cpp` | `0x0031abe0` |
| `SGFontInfo` | 1,064 | `StargateGame.cpp` | `0x0031e740` |
| `StargateHUD` | 2,252 | `StargateGame.cpp` | `0x0031e800` |
| `BotEnemy` | 2,252 | `StargateGame.cpp` | `0x0031e8c0` |
| `SquadAIBasic` | 1,440 | `StargateGame.cpp` | `0x0031e980` |
| `UnrealScriptedSequence` | 984 | `StargateGame.cpp` | `0x0031ea40` |
| `SGOrder` | 148 | `StargateGame.cpp` | `0x0031eb00` |
| `SGGenericOrderActor` | 908 | `StargateGame.cpp` | `0x0031ebc0` |
| `SGLevel` | 920 | `StargateGame.cpp` | `0x0031ec80` |
| `SGTriggeredCondition` | 920 | `StargateGame.cpp` | `0x0031ed40` |
| `Electrocutor` | 1,096 | `StargateGame.cpp` | `0x0031ee00` |
| `BeamArc` | 1,096 | `StargateGame.cpp` | `0x0031eec0` |
| `BotBase` | 1,772 | `StargateGame.cpp` | `0x0031ef80` |
| `FarClipChange` | 932 | `StargateGame.cpp` | `0x0031f040` |
| `IntelligentSpawner` | 996 | `StargateGame.cpp` | `0x0031f100` |
| `StargateVehicle` | 3,344 | `StargateGame.cpp` | `0x0031f1c0` |
| `CoverInfo` | 1,092 | `StargateGame.cpp` | `0x0031f280` |
| `ShadowManager` | 952 | `StargateGame.cpp` | `0x0031f340` |
| `UnrealPlayer` | 4,012 | `StargateGame.cpp` | `0x00322cd0` |
| `StargatePlayer` | 4,344 | `StargateGame.cpp` | `0x00322d90` |
| `SubActionDepthOfField` | 132 | `USubActionDepthOfField.cpp` | `0x003233b0` |
| `AlkeshBomber` | 3,788 | `AlkeshBomber.cpp` | `0x0030ca70` |
| `AlkeshBomberBot` | 1,868 | `AlkeshBomber.cpp` | `0x0030cb30` |
| `AlkeshLandingZone` | 924 | `AlkeshBomber.cpp` | `0x0030cbf0` |
| `DeathGlider` | 3,660 | `DeathGlider.cpp` | `0x0030e190` |
| `DeathGliderBot` | 1,792 | `DeathGlider.cpp` | `0x0030e250` |
| `StargateCar` | 3,812 | `StargateCar.cpp` | `0x00312910` |
| `StargateCopter` | 3,600 | `StargateCopter.cpp` | `0x00313d60` |
| `StargateHover` | 3,660 | `StargateHover.cpp` | `0x00314eb0` |
| `Teltak` | 3,664 | `Teltak.cpp` | `0x00315ec0` |
| `TeltakBot` | 1,812 | `Teltak.cpp` | `0x00315f80` |

## Finding 2: the module's own logic is small

The other four functions are not registration stubs, and they are the only game code in the module:

| address | name | what the decompiled body does |
|---|---|---|
| `0x00311520` | `stargatevehicles_StargateCar` | 541 bytes: projects a velocity onto an axis, clamps it, derives a speed, then asserts `Mesh` and `smesh` before reading mesh state. The vehicle-physics body. |
| `0x00316b10` | `stargategame_SGCameraEffects` | 88 bytes: depth-of-field resource reference counting, asserts `DOFResourceRefCount > 0`. |
| `0x00318060` | `stargategame_SGCameraEffects` | 217 bytes: camera effect setup; references the `Engine` package by name. |
| `0x003107b0` | `stargatevehicles_GoauldDrone` | 180 bytes: same constructor call as the stubs but with no class-name literal in the decompiled text; left unnamed (U6). |

So the game modules are generated glue plus a handful of bodies. The gameplay itself is in script
(Phase 3) and in engine modules — consistent with the earlier finding that Perception moved hot script
classes into C++ and left the rest as bytecode.

## Finding 3: three helpers carry the readability

Every one of the 43 functions calls `FUN_00058a30`, and 40 call `FUN_000340c0`/`FUN_0003af60`:

| helper | calls | evidence |
|---|---|---|
| `appAssertFailed` (`0x00058a30`) | 43 | called as `(expression, file, line)` from every function. Named from the wide literal it logs, `"Assertion failed: %s [File:%s] [Line: %i]"`, and measured to log through the engine's output device and return — no break, no abort (`docs/UNKNOWNS.md` U6). |
| `FUN_0003af60` | 40 | the class constructor used by the registration stubs: it installs the `Class` class table (`0x004d4870`, object size 1312), sets flags `\|0x12` and stores 13 arguments. Still `FUN_*`: naming it needs the caller-side evidence of PLAN 4.7. |
| `appMallocBeforeInit` (`0x000340c0`) | 40 | logs `"Called %s before memory init"` with `"appMalloc"` and returns 0; reached through a pointer from 961 sites, i.e. the allocation entry point before `GMalloc` exists. |

## Finding 4: the two packages' natives have no dispatch index

`inventory/natives.csv` lists 21 `native()` declarations across `StargateGame` and `StargateVehicles`
(`BotEnemy`'s cover search, `Electrocutor` arcing, `SGOrder` drawing, HUD order drawing,
`DeathGlider`/`StargateCopter` Karma updates). All 21 serialise `native_token` 0 — the same signature as
the 523 index-less declarations in U1 — so none of them is bound through a dispatch index. Their bodies are in
these modules or the engine, bound outside the dispatch table, which is the thread PLAN 4.7 picks up.

## What this means for Phase 4

- The game modules are now 39/43 named from evidence; the rest are four functions with documented
  bodies. "Zero unnamed functions in the game modules" is close, and honest about the four.
- Two of the three shared helpers are named from their own strings (`appAssertFailed`,
  `appMallocBeforeInit`); `FUN_0003af60` stays `FUN_*` until its callers are named (PLAN 4.7). The
  remaining engine-side naming work is the `ProcessEvent`/property machinery (PLAN 4.3).
- The `ucpp` registration table doubles as a class inventory with layout sizes, which is what PLAN
  3.9/4.7 need to match script classes to their native copies.
