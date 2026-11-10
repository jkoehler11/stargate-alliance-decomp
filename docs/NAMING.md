# Naming a body from the image alone (PLAN 4.2 / 4.8)

`docs/NATIVE.md` counts the population: 14,957 functions, and the great majority still `FUN_*`. PLAN 4.2
would name most of those from MSVC 7.1 / Xbox-SDK signature databases, which `docs/UNKNOWNS.md` U9 says this
repository may not carry — so the question that decides how much of 4.2 is *ours to do* is: how many
bodies carry evidence of their own name?

Two kinds of evidence are readable without a database or a decompiler, and this page measures both
(`make body-name`, artifact `inventory/body_names.csv`, `--check` in `make ci`):

- **a method literal.** UE2's log and assert text often names the method that emitted it — MSVC keeps the
  literal in the body, so a body carrying exactly one distinct `Class::Method` token names itself. Two or
  more distinct tokens is a **refusal**: a body that logs about another object is common in this code, and
  choosing between them would be a guess.
- **a byte-identical twin.** A body whose bytes match a *named* body's is the same function folded by the
  linker (COMDAT); a group with exactly one named member names its unnamed members, and groups under 32
  bytes are refused because two different getters compile to the same six instructions.

<!-- generated:body-name -->
| module | bodies | unnamed | named by a literal | ambiguous | source file only | no evidence |
|---|---:|---:|---:|---:|---:|---:|
| `Core` | 3238 | 2593 | 7 | 0 | 17 | 2569 |
| `Engine` | 2094 | 1493 | 7 | 0 | 0 | 1486 |
| `IpDrv` | 157 | 135 | 2 | 0 | 0 | 133 |
| `Gui` | 145 | 86 | 4 | 0 | 1 | 81 |
| `D3DDrv` | 129 | 107 | 0 | 0 | 0 | 107 |
| `stargategame` | 88 | 56 | 0 | 0 | 0 | 56 |
| `stargatevehicles` | 52 | 40 | 0 | 0 | 0 | 40 |
| `XboxAudio` | 42 | 33 | 0 | 0 | 0 | 33 |
| `Fire` | 41 | 32 | 0 | 0 | 0 | 32 |
| `XboxLive` | 31 | 18 | 7 | 0 | 4 | 7 |
| `Gameplay` | 24 | 15 | 0 | 0 | 1 | 14 |
| `XboxDrv` | 22 | 17 | 0 | 0 | 0 | 17 |
| **all modules** | **6063** | **4625** | **27** | **0** | **23** | **4575** |

**27 of 4625** unnamed bodies are named by their own text, **0** name more than one method and are refused, **23** name a source file but no method, and **4575** carry no naming evidence at all.

| address | module | was | named | evidence | class/method |
|---|---|---|---|---|---|
| `0x000341a0` | `Core` | `FUN_000341a0` | `FFileManagerError_CreateFileReader` | own method literal | `FFileManagerError` |
| `0x000341c0` | `Core` | `FUN_000341c0` | `FFileManagerError_CreateFileWriter` | own method literal | `FFileManagerError` |
| `0x00063410` | `Core` | `FUN_00063410` | `UObject_StaticShutdownAfterError` | own method literal | `UObject` |
| `0x000e5a70` | `Engine` | `FUN_000e5a70` | `USoundGroup_RefreshGroup` | own method literal | `USoundGroup` |
| `0x0012ed30` | `Engine` | `FUN_0012ed30` | `FPoly_Fix` | own method literal | `FPoly` |
| `0x001c4bc0` | `Engine` | `FUN_001c4bc0` | `UPlayerStats_Initialize` | own method literal | `UPlayerStats` |
| `0x00261310` | `Engine` | `FUN_00261310` | `ASVehicle_physKarma` | own method literal | `ASVehicle` |
| `0x00290ef0` | `Engine` | `FUN_00290ef0` | `USpline_execInitSplinePath` | own method literal | `USpline` |
| `0x00291280` | `Engine` | `FUN_00291280` | `USpline_execInitSplineRot` | own method literal | `USpline` |
| `0x00291c50` | `Engine` | `FUN_00291c50` | `USpline_execNextSplineRot` | own method literal | `USpline` |
| `0x002c4f10` | `IpDrv` | `FUN_002c4f10` | `FUdpLink_BindPort` | own method literal | `FUdpLink` |
| `0x002c4f1c` | `IpDrv` | `FUN_002c4f1c` | `FUdpLink_BindPort` | own method literal | `FUdpLink` |
| `0x00307150` | `Gui` | `FUN_00307150` | `UUIXInteraction_InitUIXVoiceSupport` | own method literal | `UUIXInteraction` |
| `0x003072e0` | `Gui` | `FUN_003072e0` | `UUIXInteraction_VoiceCommunicatorUpdate` | own method literal | `UUIXInteraction` |
| `0x00307cd0` | `Gui` | `FUN_00307cd0` | `UUIXInteraction_InitUIX` | own method literal | `UUIXInteraction` |
| `0x00308a60` | `Core` | `FUN_00308a60` | `UIXInteraction_StartSignIn` | own method literal | `UIXInteraction` |
| `0x003090a0` | `Gui` | `FUN_003090a0` | `UUIXInteraction_RegisterPlayer` | own method literal | `UUIXInteraction` |
| `0x00325da0` | `XboxLive` | `FUN_00325da0` | `StatsReadTask_Finished` | own method literal | `StatsReadTask` |
| `0x00327600` | `XboxLive` | `FUN_00327600` | `XboxLiveManager_StartStatsWrite` | own method literal | `XboxLiveManager` |
| `0x003276e0` | `XboxLive` | `FUN_003276e0` | `XboxLiveManager_StartStatsRead` | own method literal | `XboxLiveManager` |
| `0x003277c0` | `XboxLive` | `FUN_003277c0` | `XboxLiveManager_StartStatsEnumerate` | own method literal | `XboxLiveManager` |
| `0x003278b0` | `XboxLive` | `FUN_003278b0` | `XboxLiveManager_StartStatsReset` | own method literal | `XboxLiveManager` |
| `0x003279a0` | `XboxLive` | `FUN_003279a0` | `XboxLiveManager_StartStatsLadderWrite` | own method literal | `XboxLiveManager` |
| `0x00328af0` | `XboxLive` | `FUN_00328af0` | `StatsEnumerateTask_Finished` | own method literal | `StatsEnumerateTask` |
| `0x00329520` | `Core` | `FUN_00329520` | `SessionQueryTask_Finshed` | own method literal | `SessionQueryTask` |
| `0x00329960` | `Core` | `FUN_00329960` | `XboxLiveManager_StartStatsLadderRead` | own method literal | `XboxLiveManager` |
| `0x0032aa20` | `Core` | `FUN_0032aa20` | `XboxLiveManager_UpdateTask` | own method literal | `XboxLiveManager` |
<!-- /generated:body-name -->

