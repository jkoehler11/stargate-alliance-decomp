# Cut content: what counts as evidence

Status: **open** (PLAN 3.4 / 5.6). This file defines the evidence standard and records what is
currently proven — which, deliberately, is nothing yet.

## Why this is not a list of missing classes

The obvious shortcut is to diff the Dec 2005 packages against the Jun 2006 studio source and call
every class the leak lacks "cut". That inference is wrong, and `docs/ORACLE_DIFF.md` shows why: the
leaked tree is Perception's modified **Engine** source (269 `Engine` classes, 5 `Core`, 4 game
classes) plus `Pawn`/`ShadowProjector` as `.ucpp`, not a dump of the game's script packages. So the
1,362 `build-only` classes are mostly classes the oracle never covered, and treating them as cut
content would manufacture a finding out of a coverage gap.

## What does count

A cut-content claim needs evidence that ties a feature to the build **and** to its disappearance:

| Evidence | Source | Strength |
|---|---|---|
| A class exists in the build but its behaviour is unreachable (no map actor, no reference) | `classes.csv`, map T3D dumps (5.2) | strong |
| A mission or area exists in `.unr` but has no entry in the menu flow / chapter list | map dump + `StargateMenus` script | strong |
| Recorded dialogue for a scene that no shipped map triggers | `Speech/` WAV index (5.3) | strong |
| A system present in the prototype but rewritten or removed in the later source | `docs/ORACLE_DIFF.md` drifted list | medium — shows direction of work, not shipping intent |
| A class referenced by shipped content but not compiled into any package | import tables | medium |

Everything below must cite file-level evidence, as in `PLAN.md` 5.6: a claim without a file and a
count does not go in the report.

## Evidence collected so far

### 1. The campaign menu is pointed at the wrong map filenames

`System/tweak/Misc/ChapterList.ini` is the campaign definition (`StargateMenus.ChapterList.Chapters`
is `config(...)`), and its 37 chapters name maps as `m02ax`, `m07axx`, `m17xa`, `m19x`, … while the
dump ships `M02A`, `M07A`, `M17A`, `M19A`, …. **Zero** chapters match a shipped filename literally;
30 match once the `x` build marker is removed. A literal lookup by the menu therefore fails for
content that is present, which is exactly what the release notes describe: the single-player
campaign cannot be started from the Single Player menu, and missions have to be loaded from
**Continue**.

*Correction to the first draft of this file:* an earlier version of the census compared the two sets
literally and reported 37 absent maps / "disjoint campaign". That was a normalisation bug, not a
finding — the two id schemes differ by the build marker, not by content. The numbers above are the
corrected ones and `tools/content-census.py` now strips the marker before matching.

### 2. Seven chapters have no shipped counterpart

| # | chapter | map file |
|---|---|---|
| 1 | Mission 1 Replicator Attack | `m01a.unr` |
| 5 | Mission 5 Reetou Infiltration A | `M05A.unr` |
| 6 | Mission 5 Reetou Infiltration B | `M05B.unr` |
| 34 | MP SGCTF Haaken Vs Tartarus | `SGCTF-HaakenVsTartarus.unr` |
| 35 | MP SGCTF Kelowna Vs Revanna | `SGCTF-KelownaVsRevanna.unr` |
| 36 | MP SL Testmap | `SL-SystemLordTestMapx.unr` |
| 37 | MP CTF Aztec | `CTF-Aztec.unr` |

Missions 1 and 5 have no map under their own id (M00 exists and may be Mission 1 under an older
name — runtime evidence needed), and four multiplayer/test maps were planned but not shipped. The
three`SGCTF-*`/`SL-*`/`CTF-Aztec` entries are the strongest candidates for genuinely cut content,
because the build ships a different multiplayer map set instead (`SGCTF-AbydosVsSGC`, `CTF-Abydos`,
`CTF-LostCity`, 7 `DM-*`).

### 3. Shipped content the campaign never references

49 of the 73 maps appear in no chapter, including `M00` (the map the release notes tell players to
start from), 7 `DM-*` maps, 2 `CTF-*` maps, `SGCTF-AbydosVsSGC`, `Entry` and `MainMenu`. Except for
the menu maps, these are content reachable only from **Continue** or by direct load — consistent
with a prototype whose campaign glue was mid-rewrite.

### 4. Dialogue was recorded for the campaign that ships

`docs/DIALOGUE.md` indexes 1,364 speech WAVs across 14 recorded mission ids (m02…m21) and 29
speakers. Every recorded mission id has a map, so the recording matches the shipped map set rather
than the chapter list — further evidence that the maps are the real content and the chapter list is
the stale artefact.

### 5. Post-build additions (not cut content)

13 classes exist in the Jun 2006 source and not in the build — the `SgPhys*` family
(`SgPhysActor`, `SgPhysCarParams`, `SgPhysPawn`, `SgPhysProp`, `SgPhysVehicleParams`), `GSInterface`,
`MovieMaterial`, `BspMeshActor`, `CameraOverlayRot`, `BaseProfileManager`, among others. These are
work started after the prototype, not content removed from it.

## Next

1. Dump every `.unr` to T3D so actor instances, not just class counts, can be cited (5.2 follow-up).
2. Map dialogue scenes to map scenes to prove which recorded lines were never reachable (5.3
   follow-up) — the index exists, the scene-to-quest mapping does not.
3. Runtime checks (Phase 7): does the chapter list fail at load time as predicted, and can `M00`
   stand in for Mission 1?
4. Every claim above is already traceable to a committed CSV or a committed doc; keep it that way.
