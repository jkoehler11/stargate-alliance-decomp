# Per-module work: the address lists, and what a pass over them costs (PLAN 4.5)

Status: **measured end to end** — the import plus twelve exporter rounds decompile all 6,063 attributed functions
in 268 s of rounds (2026-11-10, twice: 266 s and 268 s, byte-identical output).
Artifacts: `inventory/function_modules.csv` (14,973 rows), `inventory/module_export_plan.csv` (12 rows),
`inventory/module_export_rounds.csv` (12 rounds, one per module).
Tools: `tools/module-split.py` (`make module-split`, `make module-lists`; `--check` in `make ci`),
`tools/ghidra/export-module.sh` (the pass driver).

## A module is a set of addresses, never a range

PLAN 4.5 asks for the XBE split into per-module Ghidra projects "using the assert-path map from 2.3". The map is
per *function*: `inventory/function_modules.csv` attributes each function by the `__FILE__` assert its own body
carries (plus the evidence overrides), and `inventory/function_module_attribution.csv` adds the functions whose
module came from address locality (89.9% on held-out asserts; 95.7% where both neighbours agree, `docs/MODULE_ATTRIBUTION.md`). The union is
**6,092 of 14,973 functions (40.69%) in 12 modules**.

Their addresses are nowhere near contiguous: the 12 modules' 6,092 functions occupy **5,854 contiguous runs**, a
mean of **1.04 functions per run**, and `Core` is 3,244 functions in 3,131 runs across a 3.2 MB span. That is the
same property the `run` column has measured since `function_modules.csv` landed — this build orders code per
translation unit, and the compiler emits each function of a TU together, so a module is a *scatter of individual
bodies*, not a region. There is no range to cut, and therefore no project boundary to draw.

## The plan

`inventory/module_export_plan.csv` is what the union amounts to per module, so a pass can be sized before it is
paid for (`make module-split` regenerates it; `--check` recomputes it from the two attribution artifacts and fails
if a row differs):

| module | functions | asserted | propagated | runs | bytes | first | last |
|---|---:|---:|---:|---:|---:|---|---|
| `Core` | 3,244 | 581 | 2,663 | 3,131 | 843,158 | `0x00012110` | `0x0032b210` |
| `Engine` | 2,104 | 593 | 1,511 | 2,011 | 711,533 | `0x00033060` | `0x00298890` |
| `IpDrv` | 159 | 24 | 135 | 153 | 35,469 | `0x002c3b70` | `0x002d8cc0` |
| `Gui` | 155 | 59 | 96 | 151 | 46,411 | `0x002ec180` | `0x0030b750` |
| `D3DDrv` | 129 | 22 | 107 | 127 | 37,786 | `0x002aa300` | `0x002c0c50` |
| `stargategame` | 88 | 32 | 56 | 82 | 20,271 | `0x003165b0` | `0x003237a0` |
| `stargatevehicles` | 52 | 12 | 40 | 48 | 13,318 | `0x0030c2e0` | `0x003164a0` |
| `XboxAudio` | 42 | 9 | 33 | 40 | 14,938 | `0x0002dee0` | `0x00033d20` |
| `Fire` | 41 | 9 | 32 | 39 | 10,188 | `0x002e1130` | `0x002e91f0` |
| `XboxLive` | 32 | 20 | 12 | 31 | 13,027 | `0x00325b24` | `0x00328e00` |
| `Gameplay` | 24 | 9 | 15 | 20 | 7,300 | `0x003238b0` | `0x00324d60` |
| `XboxDrv` | 22 | 5 | 17 | 21 | 4,349 | `0x0032c010` | `0x0032dff0` |

`asserted` is a name prefix or an evidence override; `propagated` is the locality label. The 8,881 functions no
rule reaches are CRT start-up, the statically linked libraries (whose sections carry almost no asserts) and bodies
whose translation unit has no assert within 2 KB.

## The lists

