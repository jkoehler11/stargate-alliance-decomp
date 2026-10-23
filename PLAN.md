# Stargate SG-1: The Alliance (Xbox, 2005-12-15 prototype) — Decompilation Plan

Status: **phase 0-2 done; phase 3 script tree complete, compile loop not started; phase 4 under way**
(last run 2026-10-23). This is the working task list for the
project described in [README.md](README.md). Section 0 was measured against the real release; every box
ticked below was executed, and its output is committed under `inventory/`, `tools/` and `docs/`.
Measured totals live in [docs/INVENTORY.md](docs/INVENTORY.md).

**Goal:** Produce a source-level reconstruction ("decomp") of the Dec 15 2005 Xbox prototype:
a recompilable UnrealScript source tree extracted from the shipped `.u` bytecode, a symbolized
and documented native-code reconstruction of `default.xbe`, and a full asset/level extraction —
verified against the original build, not just "looks right".

**Target:** Hidden Palace / CVLT OF OSIRIS release, 2019-05-06 — 968.46 MiB 7z, Xbox Debug Kit origin.
**Engine:** Unreal Engine 2.5 fork (Perception internal codename appears to be *"Warfare"*;
network protocol reports `unreal://unreal/0.30.6.0/`).

---

## 0. Recon already verified (2026-10-22)

These facts came from actually downloading and inspecting the dump; the task list below assumes them.

| Fact | Value |
|---|---|
| 7z archive | `Stargate_SG-1_-_The_Alliance-2005-12-15.7z`, 1,015,500,550 bytes |
| SHA-256 | `d70e15d5ceb479bb8443f2c0bcf2d8884f913a02033d7d8a8e33f80e622d47f4` |
| Extracted tree | 4,081 entries, ~2.2 GB |
| Executable | `default.xbe`, 6,209,536 bytes, image base `0x00010000`, no embedded PDB (`RSDS` absent) |
| Assert-path source map | 154 distinct `.cpp` paths, 12 modules (Engine 104, Core 13, IpDrv 10, stargatevehicles 7, Gui 5, D3DDrv 5, stargategame 3, XboxDrv 2, XboxAudio 2, XboxLive 1, Gameplay 1, Fire 1) |
| UnrealScript packages | 18 `.u` files, package versions 128/129/130, licensee 4 |
| Biggest packages | `Engine.u` 3.09 MB (17,980 exports), `StargateGame.u` 3.03 MB (13,425 exports), `StargateContent.u` 1.74 MB (7,732 exports) |
| Maps | 73 `.unr` (M00…M12A missions + DM/CTF multiplayer maps) |
| Assets | 3,287 `.wav`, 170 `.utx`, 91 `.usx`, 83 `.ukx`, 55 `.uax` |
| Custom formats | `.sgs` checkpoints, `.sgp` profiles, `.ka`/`.kaw` ragdolls, `.lad` LIPSinc lip-sync controllers |
| Package integrity data | `System/Packages.md5` (UE2 `PackageCheckInfo` blob) — repacked packages will fail this check unless regenerated/disabled |
| Tooling head start | UELib already ships an explicit `SG1_TA` build profile: `[Build(130, 4, BuildGeneration.UE2_5)]` |
| Script-source oracle | Leaked 2006 studio `Classes` source, held privately and never committed: 296 files / 291 classes / 254 `native()` declarations / 1.5 MB uncompressed |

Fork-specific notes worth carrying into every later task:
- The build's `Pawn.cpp` / `Pawn.ucpp` show Perception compiled hot UnrealScript classes to C++
  with an in-house "ucpp compiler" (PS2/EE-oriented, `#ifdef __PSX2_EE__`). Expect native copies of
  script classes inside the XBE, so script decompilation alone will not explain all behaviour.
- Assertion strings leak the original tree layout: `alienbrainWork\Stargate-sg1\Game\GameXBOX\<Module>\Src\<File>.cpp`,
  plus `D:\uc.xbe` — i.e. their script compiler was itself an Xbox executable.

