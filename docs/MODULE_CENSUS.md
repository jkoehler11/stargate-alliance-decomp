# What the 6,150 exported bodies are (PLAN 4.8's native half)

Status: **measured** — the export's population is classified four ways, and the module attribution is
independently checked against each body's own `__FILE__` path: **848 of 858 bodies that name a `.cpp`
name the module they were attributed to** (2026-11-20).
Artifact: `inventory/module_export_census.csv` (6,150 rows, one per exported body; **every attributed
body is now in the set** — the three the analysis used to hold no function for are exported, below).
Tools: `tools/module-export-census.py` (`make module-census`; `--check` in `make ci`).
Input: the 12 exporter rounds from `tools/ghidra/export-module.sh` (`docs/MODULE_SPLIT.md`), which
leave 18.8 MB of decompiled C under `$WORK/module-export`.

## The question, and why the answer is a classifier

4.5 paid for the export and 4.8 is left with its output: 6,150 decompiled bodies. The plan line asks
what Perception changed against stock UE2.5 — renderer, memory, compiler behaviour — and the note
before this one put the open half plainly: *whether `Core`/`Engine` are mostly library code is settled
by a classifier over the exported bodies (section, call fan-in, string refs), not by another pass*.

So this is four independent reads of the same 6,150 files, none of which trusts the decompiler's prose:

* **section** — the XBE section the address falls in. All **6,150** are in `.text`; not one is an
  attributed address that actually points into data.
* **callers** — direct `E8 rel32` call sites across the whole image, counted in one pass and tallied
  per target. A dword scan cannot see a direct call (it stores a relative offset, not the address),
  so this signal is independent of both the export and the attribution.
* **data_refs** — dword occurrences of the address in `.rdata`/`.data`/`.data1`: the vtable slot
  that makes a virtual reachable through `call [reg+slot*4]`.
* **literals** — the string constants the body carries, sorted by what they say: a build-tree source
  path (`__FILE__` in an assert), a game asset path, an ini/config string, a printf format, or prose.

The label a row gets is the *first* literal class it has, in the order `failed` → `ret-stub` → `src`
→ `asset` → `config` → `format` → `text` → `silent`. That is a statement about evidence, not about
identity: `src` means "this body asserts with a build-tree path", not "this is engine code".

<!-- generated:module-census -->
| module | bodies | failed | ret-stub | src | asset | config | format | text | silent |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `Core` | 3246 | 2 | 102 | 562 | 3 | 9 | 117 | 324 | 2127 |
| `Engine` | 2178 | 0 | 25 | 562 | 5 | 13 | 101 | 372 | 1100 |
| `IpDrv` | 159 | 0 | 4 | 22 | 0 | 0 | 23 | 15 | 95 |
| `Gui` | 155 | 0 | 1 | 54 | 1 | 0 | 9 | 27 | 63 |
| `D3DDrv` | 129 | 0 | 0 | 22 | 0 | 2 | 4 | 53 | 48 |
| `XboxAudio` | 115 | 0 | 0 | 9 | 0 | 1 | 7 | 12 | 86 |
| `stargategame` | 88 | 0 | 0 | 31 | 0 | 0 | 1 | 26 | 30 |
| `stargatevehicles` | 52 | 0 | 1 | 12 | 0 | 0 | 0 | 14 | 25 |
| `Fire` | 41 | 0 | 0 | 9 | 0 | 0 | 1 | 5 | 26 |
| `XboxLive` | 32 | 0 | 0 | 11 | 0 | 0 | 11 | 4 | 6 |
| `Gameplay` | 24 | 0 | 0 | 9 | 0 | 0 | 0 | 10 | 5 |
| `XboxDrv` | 22 | 0 | 0 | 5 | 0 | 0 | 1 | 4 | 12 |
| **total** | **6241** | **2** | **133** | **1308** | **9** | **25** | **275** | **866** | **3623** |

| measurement | bodies | share |
|---|---:|---:|
| direct call sites into the set | 40947 sites | |
| at least one caller | 4290 | 68.7% |
| in a vtable or data table | 1542 | 24.7% |
| no caller and no data reference | 528 | 8.5% |
| carry a source path (TU-attributed) | 1308 | 21.0% |
| carry no literal at all | 3623 | 58.1% |
| decompile failed | 2 | 0.0% |

| attributed module | bodies | a `.cpp` path names it | a `.cpp` names another | header path only | none |
|---|---:|---:|---:|---:|---:|
| `Core` | 3246 | 114 | 10 | 438 | 2684 |
| `Engine` | 2178 | 551 | 0 | 11 | 1616 |
| `IpDrv` | 159 | 22 | 0 | 0 | 137 |
| `Gui` | 155 | 54 | 0 | 0 | 101 |
| `D3DDrv` | 129 | 21 | 0 | 1 | 107 |
| `XboxAudio` | 115 | 9 | 0 | 0 | 106 |
| `stargategame` | 88 | 31 | 0 | 0 | 57 |
| `stargatevehicles` | 52 | 12 | 0 | 0 | 40 |
| `Fire` | 41 | 9 | 0 | 0 | 32 |
| `XboxLive` | 32 | 11 | 0 | 0 | 21 |
| `Gameplay` | 24 | 9 | 0 | 0 | 15 |
| `XboxDrv` | 22 | 5 | 0 | 0 | 17 |
| **total** | **6241** | **848** | **10** | **450** | **4933** |

