# Module attribution beyond the asserts (PLAN 4.5)

Status: **measured, with a rule that scores 89.9% on held-out labels** -- **95.7%** where both neighbours agree and **68.8%** where they disagree, which is the split a reader needs (`--holdout 400`, 2026-11-10).
Artifact: `inventory/function_module_attribution.csv` (14,973 rows, 6,092 of them with a module).
Tool: `tools/module-attribution.py` (`make module-attribution`; `--check` in `make ci`).

## The gap this closes

`inventory/function_modules.csv` attributes a function to a module only when the build already named it that
way — the name's prefix comes from the `__FILE__` literal the function's own assert passes, so **1,375 of 14,973**
functions carry a real module (`Core`, `Engine`, `Gui`, …). The remaining 13,598 are `(unnamed)` or one of the three
name-only buckets (`(dispatch: declared)`, `(dispatch: slot only)`, `(other named)`).

That number is not a fresh measurement of the build; it is the size of the *assert* evidence. Per-module work
(4.5's whole point — hand a module's functions to an exporter instead of re-analysing all 14,973) needs the
other 13,000 to have a home, and the code around them is attributed: `module-split`'s `run` column already
shows this build orders code **per translation unit**, so a file's functions sit together in address order.

## Three rules, scored the same way

`--holdout 400` hides 400 of the 1,375 asserted labels, re-runs each rule with those labels gone, and counts how
many come back — exact module match, on the same sample, for every rule. A rule that cannot recover a label the
build itself wrote is not evidence of anything.

| rule | what it does | labelled | exact |
|---|---|---:|---:|
| **locality** | the module of the nearest attributed function in address order, within `--max-gap` | 355 | **319 (89.9%)** |
| `neighbours` | one vote per attributed caller and callee, ≥2 votes and a ⅔ majority | 165 | 125 (75.8%) |
| `weighted` | the same, with a callee's vote divided by how many functions call it | 35 | 30 (85.7%) |

The two call-graph rules are the obvious approach and both fail, for a reason worth recording: **every file
calls Core.** `neighbours` reproduces Core 97 of 100 and misses 34 of 61 Engine labels, because a
function in `Engine/UnActor.cpp` calls Core array helpers and those votes outnumber its one Engine caller. The
held-out row that reads "Core 97/100 while Engine is 27/61" is the symptom, not the success. Weighting a callee by its caller count
removes the bias by removing the labels: it only ever labels 35 of the 400.

Locality is right about nine in ten and wrong in a way that is visible in the artifact: a label carries the
distance to the assert that produced it (`evidence`), so a reader can see how far it reached.

### What a disputed label is worth

Two thirds of the labels come from a pair of neighbours that name the same module; the rest are decided by the nearer
of two neighbours that contradict each other (`share` 1.000 against 0.500 in the artifact). Scored on the same
held-out sample, those are not the same evidence:

| the label's own confidence | held-out exact |
|---|---:|
| both neighbours agree (`share` 1.000) | **266 of 278 (95.7%)** |
| the neighbours disagree (`share` 0.500) | 53 of 77 (68.8%) |

So a disputed row is a majority guess, not a coin: right two times in three, and the home of 24 of the rule's 36
held-out errors. The 1,685 disputed rows in the artifact are the part of it worth re-reading before acting on a
single function, and they are the part that **moves when a name lands beside it**. Seeding 15 class-table stores
(2026-11-10) added 13 names; six functions then had a nearer assert on the other side of their boundary --
`0x0012ea70`, `0x0012eb30`, `0x001c4e60`, `0x001c4f30` moved `Core` -> `Engine`, `0x002c50b0`, `0x002c5180` moved
`Core` -> `IpDrv` -- without anything about the functions changing. That is why
`tools/module-export-census.py --check` holds this artifact to its **`assert`** rows only, and reports the propagated
rows that moved since the export round instead of failing: the round is a snapshot of an older run of this
heuristic, not a second opinion about the name.

## How far a label may reach

