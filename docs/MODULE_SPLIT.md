# Per-module work: the address lists, and what a pass over them costs (PLAN 4.5)

Status: **measured end to end** — the import plus twelve exporter rounds decompile 6,147 of the 6,150 attributed
functions in 262 s of rounds (2026-11-20; the first pass, 2026-11-10, ran the same twelve rounds in 266 s and
268 s over a stale 6,063-address list set).
Artifacts: `inventory/function_modules.csv` (16,375 rows), `inventory/module_export_plan.csv` (12 rows),
`inventory/module_export_rounds.csv` (12 rounds, one per module).
Tools: `tools/module-split.py` (`make module-split`, `make module-lists`; `--check` in `make ci`),
`tools/ghidra/export-module.sh` (the pass driver).

## A module is a set of addresses, never a range

PLAN 4.5 asks for the XBE split into per-module Ghidra projects "using the assert-path map from 2.3". The map is
per *function*: `inventory/function_modules.csv` attributes each function by the `__FILE__` assert its own body
carries (plus the evidence overrides), and `inventory/function_module_attribution.csv` adds the functions whose
module came from address locality (89.9% on held-out asserts; 95.7% where both neighbours agree, `docs/MODULE_ATTRIBUTION.md`). The union is
**7,370 of 16,375 functions (45.01%) in 12 modules** (re-derived from the artifact 2026-11-21). The module count is
unchanged at 12, so the growth is function splitting rather than new modules.

Their addresses are nowhere near contiguous: the 12 modules' 7,370 functions occupy **7,052 contiguous runs**, a
mean of **1.05 functions per run**, and `Core` is 3,078 functions in 2,950 runs across a 3.2 MB span. That is the
same property the `run` column has measured since `function_modules.csv` landed — this build orders code per
translation unit, and the compiler emits each function of a TU together, so a module is a *scatter of individual
bodies*, not a region. There is no range to cut, and therefore no project boundary to draw.

## The plan

`inventory/module_export_plan.csv` is what the union amounts to per module, so a pass can be sized before it is
paid for (`make module-split` regenerates it; `--check` recomputes it from the two attribution artifacts and fails
if a row differs):

| module | functions | asserted | propagated | runs | bytes | first | last |
|---|---:|---:|---:|---:|---:|---|---|
| `Core` | 3078 | 677 | 2401 | 2950 | 806,272 | `0x00012110` | `0x0032b210` |
| `Engine` | 2915 | 882 | 2033 | 2785 | 977,915 | `0x00033329` | `0x00298890` |
| `Gui` | 267 | 83 | 184 | 256 | 81,082 | `0x002ebad0` | `0x0030b750` |
| `IpDrv` | 232 | 41 | 191 | 223 | 51,795 | `0x00025ea0` | `0x002d8cc0` |
| `stargategame` | 217 | 72 | 145 | 203 | 42,362 | `0x000182a0` | `0x00323690` |
| `D3DDrv` | 145 | 31 | 114 | 143 | 47,611 | `0x002aa300` | `0x002c0c50` |
| `XboxAudio` | 131 | 45 | 86 | 122 | 28,170 | `0x00029c00` | `0x00033d20` |
| `stargatevehicles` | 97 | 29 | 68 | 91 | 23,809 | `0x00023dd0` | `0x003164a0` |
| `XboxDrv` | 95 | 18 | 77 | 94 | 9,728 | `0x00017900` | `0x0032dff0` |
| `Gameplay` | 81 | 19 | 62 | 77 | 11,887 | `0x0001b710` | `0x00324d60` |
| `Fire` | 80 | 25 | 55 | 77 | 20,109 | `0x000255c0` | `0x002e91f0` |
| `XboxLive` | 32 | 21 | 11 | 31 | 13,027 | `0x00325b24` | `0x00328e00` |
| **total** | **7370** | **1943** | **5427** | **7052** | **2,113,767** | | |

`asserted` is a name prefix or an evidence override; `propagated` is the locality label. The 9,005 functions no
rule reaches are CRT start-up, the statically linked libraries (whose sections carry almost no asserts) and bodies
whose translation unit has no assert within 2 KB. `XboxAudio`'s 131 is 45 asserted; the 2026-11-10 plan carried 115
because its lists had not been rewritten from the artifact, and the assert seeds have landed twice since.
Five of the 1,943 `asserted` are evidence overrides `make body-name` wrote for a body whose own class literal names its
method (`UXBoxAudioSubsystem::ShutdownAfterError` and four like it), not `__FILE__` asserts; the rollup counts both.

## The lists

`make module-lists` writes one file per module into `$WORK/module-lists` — 12 files, 7,370 addresses, ascending,
which is what `ExportAt.java @<file>` takes (address lists are derived work, so they stay out of git; `--lists`
refuses to be pointed at `inventory/`, by convention rather than by code, because an address list of game code is
not something to commit).

## The pass, measured

`ExportAt.java` takes the whole list as one `@file` argument and loops its addresses **inside one invocation**, so
the pass costs **one import plus one round per module** — 12 rounds, not 5,854 invocations. The earlier reading of
this page had it the other way round; the per-address cost it assumed is a per-*round* cost. `tools/ghidra/export-module.sh`
drives the real shape: it imports `default.xbe` with the same recipe as `tools/ghidra/run-analysis.sh` (minus
`-deleteProject` — the project is the reusable half), keeps it at `build/ghidra-module`, and runs one
`-process default.xbe -noanalysis -postScript ExportAt.java @<list>` round per module, timing each round.

