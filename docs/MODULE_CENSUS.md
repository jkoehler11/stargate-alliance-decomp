# What the 6,063 exported bodies are (PLAN 4.8's native half)

Status: **measured** — the export's population is classified four ways, and the module attribution is
independently checked against each body's own `__FILE__` path: **837 of 848 bodies that name a `.cpp`
name the module they were attributed to** (2026-11-10).
Artifact: `inventory/module_export_census.csv` (6,063 rows, one per exported body).
Tools: `tools/module-export-census.py` (`make module-census`; `--check` in `make ci`).
Input: the 12 exporter rounds from `tools/ghidra/export-module.sh` (`docs/MODULE_SPLIT.md`), which
leave 19.5 MB of decompiled C under `$WORK/module-export`.

## The question, and why the answer is a classifier

4.5 paid for the export and 4.8 is left with its output: 6,063 decompiled bodies. The plan line asks
what Perception changed against stock UE2.5 — renderer, memory, compiler behaviour — and the note
before this one put the open half plainly: *whether `Core`/`Engine` are mostly library code is settled
by a classifier over the exported bodies (section, call fan-in, string refs), not by another pass*.

So this is four independent reads of the same 6,063 files, none of which trusts the decompiler's prose:

* **section** — the XBE section the address falls in. All **6,063** are in `.text`; not one is an
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
| `Core` | 3238 | 2 | 102 | 564 | 3 | 10 | 118 | 329 | 2110 |
| `Engine` | 2094 | 0 | 25 | 562 | 5 | 13 | 96 | 360 | 1033 |
| `IpDrv` | 157 | 0 | 4 | 22 | 0 | 0 | 22 | 15 | 94 |
| `Gui` | 145 | 0 | 1 | 54 | 1 | 0 | 6 | 25 | 58 |
| `D3DDrv` | 129 | 0 | 0 | 22 | 0 | 2 | 4 | 53 | 48 |
| `stargategame` | 88 | 0 | 0 | 31 | 0 | 0 | 1 | 26 | 30 |
| `stargatevehicles` | 52 | 0 | 1 | 12 | 0 | 0 | 0 | 14 | 25 |
| `XboxAudio` | 42 | 0 | 0 | 9 | 0 | 0 | 5 | 3 | 25 |
| `Fire` | 41 | 0 | 0 | 9 | 0 | 0 | 1 | 5 | 26 |
| `XboxLive` | 31 | 0 | 0 | 11 | 0 | 0 | 11 | 3 | 6 |
| `Gameplay` | 24 | 0 | 0 | 9 | 0 | 0 | 0 | 10 | 5 |
| `XboxDrv` | 22 | 0 | 0 | 5 | 0 | 0 | 1 | 4 | 12 |
| **total** | **6063** | **2** | **133** | **1310** | **9** | **25** | **265** | **847** | **3472** |

| measurement | bodies | share |
|---|---:|---:|
| direct call sites into the set | 40794 sites | |
| at least one caller | 4232 | 69.8% |
| in a vtable or data table | 1428 | 23.6% |
| no caller and no data reference | 519 | 8.6% |
| carry a source path (TU-attributed) | 1310 | 21.6% |
| carry no literal at all | 3472 | 57.3% |
| decompile failed | 2 | 0.0% |

| attributed module | bodies | a `.cpp` path names it | a `.cpp` names another | header path only | none |
|---|---:|---:|---:|---:|---:|
| `Core` | 3238 | 114 | 11 | 439 | 2674 |
| `Engine` | 2094 | 551 | 0 | 11 | 1532 |
| `IpDrv` | 157 | 22 | 0 | 0 | 135 |
| `Gui` | 145 | 54 | 0 | 0 | 91 |
| `D3DDrv` | 129 | 21 | 0 | 1 | 107 |
| `stargategame` | 88 | 31 | 0 | 0 | 57 |
| `stargatevehicles` | 52 | 12 | 0 | 0 | 40 |
| `XboxAudio` | 42 | 9 | 0 | 0 | 33 |
| `Fire` | 41 | 9 | 0 | 0 | 32 |
| `XboxLive` | 31 | 11 | 0 | 0 | 20 |
| `Gameplay` | 24 | 9 | 0 | 0 | 15 |
| `XboxDrv` | 22 | 5 | 0 | 0 | 17 |
| **total** | **6063** | **848** | **11** | **451** | **4753** |

Disagreements (`attributed` -> `its own .cpp`): Core->Engine 8, Core->IpDrv 2, Core->XboxAudio 1