Disagreements (`attributed` -> `its own .cpp`): Core->Engine 8, Core->IpDrv 2

| address | name | attributed | its own `.cpp` | size |
|---|---|---|---|---:|
| `0x000ac170` | `Core_UnClass` | `Core` | `Engine` | 4607 |
| `0x000f1ec0` | `Core_UnClass` | `Core` | `Engine` | 208 |
| `0x000f25b0` | `Core_UnClass` | `Core` | `Engine` | 1484 |
| `0x000f39b0` | `Core_UnClass` | `Core` | `Engine` | 271 |
| `0x0015e040` | `Core_UnClass` | `Core` | `Engine` | 384 |
| `0x00166aa0` | `Core_UnClass` | `Core` | `Engine` | 620 |
| `0x001d0de0` | `Core_UnClass` | `Core` | `Engine` | 4207 |
| `0x001f6313` | `Core_UnTemplate` | `Core` | `Engine` | 766 |
| `0x002c1bc0` | `Core_UnTemplate` | `Core` | `IpDrv` | 2674 |
| `0x002d4950` | `Core_UnClass` | `Core` | `IpDrv` | 300 |

Names: `FUN_*` **4111**, module-prefixed **1323**, `ucpp_*` **40**, `Native_*` **26**, `thunk_*` **17**, other **724** of 6241.
<!-- /generated:module-census -->

## `Core` is not mostly library code by the evidence the bodies carry

The one question 4.8 asked of this pass. `Core`'s 3,220 bodies split as **563 that declare their own
source path**, **102 `ret`-only slots**, **2 failed decompiles**, **3 asset references**, and **2,103
that carry no literal at all**. `Engine` reads better: 562 of 2,111 carry a source path and 1,042 carry
none. Across all twelve modules 3,541 of 6,150 (57.6%) carry no literal whatsoever.

That is the shape of a static link — the libraries assert nothing — but it is not a label, and calling
those 3,541 "library" would be a guess: a UE2 engine function without an assert has exactly the same
signature. What the census *can* say is where the library population starts. The 100 most-called
bodies in the set are 82 `Core` and 44 of them carry no literal, with `FUN_00055ed0` — 37 bytes, 3,279
call sites — and `appAssertFailed` (2,448) at the top, which is what a `memset` and an assert handler
look like from the outside. Naming them is 4.2's signature work (U9), not a fifth census.

## What it settles about the population