| module | addresses | exported | inside | missing | unnamed | failed | seconds | C bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `Core` | 3221 | 3220 | 0 | 1 | 2148 | 2 | 174 | 9,253,723 |
| `Engine` | 2112 | 2111 | 0 | 1 | 1326 | 0 | 34 | 7,745,395 |
| `IpDrv` | 159 | 159 | 0 | 0 | 120 | 0 | 6 | 461,460 |
| `Gui` | 155 | 155 | 0 | 0 | 90 | 0 | 6 | 582,094 |
| `D3DDrv` | 129 | 129 | 0 | 0 | 104 | 0 | 6 | 413,422 |
| `XboxAudio` | 115 | 114 | 0 | 1 | 72 | 0 | 5 | 339,971 |
| `stargategame` | 88 | 88 | 0 | 0 | 51 | 0 | 5 | 275,285 |
| `stargatevehicles` | 52 | 52 | 0 | 0 | 40 | 0 | 6 | 172,409 |
| `Fire` | 41 | 41 | 0 | 0 | 27 | 0 | 5 | 130,089 |
| `XboxLive` | 32 | 32 | 0 | 0 | 12 | 0 | 6 | 160,918 |
| `Gameplay` | 24 | 24 | 0 | 0 | 15 | 0 | 4 | 82,414 |
| `XboxDrv` | 22 | 22 | 0 | 0 | 15 | 0 | 5 | 61,098 |
| **total** | **6150** | **6147** | **0** | **3** | **4020** | **2** | **262** | **19,678,278** |

Four things the table settles.

- **Every address the analysis holds a function for exports at its own address.** 6,147 of 6,150, and not one fell
  inside another function. The three that did not are bodies the analysis never made a function of — `0x0003bd30`,
  `0x0024af30` and `0x0002fa30`, the last the slot-33 audio body U36 added — each a gap body with no row in
  `symbols/xbe_overrides.csv` or `xbe_dispatch.csv` (which are what the importer builds a function from) and
  nothing xref. `--check` prints the rounds' `missing` column rather than pretending the set is whole.
- **The cost is decompile time, not process startup.** Per-body cost tracks body size: `Engine`'s 2,111 bodies cost
  34 s (16 ms each) while `Core`'s 3,220 cost 174 s (54 ms each), because Core carries the large CRT and engine
  bodies. A short round (22–159 addresses) is 4–6 s, which is the round's fixed cost (JVM, project open, the map's
  names applied); the mean over the pass is **43 ms per body**.
- **A pass is a snapshot of the analysis, so a second import is a new read, not a re-run.** The 2026-11-10 pass ran
  twice and reproduced its 6,063 files byte for byte (266 s, 268 s); this pass imports a fresh project (the earlier
  one is git-ignored and gone after a sync), so its file set is 6,147 and its per-module byte counts differ. The
  numbers above are one run; the determinism claim belongs to a re-run of the same project.
- **The residue is naming, not decompilation.** Two decompile failures (both in `Core`, out of 6,147) and 4,020
  bodies still named `FUN_*`: the pass exports the code, it cannot name it. That is 4.2/4.9's work, and the unnamed
  count per module is the honest measure of how much naming each module still needs — `Core` 2,148 of 3,220 (67%),
  `Engine` 1,326 of 2,111 (63%), `IpDrv` 120 of 159 (75%), `Gui` 90 of 155 (58%), `D3DDrv` 104 of 129 (81%), and
  the game-specific/Xbox modules 38-77% (lowest `XboxLive` 12 of 32, highest `stargatevehicles` 40 of 52).

Re-priced: 5,909 contiguous runs remains the right shape for *reading* work — a run is what a human takes on at a
time — but it is not the cost of the pass. The pass is 12 rounds and under five minutes of them, on one kept
project. A per-module Ghidra project still buys nothing.

## Not measured

- **Whether 6,150 is the right population for 4.8.** The pass answers what the bodies cost and how much is still
  unnamed; it does not answer what they are. `Core` and `Engine` are 88% of the covered functions and may be mostly
  library code — that needs a classifier over the exported bodies (section, call fan-in, string refs), not another
  pass.
- **The 9,005 unreached functions.** They have no module and no plan row; a pass over them needs a different rule
  (the library sections are the obvious place to start). The pass is cheap enough to run speculatively now that it
  is priced at 43 ms a body, but there is still no list to run it over.

## Reproduction

```
export SG1_DUMP=$HOME/nfs/sg1-dump
make module-split                  # function_modules.csv + module_export_plan.csv
make module-lists                  # 12 address lists under $WORK/module-lists
python3 tools/module-split.py --check

# the pass: one import, then one round per module (game-derived C under $WORK/module-export)
sh tools/ghidra/export-module.sh "$SG1_DUMP" "$HOME/tools/ghidra_12.2_PUBLIC" \
    Core,Engine,IpDrv,Gui,D3DDrv,stargategame,stargatevehicles,XboxAudio,Fire,XboxLive,Gameplay,XboxDrv
cp "$WORK/module-export/rounds.csv" inventory/module_export_rounds.csv
```

The attribution half of the union is `docs/MODULE_ATTRIBUTION.md`; the assert half is the rollup reconciliation in
`--check`, which keeps `inventory/function_modules.csv` equal to `inventory/xbe_functions_by_module.csv`.
