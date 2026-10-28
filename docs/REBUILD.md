# The rebuild pipeline: four stages proven, four missing, and the compiler in between

PLAN 6.1 asks for `tools/build.sh` → compile → repack → place. This is that script, and the interesting result is
not that it runs: it is *which* stages run. Four of the eight are proven today, four are blocked, and the blocked
ones now fail visibly with a reason instead of being discovered at the end.

    tools/build.sh --dry-run                                  # every stage that needs no compiler
    SG1_DUMP=~/sg1/dump tools/build.sh                        # + the dump-side stages (target `full`)
    SG1_DUMP=~/sg1/dump tools/build.sh --target hybrid        # the packages a hybrid build carries + a manifest
    SG1_DUMP=~/sg1/dump UCC=~/tools/ucc/ucc tools/build.sh    # + compile (no such ucc exists: see below)

One directory per run under `build/rebuild/<UTC>/`, one log per stage, `summary.txt` at the top, nothing committed.

## Stage by stage

| # | stage | what it proves | today |
|---|---|---|---|
| 1 | `writer-relay` | every package re-lays through the writer's own placement path and comes back byte-identical (18 of 18) | **proven** |
| 2 | `rebuild-identity` | each package is assembled from header + tables + payloads and matches the original byte for byte (18 of 18, 100% of bytes accounted for, zero gaps) | **proven** |
| 3 | `source-tree` | the 2,319 classes' own source is written out as one `.uc` per class | **proven** |
| 4 | `merge-defaults` | each class's `defaultproperties` block is merged into that tree (1,664 of 2,319 classes) | **proven** |
| 5 | `compile` | a compiler turns that tree into `.u` payloads | **missing**: no `ucc` matching package version 130 / licensee 4 |
| 6 | `repack` | generated payloads are placed by the writer | **blocked** on 5 |
| 7 | `package-check` | a repacked package passes `Packages.md5` | **decided, not implemented** (`docs/PACKAGE_CHECK.md`: bypass, not regeneration — none of 168 stored digests matches any shipped file under ~500 hash schemes) |
| 8 | `boot` | the rebuilt image boots | **blocked** on 5-7; the harness exists (`tools/xemu/smoke.py`, `docs/XEMU.md`) |

So 6.1 is *half built*: the container is a non-risk (stages 1-2), the input tree is complete for two thirds of the
classes (stages 3-4), and everything from payload generation onward waits on a compiler that this project does not
have. That is the same conclusion `docs/PATH.md` reaches from the estimate side.

## The two targets (PLAN 6.4)

`--target hybrid|full` selects what the run is *for*; stages 1-4 are shared.

| target | definition | what it does today |
|---|---|---|
| `hybrid` | our scripts + the original assets | places **18 assembled packages** into `workdir/System` (the writer's own output, not a copy — `--rebuild --write`), copies `Packages.md5` beside them, writes `manifest.csv` + `missions.csv`, and checks the merged source tree is 2,319 files. `manifest.csv` must say `identical=1` for all 18 or the stage fails |
| `full` | our scripts + re-extracted assets | the four stages above plus `compile`/`repack`/`package-check`/`boot`, which report SKIP with their reason (no `ucc`; `docs/PACKAGE_CHECK.md` decides bypass-not-regeneration) |

Two things this deliberately does **not** claim:

* **`hybrid` is not a compiled build.** There is no `ucc`, so "our scripts" in a hybrid build is the merged source
  tree staged beside the packages, not `.u` payloads made from it. The `manifest.csv` row for each package proves
  the *placement* (the file the engine would load is the writer's assembly of the original payloads), not that a
  single line of our script went in.
* **no boot matrix yet.** PLAN 6.4 also asks which missions boot in each target. `missions.csv` carries the 18
  campaign rows the build ships (`inventory/campaign_matrix.csv`) so the eventual per-target boot column has
  something to attach to, and the `boot` stage stays SKIP until an HDD image can be built from these packages
  (`docs/XEMU.md`).

`make rebuild-hybrid` / `make rebuild-full` are the same two invocations. Verifying the hybrid target by hand:

    SG1_DUMP=~/sg1/dump tools/build.sh --target hybrid
    grep -c ',1$' build/rebuild/<UTC>/manifest.csv        # 18
    cmp build/rebuild/<UTC>/workdir/System/Engine.u ~/sg1/dump/*/System/Engine.u

## Why 6.3 is not claimed

PLAN 6.3 asks for deterministic output so two rebuilds diff cleanly. **Today that is trivially true and therefore
not evidence of anything**: every payload in stages 1-2 is *copied*, so two runs cannot differ. A real determinism
test needs generated payloads — the compiler's output, timestamps and name ordering included. Stage 2 proves the
write path is reproducible; it does not prove a *build* is. 6.3 stays open until stage 5 exists, and this document
says so rather than banking the pass.

## What the script does not do

* **It never writes into the repository.** Everything lands under `build/` (git-ignored) or `$WORK`.
* **It does not touch the dump.** Stages read the dump, never modify it; `docs/LEGAL.md` and `AGENTS.md` apply.
* **It is not a packaging tool.** `--dry-run` still runs stages 1-4, because they are cheap and they are the ones
  a change is likely to break; `UCC` is only consulted for stage 5.

## Reproducing each claim

```
make rebuild              # == tools/build.sh --dry-run with SG1_DUMP
make pkg-relay            # stage 1 alone
make pkg-rebuild          # stage 2 alone
make extract-script       # stage 3 alone
make defaults-tree        # stage 4 alone
```
