# Legal posture

Read this before committing anything. It is the rule the rest of the project is built around, and
the only document here that is not open to engineering trade-offs.

## What this repository is

An independent preservation-research project. It is not affiliated with, authorised by or endorsed by
MGM, Perception Studios, JoWood, Sony, Microsoft or Epic Games. *Stargate SG-1*, *The Alliance*, the
Unreal Engine and the Xbox marks belong to their owners; this project cannot grant rights it does
not hold.

## What may be committed

| Allowed | Why |
|---|---|
| Original tooling (Python/C#/shell) written by contributors | ours, GPL-3.0 |
| Documentation, plans, format descriptions, methodology | ours |
| Structural metadata: file names, sizes, SHA-256 hashes, export/import counts, class names, `native()` indices, addresses, symbol maps, function sizes | describes the binary without reproducing protected expression |
| Comparison **scores and counts** against the private oracle (e.g. "class X: 0.86 similarity") | a measurement, not source |

Never, from the oracle: file names of the private tree beyond the class name already in the build,
function bodies, comments, or lists of its internal symbols. `inventory/oracle_diff.csv` carries
counts on that side, not names.
| Empty directory skeletons and manifests that reference no content | tooling scaffolding |

## What must never be committed

- Any part of the game: `.xbe`, `.u`, `.unr`, `.utx`, `.usx`, `.ukx`, `.uax`, `.wav`, `.sgs`, `.sgp`,
  `.ka`/`.kaw`, `.lad`, `splash.raw`, `xbox.bin`, `Packages.md5`, or any slice/hex dump of them.
- **Decompiled UnrealScript output.** Decompiled source is a derivative work of the shipped bytecode.
  It is generated into `src/UnrealScript/` locally and is git-ignored by design.
- Extracted or rebuilt assets and packages (`extracted/`, `build/out/`).
- The 2006 Perception studio `Classes` source that circulates online. It is unreleased copyrighted
  work; it stays outside the repo and is used only as a private diff oracle (`PLAN.md` 3.3).
- Emulator BIOS files, Xbox SDK libraries, leaked signing keys, or any other proprietary binary.

`.gitignore` enforces the mechanical part of this. It is a backstop, not a licence to be careless:
if a file would not survive a takedown request, it does not get committed.

## Publishing posture

- The repository publishes **tooling, methodology and structure**, never content derived from the game.
- Findings about the game (cut content, systems, mission flow) are published as prose with file-level
  citations ("`Maps/M07A_*.unr` contains N actors of class X"), not as extracted content.
- If a rights holder objects, the response is to comply. No claim of fair use is made here.

## Contributor rules

1. Bring your own dump. Do not upload, link or attach it anywhere in this repo, its issues or PRs.
2. Never paste decompiled source into an issue, PR, commit message or test fixture. Cite class names,
   line counts and hashes instead.
3. Never paste the 2006 studio source, in any form, anywhere.
4. Keep tool output that contains game content out of the repo; commit the *tool* that produces it.

## Why this is written down on day one

Every UE1/UE2 decompilation project that has survived has kept this line, and every one that died
crossed it: publishing decompiled script or extracted assets converts a research project into a
redistribution of someone's cancelled commercial product, and invites the takedown that ends it.
The scope in `docs/SCOPE.md` is deliberately built so that the interesting, publishable output —
symbol maps, format documentation, cut-content analysis, tooling — is reachable without ever
committing a byte of the game.