| address | name | attributed | its own `.cpp` | size |
|---|---|---|---|---:|
| `0x0002f820` | `Core_UnTemplate` | `Core` | `XboxAudio` | 518 |
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

Names: `FUN_*` **4608**, module-prefixed **1298**, `ucpp_*` **39**, `Native_*` **26**, `thunk_*` **17**, other **75** of 6063.
<!-- /generated:module-census -->

## `Core` is not mostly library code by the evidence the bodies carry

The one question 4.8 asked of this pass. `Core`'s 3,238 bodies split as **564 that declare their own
source path**, **102 `ret`-only slots**, **2 failed decompiles**, **3 asset references**, and **2,110
that carry no literal at all**. `Engine` reads better: 562 of 2,094 carry a source path and 1,033 carry
none. Across all twelve modules 3,472 of 6,063 (57.3%) carry no literal whatsoever.

That is the shape of a static link — the libraries assert nothing — but it is not a label, and calling
those 3,472 "library" would be a guess: a UE2 engine function without an assert has exactly the same
signature. What the census *can* say is where the library population starts. The 100 most-called
bodies in the set are 83 `Core` and 45 of them carry no literal, with `FUN_00055ed0` — 37 bytes, 3,279
call sites — and `appAssertFailed` (2,448) at the top, which is what a `memset` and an assert handler
look like from the outside. Naming them is 4.2's signature work (U9), not a fifth census.

## What it settles about the population

**6,063 is the right set and the wrong work list.** Every attributed address is a function entry in
`.text` (0 inside another function, 0 missing — the exporter's own result, re-read here), and the
attribution survives an independent check: of the 848 bodies whose literals name a `.cpp`, 837 name
the module they were attributed to. The eleven that do not are in the generated block above, and they
point one way: every one is a body attributed `Core` whose own `.cpp` is `Engine` (8), `IpDrv` (2) or
`XboxAudio` (1) — `UnClass.cpp` and `UnTemplate.cpp` compile under `Engine` in this build's tree, so
the `Core_` prefix on those bodies is the weaker of the two labels.

Eleven of 6,063 is 0.18%, and the check is worth having anyway: it is the first time the locality rule
has been scored against evidence inside the bodies rather than against held-out asserts (87.9%,
`docs/MODULE_ATTRIBUTION.md`). Reconciling the eleven is an address-level correction to
`function_modules.csv`/`function_module_attribution.csv` (4.5's artifacts, `--check`-gated against
`xbe_functions_by_module.csv`), not a census edit — they are named here as that work list rather than
changed here.

**150 of the 6,063 are not analysis targets.** 133 are `ret`-only slots (median 5 bytes — a native
registration whose C++ side does nothing) and 17 are jump thunks (`thunk_FUN_*`). So the native half's
work list is **5,913 bodies**, of which **1,310** can be tied to a translation unit today and
**4,753** cannot. 519 of the 6,063 are reached by neither a direct call nor a data reference.

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
  *name* (`.rdata`/`.data`/`.data1` are data) is what holds, and it finds 23,821 dword references into
  the 1,428 bodies that sit in a table.
* **One body's Ghidra extent runs past its census size.** `0x000816e0` is 128 bytes in the export and
  72 in `function_modules.csv` — the only disagreement in 6,063, so `--check` does not gate on size,
  and the exporter's "0 inside another function" stands for the entries it tested.

## What this does not settle

* **Library versus engine per body.** No signature database (U9), so `silent` is a population, not a
  label. 4.2 owns it.
* **4,625 `FUN_*` names.** The census says which bodies are worth naming first — the ones with ≥ 1
  caller and a literal — not what they are called.
* **Stock-versus-fork diffs.** Without a stock UE2.5 image of this compiler there is nothing to diff
  against; the fork surface stays an argument from the fork packages (`docs/systems/FORK_SURFACE.md`)
  plus bodies like `FUN_00055ed0`, which is too generic to be a fork change.

## Reproducing

    export SG1_DUMP=$HOME/nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15 WORK=$HOME/sg1/work
    tools/ghidra/export-module.sh $SG1_DUMP $GHIDRA_HOME Core,Engine,...     # once, 268 s
    make module-census                                                       # 0.7 s over 6,063 files
    python3 tools/module-export-census.py --report                           # the tables
    python3 tools/module-export-census.py --check                            # dump-free, in `make ci`

`--write-doc docs/MODULE_CENSUS.md` regenerates the block above; `--check` fails if it drifts. The
bodies themselves are game-derived and stay under `$WORK` — the committed artifact is addresses and
counts, and `--check` reconciles it against `module_export_rounds.csv`,
`function_modules.csv`/`function_module_attribution.csv` and itself.