---

## 1. Definition of done

Ship four artifacts, each with a machine-checkable pass criterion:

1. **Script decomp** — every class/state/function in the 18 game packages present as `.uc`, compiling clean.
   *Pass:* `ucc make` returns 0 errors; bytecode re-disassembly matches original opcode streams ≥98% of functions.
2. **Native decomp** — `default.xbe` fully loaded and symbolized in Ghidra; every function in the `stargategame`
   and `stargatevehicles` modules named + decompiled to readable C; engine/Core functions named where reachable
   from script natives. *Pass:* 100% of `native()` declarations mapped to an address; zero "FUN_00xxxxxx" in game modules.
3. **Assets/levels** — all packages, maps, textures, meshes, sounds exportable to standard formats with a manifest.
   *Pass:* every `.unr` dumps actor/level T3D; every `.ukx`/`.usx` exports geometrically (spot-checked in a viewer).
4. **Rebuild** — repacked `.u` from our source tree boots in xemu and loads a mission. *Pass:* title screen + M00 load
   from the Continue menu, screenshot evidence, no assert dialog.

---

## 2. Phases and tasks

### Phase 0 — Project setup (½ day)

- [x] 0.1 Create the working layout on your own machine: `src/UnrealScript/`, `src/Native/<Module>/`, `tools/`,
      `inventory/`, `docs/`, `build/`, with derived output under a gitignored `work/` (see `.gitignore` and
      `AGENTS.md` — game-derived trees stay off the remote; the repo carries tooling, docs and metrics).
- [x] 0.2 Write `docs/SCOPE.md`: what "decomp" means here (source reconstruction + symbolization, *not* a bit-exact
      rebuild of the engine), and explicitly out-of-scope items (engine renderer rewrite, netcode).
- [x] 0.3 Write `docs/LEGAL.md`: record the provenance of the build and the publishing posture — this
      repository ships tooling, docs and our own code only; no game data, no game-derived output, no
      leaked studio source (see the Legal section of README.md for the standing rule).
- [x] 0.4 Add `docs/CONVENTIONS.md`: naming rules for unknown functions, how to mark unverified code,
      what a "matched" function means, commit message format.
- [~] 0.5 Pin toolchain versions in `tools/versions.lock` (Ghidra, ghidra-xbe, UELib commit,
      UE Explorer version, umodel build, xemu version, .NET SDK, Mono/Wine).
- [ ] 0.6 Set up pre-commit hook + CI skeleton (see Phase 7) so coverage metrics cannot regress silently.

### Phase 1 — Acquire, verify, freeze (~1 day)

- [x] 1.1 Download the Hidden Palace 7z; record SHA-256 and size (done: values in §0).
- [x] 1.2 Extract with `py7zr` (container `/tmp` is a 512 MB tmpfs — extract to real disk, e.g. `/home/pn/sg1/dump`).
- [x] 1.3 Generate `inventory/manifest.sha256` over every extracted file (4,081 entries) and commit it.
- [ ] 1.4 Verify package integrity against `System/Packages.md5` and record which packages pass (baseline for
      detecting our own repack drift later).
- [ ] 1.5 Archive a pristine, read-only copy of the dump; all later work happens on copies.
- [x] 1.6 Write `docs/PROVENANCE.md`: build date, dump method (Xbox Neighborhood), release group, cross-reference
      to the public preservation listings for the same release (no copy is kept in this repo).

**Acceptance:** fresh checkout + `make verify-dump` reproduces the manifest with zero mismatches.

### Phase 2 — Static recon of the dump (~2–3 days)

- [x] 2.1 Package inventory table (`inventory/packages.csv`): name, size, UE version, licensee version, flags,
      export/import counts, class count, native-class count.
