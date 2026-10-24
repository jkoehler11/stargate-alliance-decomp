# What Perception changed (PLAN 4.8)

Status: **first half measured** — the fork's own deltas, each with the evidence that establishes it. The
second half (a diff against stock UE2.5 source) needs stock sources this project does not hold, so what
is here is what the build itself shows, not a comparison against a reference tree.

Two things are easy to confuse and are kept apart below: what this **build** does differently from the
engine it forked, and what changed **between this build (Dec 2005) and the later studio source (Jun
2006)** — the second is drift, not a fork.

## 1. The script-to-C++ pipeline (`ucpp`)

Perception compiled hot UnrealScript classes to C++ with an in-house compiler, and it runs at build
time as an Xbox executable — the image carries the string `D:\uc.xbe`.

| measurement | value | where |
|---|---|---|
| classes registered through the generated registration macro | **493** call sites of `FUN_0003af60`, one per class | `docs/NATIVE_CLASSES.md` |
| per-class C++ vtables | **492** tables in `.rdata`, `ProcessEvent` at slot 21 of every one | `docs/NATIVE_VM.md` |
| game-module functions that are registration stubs | **39 of 43** in `stargategame`/`stargatevehicles` | `PLAN` 4.6 note |
| classes with script *and* C++ bodies | `Pawn`, `ShadowProjector`, and the class pairs the oracle holds as `.ucpp` | `docs/ORACLE_DIFF.md` |

The consequence for every later phase: a class can look complete in the script tree while its
behaviour lives in the binary. `docs/UNKNOWNS.md` U1 records the signature of that — 523 `native()`
declarations that serialise index 0 and are bound by name, not through the dispatch table.

## 2. Middleware that is **inside the engine**, not beside it

The image's own strings name the middleware support files, which settles where each one is
implemented:

| middleware | evidence in the image | what it means |
|---|---|---|
| **Karma** (ragdoll physics) | `Engine\Src\KarmaSupport.cpp`; `PHYS_Karma`, `PHYS_KarmaRagDoll`; `UKarmaParams::StaticClass()`; `XLevel->Ragdolls.Num()`, `XLevel->KContactGenActors` | physics modes, an actor class and **two new `ULevel` fields** are compiled in — not a plugin bolted onto the game |
| **LIPSinc** (lip-sync) | `Engine\Src\LIPSincSupport.cpp`; the `*LIPSincAnim` natives; `AnimNotify_LIPSinc`; `.lad` controllers | the engine owns the playback path; `.lad` payload layout is still open (U4) |
| **zlib** | `Unknown zlib error`, `zlib memory error`, `zlib version error` | static link, error strings only |
| Kynapse / Havok | **no strings** | the AI that ships is script-side (`AIScript`), and Havok is absent — do not assume either from a later title in the series |

The `XLevel` fields are the interesting part: `Ragdolls` and `KContactGenActors` are new level-side
containers, which is also why the property walk special-cases a property *named* `XLevel`
(`docs/NATIVE_PROPERTIES.md` §4).

## 3. Reflection and VM deltas

These are engine-layer changes that every tool touching packages has to accommodate, all measured in
this repository:

- **Reserved name indices.** The name table pre-registers 325 names at fixed indices; the 16 property
  type names occupy **1..16** in declaration order, which is what allows the tag format to carry a
  type in a nibble (`docs/NATIVE_PROPERTIES.md` §1, §6).
- **Tag record.** One byte packs the type, the size class and the "array index follows" bit; `Size` is
  a measured serialized length; the writer picks the size class at `<0x100`/`<=0x10000` boundaries
  (§6). A stock UE2.5 reader of a later version would not accept this layout.
- **`ProcessEvent` is virtual at a fixed slot** (`+0x54`, slot 21 of 492 tables) rather than reached
  by a call anywhere in the image — a consequence of the C++ class scheme in §1.
- **Class objects are built in C++**, not deserialised: `Core.u` carries 7 class exports against 643
  property exports, so the property classes exist only as the 15 constructors in the binary
  (`docs/NATIVE_PROPERTIES.md` §2).

## 4. The fork's own special cases (named, so nobody re-derives them)

| string | where | reading |
|---|---|---|
| `XLevel` skipped silently | property walk `0x0003bdc0` | a level-side property the engine refuses to serialise |
| `DecoHack: skipping property %s` | `0x0003a220`, binary struct-property path | a Perception-specific tolerance, the only `DecoHack` string in the image |
| `D:\uc.xbe` | script compiler path | the compiler ran as an Xbox executable (§1) |
| `\alienbrainWork\Stargate-sg1\Game\GameXBOX\<Module>\Src\<File>.cpp` | 154 assert paths, 167 strings | the source tree layout, and the module map the native track is built on (PLAN 2.3) |

## 5. Drift against the later studio source (not a fork delta)

`docs/ORACLE_DIFF.md` compares this build's 2,319 classes with the 291 in the Jun-2006 source: 204
match, **63 drifted and 11 divergent** (similarity 0.01–0.74), 13 exist only in the leak, 1,362 only
in the build. The drift is concentrated in engine-side gameplay plumbing rather than in rendering:

| group | classes |
|---|---|
| animation notifies | 7 (`AnimNotify`, `AnimNotify_LIPSinc`, `AnimNotify_DestroyEffect`, …) |
| camera/editor actions | 2 (`ActionMoveCamera`, `ActionPause`) |
| music | 2 (`IMusicActor`, `IMusicController`) |
| AI markers | 1 (`AIMarker`, plus `AvoidanceWaypoint`/`FlyingPathNode` in the list) |
| core plumbing | the rest — `Actor`, `LevelInfo`, `GameInfo`, `MissionObjective`, `SavedMove`, `Triggers`, `CheatManager`, `SurfaceInfo`, `MapList`, … |

Reading: between Dec 2005 and Jun 2006 Perception was still rewriting the actor/level/objective layer,
so those classes are the ones where our decompilation and the leak *should* disagree. It is **not**
evidence about stock UE2.5, and the 1,362 build-only classes prove nothing about cut content because
the leak is the engine fork, not the game (the rule in `docs/CUT_CONTENT.md`).

## 6. What is still open

- **A diff against stock UE2.5.** The deltas above are described from this build alone; saying "this
  differs from stock" needs stock source, which the project does not carry. Until then each item is
  phrased as "this build does X", not "Perception changed X from Y".
- **Renderer-side fork work.** The `D3D`/`D3DX`/`XGRPH` sections hold 1,323 functions, 7 of them named
  (`docs/IMPORTS.md`); the device caps are a runtime probe (U11). Nothing about the renderer's deltas
  is claimed here.
- **Memory/cache and the script-compiler hooks.** Not measured. The compiler's own existence is (the
  `D:\uc.xbe` string and the registration scheme), its behaviour is not.
