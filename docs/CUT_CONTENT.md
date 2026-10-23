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

## Already visible in the inventory (leads, not claims)

- **Multiplayer in a single-player prototype.** 9 DM/CTF maps ship alongside the mission maps
  (`DM-Ha'tak`, `DM-KelownanTrain`, `CTF-Abydos`, `CTF-LostCity`, …) and `StargateGame.u` carries
  `StargateDeathMatch`, `TeamDeathMatch`, `StargateScoreBoard`, `TeamScoreBoard`. Whether MP was
  playable in this build or only present is a runtime question for Phase 7.
- **Maps with no single-player mission id.** The 73 `.unr` files include more levels than the
  mission flow names (`M00`, `M02A-D`, `M04A/B`, `M07A-C`, `M10B`, `M12A`, …). Enumerating which
  maps the chapter list actually references is task 5.2 and will settle this.
- **Post-build additions.** 13 classes exist in the Jun 2006 source and not in the build
  (`SgPhysActor`, `SgPhysCarParams`, `SgPhysPawn`, `SgPhysProp`, `SgPhysVehicleParams`, `GSInterface`,
  `MovieMaterial`, `BspMeshActor`, `CameraOverlayRot`, `BaseProfileManager`, …) — the SgPhys* family
  is a physics system the prototype does not have, which is work-in-progress rather than cut content.

## Next

1. Dump every `.unr` to T3D (5.2) and cross-reference actor classes against `classes.csv` — that
   turns "this class exists" into "this class is used, here".
2. Index `Speech/` (3,287 WAVs, 21 dialogue folders) and map lines to missions (5.3).
3. Only then write claims, each with the file and the count.