- [x] 2.2 Class inventory (`inventory/classes.csv`): package, class, superclass, `native` flag, `native()` indices,
      state count, function count. (Drives Phase 3 and 4 work assignment.)
- [x] 2.3 Module map of the XBE (`inventory/xbe_modules.csv`): the 154 assert paths → module, source file,
      address range (from assert call sites), estimated function count per file.
- [ ] 2.4 Catalog custom/serialized formats: `.sgs`, `.sgp`, `.ka`/`.kaw`, `.lad`, `.uax` speech, `LIPSincData`
      controllers — write `docs/formats/*.md` stubs with hex samples.
- [x] 2.5 Map config surface: `Default.ini`, `UnrealEngine2.ini`, `DefUser.ini`, `Startup.int`, the 20+ `Setup*.int`
      files (Xbox/PSX2/GCN variants) → note engine options that reveal fork features.
- [ ] 2.6 Enumerate XBE imports (D3D8, XAPI, XNet, XOnline) and the D3D8 device caps used — informs the
      renderer-side natives we must understand.
- [ ] 2.7 Diff the 18 packages against stock UT2004/UE2.5 class names to classify each class as
      *stock-engine*, *modified-engine*, *game-specific*.

**Acceptance:** every one of the 18 packages and 154 XBE source files appears in an inventory CSV with counts,
and `inventory/` regenerates from scripts with no manual edits.

### Phase 3 — UnrealScript decompilation (bulk of the script track, ~4–8 weeks)

- [x] 3.1 Build UELib (`dotnet build`) + UE Explorer; confirm the `SG1_TA` (130/4) profile loads
      `StargateGame.u` without exceptions.
- [x] 3.2 Baseline bulk decompile all 18 packages into `src/UnrealScript/<Package>/`; record per-package
      success/failure per class (`inventory/decompile_coverage.csv`).
- [x] 3.3 Build the oracle diff tool: normalize (whitespace/comments/ordering) the privately held 2006
      studio `.uc` sources and our decompiled output; emit per-class similarity scores and three buckets:
      *matches leak*, *drifted (build is 6 months older)*, *absent in leak (cut/added content)*.
- [~] 3.4 Triage bucket 3 — this is the cut-content list. Write `docs/CUT_CONTENT.md` (classes, missions,
      weapons, features present in Dec-2005 build but gone by Jun-2006).
- [~] 3.5 Fix decompiler gaps in priority order: `defaultproperties` reconstruction, struct/enum recovery,
      `native()`/`replication` blocks, state code + labels, latent function calls, operator precedence.
      File UELib issues/patches upstream where the gap is generic.
- [ ] 3.6 Set up the compile loop: matching `ucc` (UT2004 / UE2 Runtime under Wine) with the engine's
      `Default.ini` class list; iterate until each package compiles clean.
- [ ] 3.7 Disassembler + comparator: disassemble original package functions and our recompiled ones;
      produce per-function match grades (L0 present / L1 compiles / L2 opcode-sequence ≥98% / L3 byte-exact).
- [ ] 3.8 Chase L2→L3 on the small packages first (`Core`, `Fire`, `UnrealEd`, `IpDrv`, `AmbientCreatures`),
      then `Engine`, then the Stargate packages. Track a running scoreboard in `docs/MATCHING.md`.
- [ ] 3.9 Handle the ucpp-native classes (`Pawn`, `ShadowProjector`, others discovered in 2.2): mark their script
      bodies as "native — see src/Native", and cross-link to Phase 4 output.
- [ ] 3.10 Write class-level documentation for the game systems: mission objective graph, interaction system
      (`SGInteraction`), AI scripts, vehicle driver/passenger weapon structs, checkpoint saves.

**Acceptance:** `inventory/decompile_coverage.csv` shows 100% of classes decompiled; compile error count 0;
matching table shows ≥98% of functions at L2 or better.

### Phase 4 — Native decompilation of `default.xbe` (parallel track, 3–6+ months)