## What the number means

The method route is real but tiny: it names **27** bodies out of the corpus's unnamed ones, and those 27
are worth having because they are the ones the strings make unambiguous — the script-visible `exec`
natives of `USpline` (`execInitSplinePath`, `execInitSplineRot`, `execNextSplineRot`), `ASVehicle`'s
`physKarma`, the Xbox Live task methods (`XboxLiveManager_StartStats*`, `StatsReadTask::Finished`), and
Core's file-manager and shutdown paths. One name is misspelled in the game's own string
(`SessionQueryTask::Finshed`) and is kept as written, because the literal is the evidence; `FUdpLink::BindPort`
names two bodies, which is consistent with an overload pair and is reported rather than deduplicated away.

The twin route contributes **nothing today** and that is the more useful result: the linker did fold
code — the groups are real — but no folded group contains a named member, because what the linker folds
here is the 33-byte stub shape, not a function anyone has named. The instrument stays in the target, so
the moment the map gains a named member of a group the unnamed ones follow automatically.

So the corpus's unnamed bodies are **evidence-free by their own text**: `source_file` in the artifact is
the only trace 23 of them leave, and the rest leave nothing. That is the same conclusion the library
sections reached from the other end (`docs/LIBRARIES.md`: 0 of 3,109 SDK bodies name a source file), and
together they bound 4.2 honestly — **the image can name 27 of 4,625 attributed bodies (0.6 %)**; the rest
is signature data (U9) or a runtime pairing, not another naming pass. The next session that wants to move
this number should supply a database, not re-read the strings.

## The names are applied, not just proposed

`make body-name` appends the candidates to `symbols/xbe_overrides.csv`, the override channel
`native-summary.py`, `module-split.py`, `game-modules.py` and `module-export-census.py` already read. The
merge is append-only and idempotent — a second run adds no row — because the file is append-ordered and the
diff has to stay names-only: no row's size, module or evidence text moves.

The map is one step further out. `symbols/xbox_functions.csv` is the Ghidra product (its post-script
applies this same override file on a full import), so this pass made the equivalent edit directly and
proved it was names-only — **27 renamed, 0 added, 0 removed, 0 size or module changes** — then regenerated
`symbols/xbox.map`, `docs/NATIVE.md`, `inventory/xbe_functions_by_module.csv` and
`inventory/function_modules.csv` from it. A future `--overrides` row reaches the map the same way: either
through the next `make ghidra` import, or through the same names-only edit and `make symbols`.

## Reproducing

```
python3 tools/body-name.py --xbe DUMP/default.xbe -o inventory/body_names.csv \
    --write-doc docs/NAMING.md --write-overrides
python3 tools/native-summary.py --symbols symbols/xbox_functions.csv \
    --overrides symbols/xbe_overrides.csv -o inventory
```

The artifact has one row per exported body — the census as well as the candidates: module, address, the
`FUN_*` name it had, its size, which evidence class it fell in, the new name (blank unless something named
it), the class and method the literal carried, the body's own source file if it had one, and its twin's
address when a twin named it.
