# The UnrealScript VM in `default.xbe`

Status: `GNatives` located and validated (PLAN 4.3, partial). `UObject` layout, `ProcessEvent` and
the property tables are not yet documented — this file grows as they are confirmed.

## The native dispatch table

| Field | Value |
|---|---|
| Address | `0x00598e00` (start of `.data`, which is file offset `0x0058a000`) |
| Entries | 994 (indices 0–993) |
| Non-null entries | 991 |
| Entry 0 | null — index 0 is unused, as UE expects |
| 4-byte alignment | 985 of 991 (99.4%) |
| Code references | exactly one, at `0x00343763` — the interpreter's dispatch site |

How it was found (`tools/gnatives.py`): a dispatch table is a run of 32-bit values that are either
zero or a virtual address inside `.text`. Scanning the XBE's data sections for runs of at least 300
such values produced 23 candidates, nearly all of them vtables (their entries point into function
*bodies*, or into `.rdata`). The table at `0x00598e00` stands out because every entry is a **function
entry point** and the array starts at a section boundary with a null slot.

Independent cross-checks:

- **It matches the script side.** Across the 18 packages, `native()` declarations name 275 distinct
  indices; 270 of them (98.2%) are present and non-null in this table, and each maps to a distinct
  address. The five misses are index 0 (unused) and 3969–3971, which are declarations from packages
  whose native indices run past this table's length — a discrepancy worth chasing in 4.4, not a
  reason to doubt the table.
- **It contains more than the packages declare.** 721 entries have no declarer in any shipped
  package: these are engine-internal exec functions (renderer, Xbox services, Karma), reachable from
  C++ but never from script.
- **It explains the earlier 3:1 puzzle.** 797 declarations collapsed onto 275 indices because
  packages re-declare natives they inherit; the table has exactly one slot per index, so the index
  space is global after all and the per-package name conflicts come from re-declaration, not from
  per-package numbering.

## What it buys: 100% readable script

UELib resolves native calls through `UnrealPackage.NTLPackage`, which this dump does not ship, so
every engine call decompiled to `__NFUN_<n>__` — including the operator natives (`NotEqual_ObjectObject`
is index 119, `AndAnd_BoolBool` 130, `Add_IntInt` 146). `tools/Sg1Tool` now builds the table from
Core (operators) + Engine (actor, sound and iterator natives) + the package being decompiled, in that
override order.

| Run | Unresolved sites | Distinct tokens |
|---|---|---|
| no table | 51,289 | 213 |
| Core only | 4,230 | 93 |
| Core + Engine | 1,470 | 61 |
| Core + Engine + own package | **0** | **0** |

Zero sites across 2,319 classes means the reconstructed script no longer has any unreadable call:
`Spawn(Class'StargateGame.StargateVehicleSound', self)` and
`super(Vehicle).PostBeginPlay()` read as source, and `Engine.Pawn.IsInPain` matches the 2006 oracle
expression for expression. `docs/DECOMPILER_GAPS.md` is now a regression check that should stay empty.

## Next

1. Rename the 991 dispatch targets in Ghidra from `inventory/natives.csv` + this table, so the
   decompiler shows `exec<Name>` instead of `FUN_00xxxxxx` (4.4 — the rename script is not written yet).
2. Chase indices 3969–3971 (`inventory/natives.csv`), which do not fit the 994-entry table.
3. Locate `UObject` layout, `ProcessEvent`/`ProcessInternal` and the property pointer tables, then
   append them here; the dispatch xref at `0x00343763` is the entry point to that neighbourhood.