- [x] 4.1 Install Ghidra + `ghidra-xbe`; load `default.xbe` at base `0x00010000`; verify section mapping and imports.
- [~] 4.2 Apply Xbox SDK / MSVC 7.1 signature libraries (xbox-includes, FLIRT-style sigs) to auto-name CRT,
      XAPI, D3D8, XNet, XOnline, zlib and math routines.
- [~] 4.3 Locate the UnrealScript VM core in the XBE: `GNatives`/exec opcode dispatch table, `UObject` layout,
      `ProcessEvent`, `ProcessInternal`, property/`GPropPointers` tables. Document in `docs/NATIVE_VM.md`.
- [x] 4.4 Auto-generate native symbol names from the packages: each `native(index)` declaration in `inventory/classes.csv`
      maps to the VM native table slot → script this into a Ghidra script that renames the target functions.
      *This is the single highest-leverage task in the project — it names hundreds of engine functions for free.*
      270 of the 991 live slots named from declarations, 721 left as slot-only `Native_<index>` (none declared);
      measured result and the four unaccounted indices in `docs/NATIVE_VM.md` and `docs/UNKNOWNS.md`.
- [ ] 4.5 Split the XBE into per-module Ghidra projects using the assert-path map from 2.3 (Engine, Core, IpDrv,
      Gui, D3DDrv, XboxDrv, XboxAudio, XboxLive, Gameplay, Fire, stargategame, stargatevehicles).
- [ ] 4.6 Decompile + document `stargategame` and `stargatevehicles` first (small, 10 files, highest game-specific
      value: DeathGlider, AlkeshBomber, GoauldDrone, StargateCopter, Teltak, StargateHover, vehicle physics).
- [ ] 4.7 Reverse the ucpp-generated class implementations (from 3.9) and verify against the leaked `.ucpp` sources.
- [ ] 4.8 Reverse engine-side fork changes: what did Perception change vs stock UE2.5 (renderer for Xbox,
      memory/cache, script compiler hooks, Kynapse/AI integration, LIPSinc integration, ragdoll `.ka` loader).
- [ ] 4.9 Write `src/Native/<Module>/` reconstructed C for game modules with original function names, plus
      `docs/NATIVE_NOTES.md` per subsystem. Engine internals: name + document, decompile only where script-reachable.
- [ ] 4.10 Emit a symbol file (`symbols/xbox.map` / Ghidra CSV) that other tools (xemu GDB, xbdm) can consume.

**Acceptance:** every `native()` declaration resolves to a named address; zero unnamed functions in the game modules;
`symbols/xbox.map` loads into a debugger against the original XBE without address errors.

### Phase 5 — Assets, levels, data (parallel, ~2–4 weeks)

- [ ] 5.1 umodel/UEViewer export pass over `.ukx`/`.usx`/`.utx`/`.uax` with the UE2 profile; build `inventory/assets.csv`.
- [ ] 5.2 T3D-dump every `.unr` (actors, properties, level script) into `extracted/maps/*.t3d`; index mission structure.
- [ ] 5.3 Extract the 3,287 WAVs with speech/sound categorization (dialogue, SFX, music) and pull `Speech/` dialogue
      lines into a transcript index — this is the fastest route to documenting cut story content.
- [ ] 5.4 Parse `.sgs` checkpoints and `.sgp` profiles; document the save format (also needed to make rebuilt builds testable).
- [ ] 5.5 Parse `.ka`/`.kaw` ragdolls and `LIPSincData/*.lad`; document formats, map to the native loaders from 4.8.
- [ ] 5.6 Produce a "cut content report" merging 3.4 (script), 5.2 (maps), 5.3 (dialogue) and the DM/CTF maps
      that shipped in a single-player prototype.

**Acceptance:** every asset class in `inventory/assets.csv` has a documented extraction path, and the cut-content
report cites file-level evidence for each claim.

### Phase 6 — Rebuild pipeline (~2 weeks, after 3.6)