`make module-lists` writes one file per module into `$WORK/module-lists` — 12 files, 6,092 addresses (6,063 of them in the round measured below), ascending,
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
| `Core` | 3,238 | 3,238 | 0 | 0 | 2,593 | 2 | 179 | 9,314,551 |
| `Engine` | 2,094 | 2,094 | 0 | 0 | 1,493 | 0 | 36 | 7,681,115 |
| `IpDrv` | 157 | 157 | 0 | 0 | 135 | 0 | 6 | 455,126 |
| `Gui` | 145 | 145 | 0 | 0 | 86 | 0 | 6 | 555,576 |
| `D3DDrv` | 129 | 129 | 0 | 0 | 107 | 0 | 6 | 409,026 |
| `stargategame` | 88 | 88 | 0 | 0 | 56 | 0 | 5 | 275,155 |
| `stargatevehicles` | 52 | 52 | 0 | 0 | 40 | 0 | 5 | 172,420 |
| `XboxAudio` | 42 | 42 | 0 | 0 | 33 | 0 | 6 | 164,468 |
| `Fire` | 41 | 41 | 0 | 0 | 32 | 0 | 5 | 130,088 |
| `XboxLive` | 31 | 31 | 0 | 0 | 18 | 0 | 5 | 153,145 |
| `Gameplay` | 24 | 24 | 0 | 0 | 15 | 0 | 5 | 82,422 |
| `XboxDrv` | 22 | 22 | 0 | 0 | 17 | 0 | 4 | 60,917 |
| **total** | **6,063** | **6,063** | **0** | **0** | **4,625** | **2** | **268** | **19,454,009** |

Four things the table settles.

- **Every attributed address is a function start.** 6,063 of 6,063 exported at their own address — no address fell
  inside another function and none was missing. The lists can be handed to `ExportAt.java` unedited, and the
  attribution's address set is consistent with the function census `XbePost` names from.
- **The cost is decompile time, not process startup.** Per-body cost tracks body size: `Engine`'s 2,094 bodies cost
  36 s (17 ms each) while `Core`'s 3,238 cost 179 s (55 ms each), because Core carries the large CRT and engine
  bodies. A short round (22–145 addresses) is 4–6 s, which is the round's fixed cost (JVM, project open, the 12,962
  symbol names it applies); the mean over the pass is **44 ms per body**.
- **The output is deterministic.** Two runs of all twelve rounds, minutes apart, produced the same 6,063 files with
  identical byte counts per module (266 s and 268 s), so a re-run after a tool change is a clean diff.
- **The residue is naming, not decompilation.** Two decompile failures (both in `Core`, out of 6,063) and 4,625
  bodies still named `FUN_*`: the pass exports the code, it cannot name it. That is 4.2/4.9's work, and the unnamed
  count per module is the honest measure of how much naming each module still needs — `Core` 2,593 of 3,238 (80%),
  `Engine` 1,493 of 2,094 (71%), `IpDrv` 135 of 157 (86%), `Gui` 86 of 145 (59%), and the game-specific/Xbox
  modules 58-79% (lowest `XboxLive` 18 of 31, highest `XboxAudio` 33 of 42).

Re-priced: 5,854 contiguous runs remains the right shape for *reading* work — a run is what a human takes on at a
time — but it is not the cost of the pass. The pass is 12 rounds and under five minutes of them, on one kept
project. A per-module Ghidra project still buys nothing.

## Not measured

- **Whether 6,063 is the right population for 4.8.** The pass answers what the bodies cost and how much is still
  unnamed; it does not answer what they are. `Core` and `Engine` are 88% of the covered functions and may be mostly
  library code — that needs a classifier over the exported bodies (section, call fan-in, string refs), not another
  pass.
- **The 8,881 unreached functions.** They have no module and no plan row; a pass over them needs a different rule
  (the library sections are the obvious place to start). The pass is cheap enough to run speculatively now that it
  is priced at 44 ms a body, but there is still no list to run it over.

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
