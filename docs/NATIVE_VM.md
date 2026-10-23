# The UnrealScript VM in `default.xbe`

Status: `GNatives` located (4.3) and its targets named (4.4). `UObject` layout, `ProcessEvent` and
the property tables are not yet documented — this file grows as they are confirmed.

## The native dispatch table

| Field | Value |
|---|---|
| Address | `0x00598e00` (start of `.data`, which is file offset `0x0058a000`) |
| Entries | 994 (indices 0–993) |
| Non-null entries | 991 |
| Entry 0 | null — index 0 is unused, as UE expects |
| 4-byte alignment | 985 of 991 (99.4%) |
| Entries pointing into `.text` | 991 of 991 |
| Code references to the table | exactly one, at `0x00343763` — the interpreter's dispatch site |

### How it is identified (`tools/gnatives.py`)

A dispatch table is a run of 32-bit values that are either zero or the entry address of a function,
and that shape is *not* unique: this image has 23 runs of at least 300 such words. Ranking them by
size is how the first version of the script picked the wrong one — the committed map used to be a
6,385-entry run in `.rdata`, while this document described the 994-entry table. The script now scores
every run against four tests that only a dispatch table satisfies, and writes the evidence for all
of them (`inventory/gnatives_candidates.csv`):

1. entry 0 is null (UE leaves index 0 unused);
2. the run begins at a section boundary — the array is its own object, not a field inside one;
3. every non-null entry points into `.text`, not merely into a section flagged executable (the XBE
   marks `.data` and `.rdata` executable too, so the flag alone proves nothing);
4. exactly one code reference to the run's first word. A vtable or callback array is reached from
   whatever installs it, so it has several, and the address is usually not referenced from code at
   all.

| candidate | section | entries | non-null | known function entries | tests | code xrefs |
|---|---|---|---|---|---|---|
| `0x00598e00` | `.data` | 994 | 991 | 0 | **4/4** | 1 |
| `0x004c9e54` | `.rdata` | 5,966 | 5,936 | 2,925 | 2/4 | 0 |
| `0x004ee908` | `.rdata` | 6,417 | 6,385 | 3,208 | 2/4 | 0 |

The real table is also the only candidate whose entries the analyser never turned into functions:
nothing calls a native directly, so all 991 slots were `FUN_`-less addresses until this pass.

### Independent cross-checks

- **It matches the script side.** Across the 18 packages, 274 `native()` declarations carry an
  explicit index; 270 of them land on a distinct, non-null slot (98.5%), and no index has two
  different names.
- **It contains more than the packages declare.** 721 entries have no declarer in any shipped
  package: engine-internal exec functions (renderer, Xbox services, Karma), reachable from C++ but
  never from script.
- **It explains the earlier 3:1 puzzle.** 797 native declarations collapse onto 275 indices because
  packages re-declare natives they inherit; the table has exactly one slot per index, so the index
  space is global after all.
- **It leaves four declared indices unaccounted for** — see `docs/UNKNOWNS.md`.

## What it buys: readable script

UELib resolves native calls through `UnrealPackage.NTLPackage`, which this dump does not ship, so
every engine call decompiled to `__NFUN_<n>__` — including the operator natives
(`NotEqual_ObjectObject` is index 119, `AndAnd_BoolBool` 130, `Add_IntInt` 146). `tools/Sg1Tool`
builds the table from Core (operators) + Engine (actor, sound and iterator natives) + the package
being decompiled, in that override order.

| Run | Unresolved sites | Distinct tokens |
|---|---|---|
| no table | 51,289 | 213 |
| Core only | 4,230 | 93 |
| Core + Engine | 1,470 | 61 |
| Core + Engine + own package | **0** | **0** |

Zero sites across 2,319 classes: `Spawn(Class'StargateGame.StargateVehicleSound', self)` and
`super(Vehicle).PostBeginPlay()` read as source, and `Engine.Pawn.IsInPain` matches the 2006 oracle
expression for expression. `docs/DECOMPILER_GAPS.md` is a regression check that should stay empty.

## Naming the dispatch targets (4.4)

`tools/native-names.py` turns the table into a naming decision and keeps the evidence apart:

| evidence | slots | name | what it means |
|---|---|---|---|
| declared | 270 | `exec<Function>` (`execTrace`) | slot N is the function the script calls as `Class.Function` at index N |
| slot-only | 721 | none in the CSV; `Native_<index>` in Ghidra | the address is dispatch slot N and nothing more |
| ambiguous | 0 | — | a conflict would be recorded, not guessed |

Naming follows `docs/CONVENTIONS.md`: `exec<Function>` is the engine's own spelling for a native
implementation, and a name two slots would share takes the class prefix instead — one pair collides
here (`Actor.Sleep` at 256 and `GUIPage.Sleep` at 332 become `execActor_Sleep` and
`execGUIPage_Sleep`). `Editor` and `UPreview` are excluded from naming: they are editor-side
packages that number their natives locally, and `UPreviewMapListClient.Created` claims index 2, which
is a null slot in the runtime table.

`XbePost.java` applies the map during the same headless run as the assert naming, and the result is
a clean addition rather than a rename:

| measurement | before | after |
|---|---|---|
| functions | 14,027 | 15,018 |
| named from the dispatch table | 0 | 991 (270 `exec*`, 721 `Native_*`) |
| assert-attributed | 1,245 | 1,245 |
| unnamed (`FUN_*`) | 12,497 | 12,497 |
| name clashes / renamed over an existing name | — | 0 / 0 |

The 991 added functions were created at the table's entries because no xref reaches them; each
slot-only one also carries a plate comment `GNatives[<index>] — dispatch slot with no declarer in
the shipped packages`, so the slot number survives even when the name is only `Native_<index>`.
`build/ghidra/gnatives_applied.csv` records index, address and final name for review.

## Next

1. Chase the four unaccounted indices and the 523 index-less declarations (`docs/UNKNOWNS.md`).
2. Identify the 721 slot-only execs from behaviour and string references; the table gives an index,
   not a function name.
3. Locate `UObject` layout, `ProcessEvent`/`ProcessInternal` and the property pointer tables; the
   dispatch xref at `0x00343763` is the entry point to that neighbourhood.
4. Signature libraries for the CRT/XAPI/D3D8 majority (4.2).