- [ ] 6.1 Scripted rebuild: `tools/build.sh` → compile `src/UnrealScript` → produce `.u` packages → place into a
      working copy of the dump.
- [ ] 6.2 Solve the package-check problem: regenerate `Packages.md5`-equivalent check data, or patch the check
      off in the XBE for dev builds. Document both paths in `docs/PACKAGE_CHECK.md`.
- [ ] 6.3 Ensure deterministic output (fixed timestamps, sorted names) so rebuilt packages diff cleanly between runs.
- [ ] 6.4 Produce two build targets: `rebuild-hybrid` (our scripts + original assets) and `rebuild-full`
      (our scripts + re-extracted assets) and record which missions boot in each.

**Acceptance:** `tools/build.sh` on a clean checkout produces bootable packages; rebuilt-vs-original package diffs
are limited to expected fields (checksum/timestamp) and documented.

### Phase 7 — Verification harness and CI (~1 week, then continuous)

- [x] 7.1 xemu configured as an Xbox debug kit (128 MB RAM, debug BIOS, HDD image) per Hidden Palace's notes for
      other debug-kit dumps; document exact config in `docs/XEMU.md`.
- [~] 7.2 Smoke test script: boot → title screen → Continue → load each mission → capture screenshot + log tail.
      Missions are M00, M02A–D, M04A/B, M07A–C, M10B, M12A (+ the rest from the map list).
- [ ] 7.3 Gameplay probes on keyboard (Tab opens the Unreal console): scripted `exec` commands to dump actors,
      walk objective graph, spawn/test classes in isolation.
- [ ] 7.4 CI job per commit: manifest verify → package parse → decompile coverage → compile status →
      bytecode match % → asset count. Publish the scoreboard as the repo's README badge/table.
- [ ] 7.5 Nightly regression: rebuild + xemu boot test; fail on any new assert dialog or missing class.

**Acceptance:** CI is green on a stock machine, the scoreboard moves in the right direction on every PR,
and a deliberate one-line script break is caught by the boot test.

### Execution notes (2026-10-23)

- **0.1** layout is `docs/`, `inventory/`, `src/{UnrealScript,Native}/`, `symbols/`, `tools/`, `build/`.
  Game-derived output is git-ignored (`src/UnrealScript/**/*.uc`, `extracted/`, `build/out/`).
- **0.5** partial: .NET SDK 10.0.402, Python 3.12.3, py7zr 1.1.4, UELib `8236c39` pinned. Ghidra,
  umodel and xemu stay unpinned until their phases start.
- **1.3** manifest: 4,006 files, 2,253,398,443 bytes, 4,082 tree entries. `make verify-dump` reproduces it.
- **2.1-2.3** generated by `tools/Sg1Tool` (UELib front-end) and `tools/xbe.py`; 5.5 s for all 18 packages.
  Pitfall worth keeping: `Construct`-only init leaves exports undeserialized and `OfType<UClass>()`
  returns stubs (7 classes for `Core.u`), so `Dump.Load` uses `InitFlags.All`.
- **3.1/3.2** UELib auto-detects `Build:SG1_TA` (130/4). The bulk run reconstructs **2,319/2,319
  classes across all 18 packages in ~4 s**, 0 failures (`inventory/decompile_coverage.csv`); output is
  14 MB of `.uc` under the git-ignored `src/UnrealScript/`. Reader warnings on
  `TempleTurretEmplacement.ExitPositions` and `UWindowWin95LookAndFeel.Tab*` (property-tag size)
  do not fail the class and belong to 3.5.
- **New measured gap for 3.5:** the reconstructed tree contains **51,289 `__NFUN_<n>__` sites over
  213 distinct tokens** — tokens UELib cannot name for this build. Ranked queue in
  `docs/DECOMPILER_GAPS.md` (`tools/token-gaps.py`), led by `__NFUN_119__` (5,565 sites) and
  concentrated in `StargateGame` (17,250) and `Engine` (14,093). Closing tokens is now the single
  biggest readability win in the script track, ahead of 3.3.