**6,150 is the right set and the wrong work list.** Every attributed address is a function entry in
`.text` (0 inside another function — the exporter's own result, re-read here), and the
attribution survives an independent check: of the 858 bodies whose literals name a `.cpp`, 848 name
the module they were attributed to. The ten that do not are in the generated block above, and they
point one way: every one is a body attributed `Core` whose own `.cpp` is `Engine` (8) or `IpDrv` (2) —
`UnClass.cpp` and `UnTemplate.cpp` compile under `Engine` in this build's tree, so the `Core_` prefix
on those bodies is the weaker of the two labels.

Ten of 6,150 is 0.16%, and the check is worth having anyway: it is the first time the locality rule
has been scored against evidence inside the bodies rather than against held-out asserts (89.9%,
`docs/MODULE_ATTRIBUTION.md`). Reconciling the ten is an address-level correction to
`function_modules.csv`/`function_module_attribution.csv` (4.5's artifacts, `--check`-gated against
`xbe_functions_by_module.csv`), not a census edit — they are named here as that work list rather than
changed here.

**What `--check` holds the attribution to, and what it only reports.** The reconciliation against
`function_module_attribution.csv` is exact for the 1,411 rows whose label is an **assert** -- the module the
build's own `__FILE__` literal wrote, or an evidence override -- and advisory for the 4,736 whose label came from
address locality, because
this artifact is a snapshot of that heuristic as it ran when the export round was paid for. A later name can move a
locality label without anything about the body changing: the 15 class-table store seeds (2026-11-10) added 13
names and moved six -- `0x0012ea70`, `0x0012eb30`, `0x001c4e60`, `0x001c4f30` `Core` -> `Engine`,
`0x002c50b0`, `0x002c5180` `Core` -> `IpDrv`. `--check` prints that count and names the addresses; it fails only
when an asserted module and the round disagree, or when the generated block, `module_export_rounds.csv` or
`function_modules.csv` drift.

**147 of the 6,150 are not analysis targets.** 133 are `ret`-only slots (median 5 bytes — a native
registration whose C++ side does nothing) and 14 are jump thunks (`thunk_FUN_*`). So the native half's
work list is **6,000 bodies**, of which **1,309** can be tied to a translation unit today and
**4,838** cannot. 535 of the 6,150 are reached by neither a direct call nor a data reference.

**The population moved because the lists were stale, and the second re-export finished the job.**
`make module-lists` used to return before writing its lists, so every round from 2026-11-10 onwards read
the 6,063 addresses of one early write while the attribution kept growing (6,092 on `main`, 6,150 after
the boundary repairs). The re-export then ran the lists the plan writes, and three attributed bodies were
still left out: the analysis held **no function** for `0x0003bd30` (133 bytes), `0x0024af30` (809) and
`0x0002fa30` (87, the slot-33 audio body U36 added) — no row in `symbols/xbe_overrides.csv` or
`xbe_dispatch.csv`, which is what the importer builds a function from, and nothing xrefs them.

The symbols bundle (2026-11-20) gave each of them a creation row, and this pass proves it: the rounds
run again with those rows imported, and the census reads **6,150 of 6,150 exported, 0 inside another
function, 0 attributed bodies the analysis holds no function for**. The three are ordinary rows now —
`0x0002fa30` is a `ret`-stub with 0 callers, `0x0024af30` is a 809-byte `silent` body, `0x0003bd30` a
133-byte `silent` one — and the rounds' `missing` column is 0 in all twelve modules.

**The name column no longer disagrees with the map.** Two things used to make the export header and
`function_modules.csv` spell a row differently: a thunk named after a target the map spelled by address,
and a rename that lives in `symbols/xbe_boundaries.csv` rather than `xbe_overrides.csv`. Both are now the
census's input — it reads the override file *and* the boundary renames — so the 47 thunks the map names
and the rename rows land in the census under the map's spelling, and `--check` reports **0 `rethunked`**
where it used to report 3.

## The renderer half, sized

4.8's renderer question is `D3DDrv`, and it is small enough to read: **129 bodies**, of which 21 name
a `.cpp` and one more a header, 107 declare nothing, 48 carry no literal, 53 carry prose and 4 a
format string. The fork's own asset vocabulary is tiny in code — **9** bodies in the entire export
reference an asset path and **25** a config string, so the Xbox renderer's behaviour lives in the
bodies, not in the strings they carry.

## Two things the pass measured about the image

* **`Section.executable` cannot separate code from data on this build.** `.rdata`, `.data` and
  `.data1` all carry section flags `0x7` — the same `0x4` "executable" bit as `.text` — so the first
  version of this tool found **zero** data references, having skipped the data as code. The split by
  *name* (`.rdata`/`.data`/`.data1` are data) is what holds, and it finds 23,831 dword references into
  the 1,436 bodies that sit in a table.
* **Six Ghidra extents differ from the map's size, and they are exactly the rows `make boundaries`
  resized.** `0x0002f13c` 1,764 vs 2,282, `0x0002feb0` 5,277 vs 5,327, `0x0012c850` 2,425 vs 2,451,
  `0x0015a200` 1,094 vs 1,109, `0x001ee690` 10,114 vs 10,145, `0x002b7560` 1,324 vs 1,334. The
  export reads the analysis's own extent and `make boundaries` does not feed a size back into Ghidra,
  so `--check` counts the drift instead of gating on it. The earlier reading here — `0x000816e0`, 128
  bytes against 72 — is gone: on a fresh analysis that body's extent and its census size agree.

## What this does not settle

* **Library versus engine per body.** No signature database (U9), so `silent` is a population, not a
  label. 4.2 owns it.
* **4,004 `FUN_*` names.** The census says which bodies are worth naming first — the ones with ≥ 1
  caller and a literal — not what they are called.
* **Stock-versus-fork diffs.** Without a stock UE2.5 image of this compiler there is nothing to diff
  against; the fork surface stays an argument from the fork packages (`docs/systems/FORK_SURFACE.md`)
  plus bodies like `FUN_00055ed0`, which is too generic to be a fork change.

## Reproducing

    export SG1_DUMP=$HOME/nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15 WORK=$HOME/sg1/work
    tools/ghidra/export-module.sh $SG1_DUMP $GHIDRA_HOME Core,Engine,...     # once, 262 s
    make module-census                                                       # 0.8 s over 6,150 files
    python3 tools/module-export-census.py --report                           # the tables
    python3 tools/module-export-census.py --check                            # dump-free, in `make ci`

`--write-doc docs/MODULE_CENSUS.md` regenerates the block above; `--check` fails if it drifts. The
bodies themselves are game-derived and stay under `$WORK` — the committed artifact is addresses and
counts, and `--check` reconciles it against `module_export_rounds.csv`,
`function_modules.csv`/`function_module_attribution.csv` and itself.
