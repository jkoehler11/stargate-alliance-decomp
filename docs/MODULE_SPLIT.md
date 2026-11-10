# Per-module work: the address lists, and what a pass over them costs (PLAN 4.5)

Status: **the input is measured; the exporter pass itself has not been run.**
Artifacts: `inventory/function_modules.csv` (14,957 rows), `inventory/module_export_plan.csv` (12 rows).
Tools: `tools/module-split.py` (`make module-split`, `make module-lists`; `--check` in `make ci`).

## A module is a set of addresses, never a range

PLAN 4.5 asks for the XBE split into per-module Ghidra projects "using the assert-path map from 2.3". The map is
per *function*: `inventory/function_modules.csv` attributes each function by the `__FILE__` assert its own body
carries (plus the evidence overrides), and `inventory/function_module_attribution.csv` adds the functions whose
module came from address locality (87.9% on held-out asserts, `docs/MODULE_ATTRIBUTION.md`). The union is
**6,063 of 14,957 functions (40.54%) in 12 modules**.

Their addresses are nowhere near contiguous: the 12 modules' 6,063 functions occupy **5,826 contiguous runs**, a
mean of **1.04 functions per run**, and `Core` is 3,238 functions in 3,125 runs across a 3.2 MB span. That is the
same property the `run` column has measured since `function_modules.csv` landed — this build orders code per
translation unit, and the compiler emits each function of a TU together, so a module is a *scatter of individual
bodies*, not a region. There is no range to cut, and therefore no project boundary to draw.

## The plan

`inventory/module_export_plan.csv` is what the union amounts to per module, so a pass can be sized before it is
paid for (`make module-split` regenerates it; `--check` recomputes it from the two attribution artifacts and fails
if a row differs):

| module | functions | asserted | propagated | runs | bytes | first | last |
|---|---:|---:|---:|---:|---:|---|---|
| `Core` | 3,238 | 574 | 2,664 | 3,125 | 842,694 | `0x00012110` | `0x0032ae50` |
| `Engine` | 2,094 | 586 | 1,508 | 2,002 | 707,214 | `0x00033060` | `0x00298890` |
| `IpDrv` | 157 | 22 | 135 | 151 | 35,110 | `0x002c3b70` | `0x002d8cc0` |
| `Gui` | 145 | 55 | 90 | 141 | 44,170 | `0x002ec180` | `0x0030b750` |
| `D3DDrv` | 129 | 22 | 107 | 127 | 37,786 | `0x002aa300` | `0x002c0c50` |
| `stargategame` | 88 | 32 | 56 | 82 | 20,271 | `0x003165b0` | `0x003237a0` |
| `stargatevehicles` | 52 | 12 | 40 | 48 | 13,318 | `0x0030c2e0` | `0x003164a0` |
| `XboxAudio` | 42 | 9 | 33 | 40 | 14,938 | `0x0002dee0` | `0x00033d20` |
| `Fire` | 41 | 9 | 32 | 39 | 10,188 | `0x002e1130` | `0x002e91f0` |
| `XboxLive` | 31 | 13 | 18 | 30 | 12,163 | `0x00325b24` | `0x00328af0` |
| `Gameplay` | 24 | 9 | 15 | 20 | 7,300 | `0x003238b0` | `0x00324d60` |
| `XboxDrv` | 22 | 5 | 17 | 21 | 4,349 | `0x0032c010` | `0x0032dff0` |

`asserted` is a name prefix or an evidence override; `propagated` is the locality label. The 8,894 functions no
rule reaches are CRT start-up, the statically linked libraries (whose sections carry almost no asserts) and bodies
whose translation unit has no assert within 2 KB.

## The lists

`make module-lists` writes one file per module into `$WORK/module-lists` — 12 files, 6,063 addresses, ascending,
which is what `ExportAt.java @<file>` takes (address lists are derived work, so they stay out of git; `--lists`
refuses to be pointed at `inventory/`, by convention rather than by code, because an address list of game code is
not something to commit).

**One address per invocation is the cost, not one file.** `ExportAt.java` exports a single procedure and writes
`<out>/<address>.c`, so the pass over a module's list is 3,125 invocations for `Core` and 5,826 for all twelve —
each of which is one `-process default.xbe -noanalysis -postScript` round against a kept project. That is the
measurement 4.5 needed: a per-module Ghidra *project* would save nothing over one project plus an address list,
because the list is already 1.04 functions long per fragment.

## Not measured

- **The exporter pass itself.** No body has been exported from these lists; the plan prices it, it does not run it.
- **Whether 6,063 is the right population for 4.8.** 4.8's native half wants the renderer, memory and compiler
  behaviour; `Core` and `Engine` are 88% of the covered functions and may be mostly library code, which is a
  question for the exporter's output, not for the labels.
- **The 8,894 unreached functions.** They have no module and no plan row; a pass over them needs a different rule
  (the library sections are the obvious place to start, and their absence from the asserts is the reason the
  locality bound exists).

## Reproduction

```
export SG1_DUMP=$HOME/nfs/sg1-dump
make module-split                  # function_modules.csv + module_export_plan.csv
make module-lists                  # 12 address lists under $WORK/module-lists
python3 tools/module-split.py --check
```

The attribution half of the union is `docs/MODULE_ATTRIBUTION.md`; the assert half is the rollup reconciliation in
`--check`, which keeps `inventory/function_modules.csv` equal to `inventory/xbe_functions_by_module.csv`.