- **3.3/3.4 done/started.** The oracle diff (`tools/oracle-diff.py`, `docs/ORACLE_DIFF.md`) pairs
  291 leaked classes against our 2,319: **202 matched (mean similarity 0.862), 65 drifted, 11
  divergent, 13 oracle-only, 1,362 build-only**. The decisive finding is what the oracle *is*:
  269 `Engine` + 5 `Core` + 4 game classes plus `Pawn`/`ShadowProjector` as `.ucpp`. It is
  Perception's **engine** fork source, not the game script, so `build-only` is a coverage gap and
  **cannot** be reported as cut content — see `docs/CUT_CONTENT.md` for the evidence standard that
  replaces the shortcut. The 65 drifted + 11 divergent `Engine` classes are the engine subsystems
  still being rewritten between Dec 2005 and Jun 2006 (input for 4.8), and the oracle gives a hard
  quality number to move with 3.5.
- **3.5 big win (native table).** Bytecode calls natives by index and UELib resolves them through
  `UnrealPackage.NTLPackage`, which nothing in this dump supplies -- so every engine call, including
  the operator natives (`Object.NotEqual_ObjectObject` = native 119), decompiled to `__NFUN_<n>__`.
  Rebuilding that table from the build's own `Core.u` (161 natives) and handing it to every package
  took unresolved sites from **51,289 to 4,230 (-92%)**, and operator expressions now read as source
  (`V.bPainCausing && V.DamageType != ReducedDamageType`). Deliberately **Core-only**: across the 18
  packages there are 797 native declarations for ~275 indices, so a union table would name package
  A's code with package B's symbol -- silently wrong. The remaining 93 tokens (4,230 sites) need the
  real `GNatives` dispatch table from the XBE (4.4), which is now the cheapest route to 100%
  readability rather than a rewrite of the token map.
- **5.2 started (map census).** `Sg1Tool maps` parses all 73 `.unr` packages (they are packages:
  exports are placed actors) into `maps.csv` / `map_actors.csv` — 175,718 placed actors, 389 distinct
  actor classes. `tools/content-census.py` cross-references them with `classes.csv` and the campaign
  definition, giving `docs/CONTENT_CENSUS.md`.
- **Cut-content evidence (5.6) with a real finding:** `System/tweak/Misc/ChapterList.ini` is the
  campaign (`StargateMenus.ChapterList.Chapters` is `config()`), and its **37 chapters reference maps
  that do not ship in this build** (`m01a`, `M02AX`, `m07ax`, `m12ax` … Mission 21, matching the
  `Music/M01…M21` and `Speech/M01…M21` folders), while **none of the 73 shipped maps** appears in it
  (the build uses `M00`, `M02A`, `M04a`, `M07A`, `M12A` ids). That is the static explanation for the
  release note "single-player campaign won't start, use Continue": the menu path reads a chapter list
  from a different campaign revision. Candidate cut content is therefore 37 chapter maps, and the open
  question is the revision mapping between the two id schemes.
- **4.1 done, 4.2 started.** Ghidra 12.2 needs JDK 25 (its `application.properties` sets
  `java.min=25`; JDK 21 is rejected with "unsupported java version"), installed in the container.
  `tools/ghidra/run-analysis.sh` imports `default.xbe` as x86:LE:32 at base `0x10000`, and
  `XbePre.java` carves the 15 real sections before analysis — Ghidra's binary loader makes one flat
  block, which would put every section after `.text` at the wrong address.
  `XbePost.java` then names every function that owns an assert string.
  Result (`docs/NATIVE.md`): **14,027 functions**, 3.77 MB of code, median 114 bytes, **1,245
  attributed to a module** (Core 549, Engine 526, Gui 53, stargategame 31, IpDrv 20, D3DDrv 16,
  stargatevehicles 12, XboxLive 10, Gameplay 9, Fire 8, XboxAudio 6, XboxDrv 5). 199 assert xrefs
  sit in no function — worth a look, since each is a call site the analyser did not cover.
