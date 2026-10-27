# The network surface

Every `UFunction` carries its `FunctionFlags` in its export payload, so "what does this build replicate" is a census
over the packages rather than a reading of source. That census is `inventory/function_flags.csv`, and this page is
what it says.

    python3 tools/function-flags.py --packages "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
    python3 tools/function-flags.py --check          # dump-free, in `make ci`

The flags are the last field of a `Function` payload (`docs/BYTECODE.md`): `u32` at `payload_end-4`, or at
`payload_end-6` when the `Net` bit means a `RepOffset` follows. 795 of the 797 functions in `inventory/natives.csv`
agree with the flags read this way; the two that do not are both in `UPreview` (`Created`, `SetRenderInfo`), the
class `docs/UNKNOWNS.md` U3 was already about — a reader disagreement, not a payload one.

## Scope

**12,472 functions over 1,568 classes in 16 packages.** `Fire.u` and `UnrealEd.u` declare no `Function` export at
all. 756 payloads carry no `04 0b 47` epilogue — 751 of them have no script at all, so no `'G'` marker either — and
**all 756 carry `Native`**, which is why they went missing from the first version of this census.

| flag | functions | share |
|---|---|---|
| `Defined` | 10,581 | 84.8% |
| `Public` | 12,414 | 99.5% |
| `Simulated` | 2,016 | 16.2% |
| `Final` | 1,256 | 10.1% |
| `Event` | 1,097 | 8.8% |
| `Native` | 795 | 6.4% |
| `Exec` | 433 | 3.5% |
| **`Net`** | **342** | **2.7%** |
| `NetReliable` | 279 | 2.2% |

## The RPCs are the engine's, the prediction is the fork's

| package | functions | `Net` | `Simulated` | `Exec` | `Native` |
|---|---|---|---|---|---|
| Engine | 3,842 | 206 | 668 | 242 | 444 |
| StargateGame | 3,273 | 75 | 501 | 177 | 17 |
| StargateContent | 1,834 | 54 | 643 | 0 | 0 |
| StargateVehicles | 549 | 6 | 140 | 0 | 4 |
| GamePlay | 382 | 1 | 9 | 2 | 0 |
| StargateMenus | 334 | 0 | 22 | 0 | 0 |
| GUI | 800 | 0 | 4 | 12 | 81 |
| UWindow | 731 | 0 | 2 | 0 | 0 |
| Core, IpDrv, Editor, UPreview, UDebugMenu, Sharpreffects, Stargateeffects, AmbientCreatures | 687 | 0 | 26 | 0 | 249 |

Two different shapes, and the difference is the finding:

* **`Net` is concentrated in the engine.** `Engine.PlayerController` alone holds **125 of the 342** — 36.5% of every
  RPC in the build — then `StargateGame.StargatePlayer` 17, `Engine.Controller` 12, `Engine.Weapon` 10,
  `StargateContent.Charging` 10, `StargateGame.UnrealPlayer` 9, `StargateGame.StargateWeapon` 8, `Engine.Vehicle` 6.
  The fork's contribution is 136 functions over `StargateGame` + `StargateContent` + `StargateVehicles`, and the
  fork's *player* classes are where they sit: `StargatePlayer` is `UnrealPlayer`'s subclass and both appear, so the
  fork kept the stock RPC surface and added a layer rather than replacing it.
* **`Simulated` is concentrated in content.** `StargateContent` alone has 643 simulated functions out of 1,834
  (35%), against 668 of 3,842 in Engine (17%). Simulated functions are the ones a client may run locally, so the
  content packages — objectives, mission script, interactive props — carry the client-side prediction, while the RPC
  surface stays in the engine. 2,016 simulated functions against 342 RPCs is the ratio this build is built around:
  **behaviour is predicted locally and corrected rarely**, which matches a co-op game with a small player count
  rather than a deathmatch.
* **279 of 342 RPCs are reliable (81.6%)**, and `PlayerController` holds 88 of those. Unreliable RPCs (63) are the
  movement/aim updates; the reliable ones are state changes. `NetServer` and `NetClient` bits are **never** set —
  this build predates (or does not use) UE2's client/server-only RPC restriction, so direction is not in the flags.
* **`Exec` 433** is the console-command surface, and it splits cleanly: `Engine` 242 + `GUI` 12 + `GamePlay` 2 are
  stock debug commands, `StargateGame` 177 is the fork's own, which is where `Tab`-console gameplay probes
  (`PLAN.md` 7.3) would have to hook.

## Replicated properties

`inventory/class_properties.csv` carries the same vocabulary for properties: **318 of the 11,496 declared properties
carry `Net`**, led by `Engine.Actor` 56, `Engine.Pawn` 35, `Engine.GameReplicationInfo` 34,
`Engine.PlayerReplicationInfo` 31, `StargateGame.StargatePawn` 14, `Engine.MissionObjective` 9,
`Engine.PlayerController` 9, `Engine.Mover` 8.

So the replicated *state* is the engine's actor/pawn/player bookkeeping, and the fork's one addition is
`StargatePawn` — 14 properties, i.e. the Jaffa/player pawn's replicated state is a small delta on the stock `Pawn`.
The replication vocabulary in this build has **no `RepNotify` and no `RepRetry`** flag at all, so replication here is
poll-based: nothing fires a client-side callback on a property change, which is the format-level reason the content
layer leans on `Simulated` functions and RPCs instead.

## Not measured

* **Which of the 342 RPCs are actually called, and when.** The flags say a function *may* replicate; nothing here
  says the shipped content uses it. What would settle it: locate the scripts (`docs/UNKNOWNS.md` U13) and count the
  call sites, or watch a session on a debug kit.
* **RPC parameter layouts and the `RepOffset` values.** The tail is read, the `RepOffset` is in the payload, but what
  each offset means needs the replication layout the loader builds — a `LinkerLoad` job, not a flag read.
* **The 5 functions whose script ends `0b 47` rather than `04 0b 47`** (`Engine.Actor.DemoPlaySound` and four
  `PlayerController` client/server pairs): they are the only payloads where the epilogue test fails *and* the flags
  resolve, so their scripts are 2 bytes shorter than the pattern predicts. Unexplained, and named here rather than
  averaged away.
* **Bandwidth, tick rate, relevancy radius.** Nothing in the packages states them; `Engine.GameInfo`'s defaults carry
  a `NetUpdateFrequency`-shaped property set but reading it needs the defaults tree, and the Xbox Live layer
  (`docs/IMPORTS.md`) is outside this census.
