# Module attribution beyond the asserts (PLAN 4.5)

Status: **measured, with a rule that scores 87.9% on held-out labels.**
Artifact: `inventory/function_module_attribution.csv` (14,957 rows, 6,063 of them with a module).
Tool: `tools/module-attribution.py` (`make module-attribution`; `--check` in `make ci`).

## The gap this closes

`inventory/function_modules.csv` attributes a function to a module only when the build already named it that
way — the name's prefix comes from the `__FILE__` literal the function's own assert passes, so **1,348 of 14,957**
functions carry a real module (`Core`, `Engine`, `Gui`, …). The remaining 13,609 are `(unnamed)` or one of the three
name-only buckets (`(dispatch: declared)`, `(dispatch: slot only)`, `(other named)`).

That number is not a fresh measurement of the build; it is the size of the *assert* evidence. Per-module work
(4.5's whole point — hand a module's functions to an exporter instead of re-analysing all 14,957) needs the
other 13,000 to have a home, and the code around them is attributed: `module-split`'s `run` column already
shows this build orders code **per translation unit**, so a file's functions sit together in address order.

## Three rules, scored the same way

`--holdout 400` hides 400 of the 1,348 asserted labels, re-runs each rule with those labels gone, and counts how
many come back — exact module match, on the same sample, for every rule. A rule that cannot recover a label the
build itself wrote is not evidence of anything.

| rule | what it does | labelled | exact |
|---|---|---:|---:|
| **locality** | the module of the nearest attributed function in address order, within `--max-gap` | 354 | **311 (87.9%)** |
| `neighbours` | one vote per attributed caller and callee, ≥2 votes and a ⅔ majority | 313 | 169 (54.0%) |
| `weighted` | the same, with a callee's vote divided by how many functions call it | 50 | 24 (48.0%) |

The two call-graph rules are the obvious approach and both fail, for a reason worth recording: **every file
calls Core.** `neighbours` reproduces Core perfectly (159/159) and misses 135 of 144 Engine labels, because a
function in `Engine/UnActor.cpp` calls Core array helpers and those votes outnumber its one Engine caller. The
held-out row that reads "Core 159/159" is the symptom, not the success. Weighting a callee by its caller count
removes the bias by removing the labels: it only ever labels 50 of the 400.

Locality is right about nine in ten and wrong in a way that is visible in the artifact: a label carries the
distance to the assert that produced it (`evidence`), so a reader can see how far it reached.

## How far a label may reach

The library sections (`XONLINE`, `XGRPH`, `D3D`, `DSOUND`, …) carry almost no asserts, so an unbounded nearest
neighbour hands thousands of their functions to whichever module happens to sit closest — it labels all 14,957
and calls 6,547 of them `XboxDrv`. The sweep picks the bound; the numbers are what `--holdout 400` printed:

| `--max-gap` | functions labelled | held-out exact |
|---|---:|---:|
| `0x200` | 1,912 | 238 / 259 (91.9%) |
| `0x400` | 3,168 | 283 / 318 (89.0%) |
| **`0x800`** (default) | **4,639** | **311 / 354 (87.9%)** |
| `0x1000` | 6,005 | 331 / 381 (86.9%) |
| `0x2000` | 6,968 | 340 / 394 (86.3%) |
| `0x4000` | 7,555 | 342 / 398 (85.9%) |
| unbounded | 14,009 | 343 / 400 (85.8%) |

Accuracy barely moves with the bound; coverage halves every time it halves. The default is `0x800` because a
consumer that wants the strict subset can ask for `--max-gap 0x200` from the same tool and get 91.9%, and the
artifact's `depth` and `evidence` columns keep the loose labels distinguishable.

The `functions labelled` column is the **scoring** run's, so it undercounts by however many of the 400 hidden
labels it failed to recover: with every seed in place the same bound labels **6,063** rows, not 4,639.

## What the artifact says

With the default rule and every seed present: **6,063 of 14,957 functions carry a module (40.54%), 4,715 of them
propagated** (against 1,348 asserted). The distribution is plausible against the asserted seeds:

| module | functions | asserted |
|---|---:|---:|
| `Core` | 3,238 | 574 |
| `Engine` | 2,094 | 586 |
| `IpDrv` | 157 | 22 |
| `Gui` | 145 | 55 |
| `D3DDrv` | 129 | 22 |
| `stargategame` | 88 | 32 |
| `stargatevehicles` | 52 | 12 |
| `XboxAudio` | 42 | 9 |
| `Fire` | 41 | 9 |
| `XboxLive` | 31 | 13 |
| `Gameplay` | 24 | 9 |
| `XboxDrv` | 22 | 5 |

`(unnamed)` falls from 13,609 to **8,412**, which is code the asserts do not reach: CRT start-up, the statically
linked libraries, and functions whose TU has no assert within 2 KB.

## The artifact that shipped was a scoring run's output — corrected here

`main()` wrote the artifact from whatever rule result the run produced, including a `--holdout` run — and the
artifact that landed in #318 came from `--holdout 400`. Hiding 400 seeds makes the rows that carry one look
unattributed, so the file it wrote:

- labelled **354 rows `propagated` from a neighbour although their own assert names a module** (43 of them cite a
  *different* module: `Engine_AxPickupBase` → `Core`, `KScript` → `Core`, `XboxAudio_XboxAudioSubsystem` → `Core`);
- left **430 rows unlabelled** that the full-seed run attributes, because their nearest label had been hidden;
- and put every seed-dependent total in this page and in `PLAN.md` 400 low ("948 asserted", "5,633", "14,009").

The **score is unaffected** — the same `--holdout 400` run still answers 311 of 354 (87.9%), which is what a
holdout measures — so this is a defect in the artifact and the totals, not in the rule.

Two things now prevent it: a `--holdout` run **writes nothing** (the artifact's md5 is unchanged across a scoring
run), and `--check` fails on any row that carries a real `asserted_module` but is not an `assert` row — the
invariant that would have caught it, and which answers
`0x0001afc0 carries the asserted module Core but is labelled propagated` against the shipped version. The corrected
artifact moves **632 module labels** (430 rows gain one, 202 change) and **784 methods** (354 `propagated` →
`assert`, 430 `none` → `propagated`); labelled 5,633 → 6,063.

## Limits — what a module label is not

- **It is not a name.** `docs/CONVENTIONS.md` is explicit: attribution says which translation unit's code this
  is and nothing about what the function does. Nothing here promotes a `FUN_*` to a semantic name.
- **It cannot see through a jump table or a virtual call.** The call-graph rules read `E8 rel32` only; the
  classes' own tables are what dispatch most engine code (slot 21 is `ProcessEvent`), so the graph understates
  reach — which is one more reason the winning rule is not a call-graph rule.
- **Interleaved TUs get the wrong label.** Two files compiled next to each other have no marker between them; a
  function at the boundary takes its neighbour's module. That is the 12% the holdout measures.
- **`(unnamed)` is a real answer.** 8,412 rows stay unattributed and are not guesses with the label stripped.

## Reproduction

```
export SG1_DUMP=$HOME/nfs/sg1-dump
make module-attribution                       # writes inventory/function_module_attribution.csv
python3 tools/module-attribution.py --xbe $SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe \
    --holdout 400                             # every rule, and the gap sweep, scored; writes no artifact
python3 tools/module-attribution.py --check   # dump-free: columns, order, and the rule's own invariants
```

The next consumer is `make module-split --lists`: it emits one address list per module for `ExportAt.java`, and
today that list is the 1,348 assert-attributed functions. Pointing it at this artifact's `module` column is the
follow-up that turns 4.5's per-module exporter pass from a sample into the file.