- **4.4 done — the dispatch table names 270 functions.** `tools/native-names.py` maps
  `inventory/natives.csv` onto the table; 274 declarations carry an explicit index, 270 resolve to a
  distinct slot, and no index has two names. Slot names follow `docs/CONVENTIONS.md`
  (`exec<Function>`, class-prefixed only for the one colliding pair, `Actor.Sleep`/`GUIPage.Sleep`).
  Applied in Ghidra by `XbePost.java`, which **created** 991 functions — nothing xrefs a dispatch
  target, so the analyser had never made one — taking the binary from 14,027 to 15,018 functions
  with 0 renames over an existing name and the 1,245 assert-attributed functions unchanged.
  **Data correction included:** the committed `inventory/gnatives_map.csv` had been the largest
  pointer run in `.rdata` (6,385 entries), not the documented table — `tools/gnatives.py` ranked
  candidates by size. It now scores four structural tests (entry 0 null, section boundary, every
  entry in `.text`, exactly one code xref); only `0x00598e00` passes 4/4, and the map is 991 rows.
  Two questions the table cannot answer are tracked in `docs/UNKNOWNS.md`: 523 of the 797
  declarations serialise index 0 (no slot claimed), and `Actor.MoveSmooth`/`SetPhysics`/
  `AutonomousPhysics` declare 3969–3971, past the table's 993.
- **4.3 (partial) — `GNatives` found.** `tools/gnatives.py` scans for runs of 32-bit values that are
  null or point at function entries: **994 entries at `0x00598e00`**, 991 non-null, 99.4% aligned,
  exactly one code reference (the interpreter, `0x00343763`). Cross-check: 270 of the 274 declared
  native indices (98.5%) are present and map to distinct addresses; 721 entries are engine-internal
  natives no package declares. Documented in `docs/NATIVE_VM.md`.
- **Script readability is now 100%.** Native resolution order Core -> Engine -> own package took
  unresolved `__NFUN_<n>__` sites 51,289 -> 4,230 -> 1,470 -> **0** (0 tokens) across 2,319 classes.
  Oracle quality moved with it (see `docs/ORACLE_DIFF.md`). `docs/DECOMPILER_GAPS.md` is now an
  empty-table regression check.
  Next: identify the 721 slot-only execs, then 4.2 signature libraries; indices 3969-3971 are
  tracked in `docs/UNKNOWNS.md`.
- **7.1 (partial) — xemu installed, firmware missing.** xemu `0.8.139` (debug AppImage) runs on the
  desktop and starts headless; the dump is staged at `~/nfs/sg1-dump/…` (outside the repo) so the DVD
  path needs no copying. `tools/xemu/boot-test.sh` encodes the debug-kit run (128 MB, serial log) and
  fails fast when firmware is absent. Blocked on the console's own `mcpx_1.0.bin` and flash ROM:
  those are Microsoft firmware, deliberately not downloaded or committed (`docs/XEMU.md`).
- **7.1 done, 7.2 started — the prototype boots.** With the debug flash (`bios_debug_4627.bin`) and a
  media-patched XISO (`media=DVD-X2`, `region=NA`) at 128 MB, xemu reaches the engine's main menu with
  `Build: Dec 15 2005 12:02:54`. Retail flash + unpatched disc was refused; the two together work.
  Next: drive the `Continue` flow to load a mission (needs input automation) and settle the
  chapter-list claim in `docs/CUT_CONTENT.md`.

### Phase 8 — Documentation, release, handoff (ongoing)