The library sections (`XONLINE`, `XGRPH`, `D3D`, `DSOUND`, …) carry almost no asserts, so an unbounded nearest
neighbour hands thousands of their functions to whichever module happens to sit closest — it labels 13,998 of 14,973
and hands most of them to whichever module happens to sit closest. The sweep picks the bound; the numbers are what `--holdout 400` printed:

| `--max-gap` | functions labelled | held-out exact |
|---|---:|---:|
| `0x200` | 1,906 | 236 / 256 (92.2%) |
| `0x400` | 3,114 | 286 / 315 (90.8%) |
| **`0x800`** (default) | **4,637** | **319 / 355 (89.9%)** |
| `0x1000` | 5,940 | 336 / 381 (88.2%) |
| `0x2000` | 6,943 | 345 / 395 (87.3%) |
| `0x4000` | 7,517 | 347 / 398 (87.2%) |
| unbounded | 13,998 | 348 / 400 (87.0%) |

Accuracy barely moves with the bound; coverage halves every time it halves. The default is `0x800` because a
consumer that wants the strict subset can ask for `--max-gap 0x200` from the same tool and get 92.2%, and the
artifact's `depth` and `evidence` columns keep the loose labels distinguishable.

The `functions labelled` column is the **scoring** run's, so it undercounts by however many of the 400 hidden
labels it failed to recover: with every seed in place the same bound labels **6,092** rows, not 4,637.

## What the artifact says

With the default rule and every seed present: **6,092 of 14,973 functions carry a module (40.69%), 4,717 of them
propagated** (against 1,375 asserted). The distribution is plausible against the asserted seeds:

| module | functions | asserted |
|---|---:|---:|
| `Core` | 3,244 | 581 |
| `Engine` | 2,104 | 593 |
| `IpDrv` | 159 | 24 |
| `Gui` | 155 | 59 |
| `D3DDrv` | 129 | 22 |
| `stargategame` | 88 | 32 |
| `stargatevehicles` | 52 | 12 |
| `XboxAudio` | 42 | 9 |
| `Fire` | 41 | 9 |
| `XboxLive` | 32 | 20 |
| `Gameplay` | 24 | 9 |
| `XboxDrv` | 22 | 5 |

`(unnamed)` falls from 13,598 to **8,881**, which is code the asserts do not reach: CRT start-up, the statically
linked libraries, and functions whose TU has no assert within 2 KB.

## The artifact that shipped was a scoring run's output — corrected here

`main()` wrote the artifact from whatever rule result the run produced, including a `--holdout` run — and the
artifact that landed in #318 came from `--holdout 400`. Hiding 400 seeds makes the rows that carry one look
unattributed, so the file it wrote:

- labelled **354 rows `propagated` from a neighbour although their own assert names a module** (43 of them cite a
  *different* module: `Engine_AxPickupBase` → `Core`, `KScript` → `Core`, `XboxAudio_XboxAudioSubsystem` → `Core`);
- left **430 rows unlabelled** that the full-seed run attributes, because their nearest label had been hidden;
- and put every seed-dependent total in this page and in `PLAN.md` 400 low ("948 asserted", "5,633", "14,009").

The **score is unaffected** — the same `--holdout 400` run answered 311 of 354 (87.9%) on that symbol set and answers 319 of 355 (89.9%) on today's, which is what a holdout measures — so this is a defect in the artifact and the totals, not in the rule.

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
  function at the boundary takes its neighbour's module. That is the 10% the holdout measures.
- **`(unnamed)` is a real answer.** 8,881 rows stay unattributed and are not guesses with the label stripped.

## Reproduction

```
export SG1_DUMP=$HOME/nfs/sg1-dump
make module-attribution                       # writes inventory/function_module_attribution.csv
python3 tools/module-attribution.py --xbe $SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe \
    --holdout 400                             # every rule, and the gap sweep, scored; writes no artifact
python3 tools/module-attribution.py --check   # dump-free: columns, order, and the rule's own invariants
```

The consumer is `make module-split --lists`: it emits one address list per module for `ExportAt.java`, and that
list is the union of this artifact's `module` column with the asserted names -- 6,092 addresses, which is what the
2026-11-09 export round ran on before 29 of them gained a name.
