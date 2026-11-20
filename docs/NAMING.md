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

## Names from call sites (2026-11-19)

The masked-body pass leaves targets it cannot match: import thunks, bodies whose Ghidra boundary
differs, functions inlined at some call sites and not others. But a matched function's `REL32`
relocations to *function* symbols are resolved calls, and the library's own symbol table names the
target. `tools/xdk-call-names.py` (`make xdk-call-names`) reads exactly that from
`inventory/xdk_lib_names.csv`: **482 addresses** are named this way, 256 of them by more than one
caller — the XAPI stubs run to 124 callers from one target. The seven library sections go from
2,532 to **2,838 of 3,109** named, and the map from 44.5% to 47.65%.

Two rules, both visible in the artifact (`inventory/xdk_call_names.csv`): a name is taken only on a
**strict majority** of the callers that reach the address -- a tie between the top two names is
refused and the address stays `FUN_*` -- and the vote count travels with the row, so a
single-caller name is distinguishable from one 124 callers confirm. Dissent is recorded rather than
dropped: `other_names` lists every losing name with its votes, and it is non-empty on **14 of the
482 rows**, all of them majority wins that a minority reading disagrees with. Two names land on two
addresses each -- COMDAT copies of `operator new` and D3DX's `process_restart` -- which is evidence
rather than ambiguity, since each copy has its own callers, so the duplicate is reported instead of
refused (the body matcher refuses duplicates for the opposite reason: there a shared body is a
shared guess).

## What the libraries cannot reach (2026-11-19)

After both naming passes, 250 library-section bodies remain `FUN_*` (plus the 21 `thunk_` stubs the
symbol map already names). `tools/xdk-lib-residue.py` (`make xdk-lib-residue`,
`inventory/xdk_lib_residue.csv`) asks each one the only question that matters: is it in the supplied
libraries at all?

* **17 are, and were named.** A prefix of the image body equals a library function byte for byte
  with the operands masked, and the leftover is 4 bytes past the `ret` — Ghidra's body runs long, not
  the library's. `?IpTerm@CXnIp` is the clearest case: 189 bytes identical over 22 masked operands.
* **233 are not.** No library body matches at any start offset 0–24 under the same quality rules,
  so these bodies did not come from the 60 supplied archives. That is not a contradiction: XBE section
  names follow the linker's *contribution*, and a section named `XNET` can hold code no `xnet.lib`
  member produced.
* **0 are boundary-shifted in the other direction.** A suffix match (Ghidra starting the function
  late) held for one body before the quality rules, and for none after; the rule is what makes the
  count trustworthy.

The residue is therefore an answer, not a gap: the libraries are exhausted, and what remains in these
sections is code the libraries never contained.

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

## The library half: a masked body is a name (2026-11-19)

The library sections this page called unreachable become readable when the libraries are supplied, because a
linked copy differs from its archive member only where the linker patched an operand. `tools/xdk-lib-match.py`
(`make xdk-lib-names`) reads each COFF member's symbols and relocations, zeroes the operand bytes at every
relocation offset, and compares the whole body against `symbols/xbox_functions.csv`; a match is the member's
name at the image address. With a locally supplied XDK 5849.17 set it names **3,700** functions — 2,531 of the
3,109 library-section bodies and 1,169 in `.text` — taking the map from 19.7% named to 44.4%.

Three rules keep it honest, and each came from a wrong answer:

- **A body under 12 bytes names nothing.** The image holds several `mov eax,1; ret N` stubs, and an 8-byte
  match named four separate addresses `_Direct3DCreate8@4`. A body is trusted at 12 bytes when it carries at
  least one relocation, or 24 when it carries none; 287 candidate matches failed that rule.
- **A signature two members share is not a name.** COMDAT duplicates and linker-folded twins give one masked
  body several names; 338 candidates were rejected on this rule, and a name claimed twice is dropped.
- **Verify against the libraries' own call graph, not the count.** For every match, each `REL32` relocation
  whose target is a function is chased to its image address: **8,461 of 8,476** land on the function the member
  names (99.82%), and the 15 that do not are `__alloca_probe` == `__chkstk`. The other control is the 207
  names Ghidra's FID already carried: 173 of the 176 the matcher could resolve reproduce (98.3%), and all 3
  misses are bodies under 12 bytes.

The matcher is published separately as [`xbox-ue2-re-tools`](https://github.com/jkoehler11/xbox-ue2-re-tools)
(MIT, `lib-match`), which is the same code with this project's paths and CSV defaults taken out; the copy in
`tools/` is the one wired to this repo's artifacts and gates.

`--apply` writes the names into `symbols/xbe_overrides.csv` (append-only, evidence = the member) so a later
Ghidra import reproduces them, and applies them to `symbols/xbox_functions.csv` names-only — 3,700 renamed, 0
addresses, sizes or modules moved. The libraries are never committed: `--libs` is a path argument, and the
set's identity is the `xdk-libs` line in `tools/versions.lock`.