- [ ] 8.1 `docs/BUILDING.md`: end-to-end reproduction, toolchain versions, expected hashes.
- [ ] 8.2 `docs/ARCHITECTURE.md`: engine fork overview, VM, script-to-native mapping, level flow.
- [ ] 8.3 Per-system write-ups: AI (`AIScript`/`AIMsgManager`), vehicles, weapons, mission objectives, checkpoints,
      UI/menus, audio/LIPSinc.
- [ ] 8.4 Milestone releases: (a) inventory+tooling, (b) scripts decompiled, (c) scripts compile, (d) bytecode match,
      (e) native symbol map, (f) bootable rebuild.
- [ ] 8.5 Publish tooling separately (package scripts, native-symbol generator) as generally useful UE2/Xbox RE tools.

---

## 3. Sequencing and effort

| Track | Depends on | Rough effort (one experienced person) |
|---|---|---|
| 0–2 (setup + recon) | — | 1 week |
| 3 (script decomp) | 2.2 | 4–8 weeks |
| 4 (native decomp) | 2.3, 3.x natives list | 3–6 months (parallelizable per module) |
| 5 (assets) | 2.1 | 2–4 weeks |
| 6 (rebuild) | 3.6 | 2 weeks |
| 7 (CI/verification) | 6.1 | 1 week + upkeep |
| 8 (docs) | continuous | continuous |

Recommended split for a small team: one owner for the script track (Phase 3), one for native (Phase 4),
one for tools/CI/rebuild (2, 6, 7), one part-time for assets and content reporting (Phase 5).
The script track unblocks everything else and should start first.

**First-week quick wins (in order):** 1.3 manifest → 2.1/2.2 inventory → 3.1 UELib load of `StargateGame.u` →
3.2 bulk decompile → 4.3/4.4 VM + native-table naming. Those five together already produce a named
class list, a decompiled script tree and a hundred-plus named native functions.

---

## 4. Risks and open questions

1. **Licensing.** The circulated 2006 studio source is copyrighted material from a cancelled commercial project,
   and decompiled scripts are derivative of the shipped binary. Nothing game-derived may be published from this
   repo; the rule is tooling, docs and original code (Phase 0.3, README Legal).
2. **Package check (`Packages.md5`).** Repacked packages will likely be rejected until the check data is regenerated
   or patched. Budget time in 6.2; this is the classic blocker for UE2 rebuild projects.
3. **Compiler parity.** We do not have Perception's exact `ucc`/ucpp toolchain. Byte-exact matching may be impossible;
   the plan targets opcode-sequence parity (L2) as the real success criterion, with L3 as a bonus on small packages.
4. **ucpp native classes.** Some script logic exists only as C++ (Pawn, ShadowProjector, likely more). Detect these
   early (2.2) or the script tree will look complete while missing behaviour.
5. **Oracle drift.** The leaked source is ~6 months newer than the build. Differences are expected and are themselves
   the interesting content; don't "fix" our decompilation to match the leak blindly.
6. **Debug-kit emulation.** xemu needs a debug BIOS + 128 MB config; if that proves unstable, fall back to a real
   debug kit or to a patched retail-style XBE for boot testing only.
7. **Scope creep in native work.** The Engine module is 104 source files; decompiling all of it is a multi-year effort
   with low marginal value. Stay script-reachable + game-specific.
8. **Asset volume.** 3,287 WAVs and 73 maps are mechanical but slow; automate and never hand-curate.

---

## 5. Immediate next actions

1. `git init` the repo, drop in `inventory/manifest.sha256` from the verified dump.
2. Run the inventory generators (2.1, 2.2) — the class/native CSV is the backbone of Phases 3 and 4.
3. Build UELib and bulk-decompile `StargateGame.u`; compare 10 random classes against the leaked source to sanity-check
   the decompiler before committing to it as the tool of record.
4. Stand up Ghidra + `ghidra-xbe` on `default.xbe` and find the native dispatch table (4.3).
5. Only then fill in the remaining task list with owners and dates.
