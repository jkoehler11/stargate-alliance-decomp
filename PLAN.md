# Stargate SG-1: The Alliance (Xbox, 2005-12-15 prototype) — Decompilation Plan

Status: **phase 0-2 done; phase 3 script tree complete, compile loop not started; phase 4 under way,
4.4 re-done against the runtime dispatch table after the earlier correction, 4.3 executor, binder,
expression decoder and `ProcessEvent` measured; 4.3's property type space, class roster and field
layout measured, the tagged tag record open)**
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
- [~] 0.6 Set up pre-commit hook + CI skeleton (see Phase 7) so coverage metrics cannot regress silently.
      The CI skeleton is in: `.github/workflows/ci.yml` runs `make ci` on every push and pull request, and
      that target checks what needs no game data (committed CSV shapes, the function and symbol maps, and
      that `symbols/xbox.map`, `docs/NATIVE.md` and `inventory/xbe_functions_by_module.csv` reproduce from
      the committed inputs). No pre-commit hook: it would run the same one command, and a hook each
      contributor can skip is not the gate CI is.

### Phase 1 — Acquire, verify, freeze (~1 day)

- [x] 1.1 Download the Hidden Palace 7z; record SHA-256 and size (done: values in §0).
- [x] 1.2 Extract with `py7zr` (container `/tmp` is a 512 MB tmpfs — extract to real disk, e.g. `/home/pn/sg1/dump`).
- [x] 1.3 Generate `inventory/manifest.sha256` over every extracted file (4,081 entries) and commit it.
- [x] 1.4 Verify package integrity against `System/Packages.md5` and record which packages pass (baseline for
      6.2's package-check problem). Done as a measurement with a negative result: the file is itself a UE2 package
      (tag `0x9E2A83C1`, version 130) holding **168** `PackageCheckInfo` exports, not a text MD5 list, and none of
      the 18 shipped `.u` files' digests appears in it under four candidate hash schemes. Recorded in
      `inventory/package_check.csv` with `recovered_from=absent` and in `docs/PACKAGE_CHECK.md`, which states what
      6.2 now has to reproduce instead of assuming `md5(file)` is the check.
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
- [x] 2.4 Catalog custom/serialized formats: `.sgs`, `.sgp`, `.ka`/`.kaw`, `.lad`, `.uax` speech, `LIPSincData`
      controllers — write `docs/formats/*.md` stubs with hex samples.
      `inventory/formats.csv` (12 formats, container-detected) + four docs; `.lad` payload tracked as U4.
- [x] 2.5 Map config surface: `Default.ini`, `UnrealEngine2.ini`, `DefUser.ini`, `Startup.int`, the 20+ `Setup*.int`
      files (Xbox/PSX2/GCN variants) → note engine options that reveal fork features.
- [~] 2.6 Enumerate XBE imports (D3D8, XAPI, XNet, XOnline) and the D3D8 device caps used — informs the
      renderer-side natives we must understand.
      **Imports done, and the answer is that there are none to enumerate**: the header's import address is
      `0` and the image links statically, recording its **13 libraries** (all 1.0.5849) in one table at
      `0x10738` (`inventory/xbe_libraries.csv`, `tools/xbe-libraries.py`). The libraries' code is visible
      as whole sections — `XONLINE` 704 functions, `XGRPH` 642, `DSOUND` 427, `XNET` 413, `D3DX` 379,
      `D3D` 302, `XPP` 242 — **3,109 functions and 565 KB outside `.text` of which 22 are named**, which
      is the measurable size of the 4.2 signature gap. **Deferred:** the D3D8 device caps, which need a
      runtime probe (U11). `docs/IMPORTS.md`.
- [~] 2.7 Diff the 18 packages against stock UT2004/UE2.5 class names to classify each class as
      build-only / modified / stock.
      **Blocked differently now**: no stock UE2.5 package set is on hand, and the shipped `ScriptText`
      (`docs/PACKAGE_SCRIPT_TEXT.md`) makes part of the question moot — a class's text can be compared against stock
      *source* for whichever classes UT2004 sources cover, which is a stronger test than class names. Needs either a
      stock package set or the stock script sources; neither is in this repository or the dump.
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
      **`defaultproperties` reconstruction and enum recovery are done** (`docs/CLASS_DEFAULTS.md`): the locator
      finds a block for 2,209 of 2,319 classes and one that accounts for itself for 1,666 (71.8%) — the previous
      512-byte scan window excluded every base class, whose record is longer than that. 15,491 of 15,530 class-level
      properties render to UnrealScript (99.7%) — 468 arrays (1,435 elements), 207 nested structs — and 1,317
      `Begin Object` subobjects carry another 15,010 of 15,810; enum element names resolve for 771 of 1,052 byte
      values from the shipped `enum` declarations, and `make defaults-tree` merges the blocks into the extracted
      source so one tree carries both halves. Remaining here: 18 arrays and 12 structs whose element type the tiling
      test refuses, 543 withheld blocks (see the doc's "Not measured"), then `replication` blocks, state code and
      labels, latent calls, operator precedence.
- [~] 3.6 Set up the compile loop: matching `ucc` (UT2004 / UE2 Runtime under Wine) with the engine's
      flags, so recompiled script can be compared byte-for-byte.
      **Input problem solved, tool still missing**: the packages ship their own source, and `make extract-script`
      writes all 2,319 classes to `$WORK/source-tree` (6,113,909 bytes, `inventory/source_tree_summary.csv`,
      `docs/PACKAGE_SCRIPT_TEXT.md`). **The pipeline is scripted** (`tools/build.sh`, `docs/REBUILD.md`, `make rebuild`):
      eight stages, four proven today (writer relay 18/18, rebuild identity 18/18, source tree, defaults merged into
      it) and four SKIP with their reason (compile, repack, package check, boot). But **only 15 classes carry
      `defaultproperties`** — the defaults live in the
      package's default-properties data, not in the text — so the loop needs the text *and* a defaults reader, and
      `ucc` under Wine is still absent. Function counts match `inventory/classes.csv` for 1,789 of 2,319 classes;
      the disagreements are recorded, not explained. **The defaults half of the input now exists**: the packages
      yield 15,491 rendered properties over 1,666 classes and `make defaults-tree` merges them into the extracted
      tree, so the compile loop is blocked on `ucc` alone, not on its inputs.
- [ ] 3.7 Disassembler + comparator: disassemble original package functions and our recompiled ones;
      produce per-function match grades (L0 present / L1 compiles / L2 opcode-sequence ≥98% / L3 byte-exact).
- [ ] 3.8 Chase L2→L3 on the small packages first (`Core`, `Fire`, `UnrealEd`, `IpDrv`, `AmbientCreatures`),
      then `Engine`, then the Stargate packages. Track a running scoreboard in `docs/MATCHING.md`.
- [~] 3.9 Handle the ucpp-native classes (`Pawn`, `ShadowProjector`, others discovered in 2.2): mark their script
      bodies as "native — see src/Native", and cross-link to Phase 4 output.
      **The declaration side is reconciled** (`docs/NATIVE_CLASSES.md`): 96 classes, 681 `native function`
      declarations in the shipped text (256 carrying an explicit index) and 92 operator declarations, against 685
      function records in `inventory/natives.csv` — **672 matched (98.1%) and 0 index disagreements**, so every
      `native(N)` agrees with its record. 22 records over 14 classes still disagree (`PlayerController` 4,
      `Core.Object` 2, four others 2 each); which side is right needs the C++ side. Marking the bodies and the
      `src/Native` cross-links remain.
- [~] 3.10 Write class-level documentation for the game systems: mission objective graph, interaction system
      (`SGInteraction`), AI scripts, vehicle driver/passenger weapon structs, checkpoint saves.
      Objectives done — `docs/systems/mission-objectives.md`: the model, the one-event state machine
      (activate, then complete), child aggregation, the panel, placement (61 of 73 maps, exactly one per
      map), and that no checkpoint schema carries objective state.
      Interactions done — `docs/systems/interactions.md`: `SGInteraction` as an inline value object on
      `Actor.InteractionData` (621 of them across 61 maps), the prompt/use path through `StargatePlayer`
      and `CommandInterface`, and the separate Epic `Interaction`/`InteractionMaster` client framework.
      The other three systems (AI scripts, vehicle weapon structs, checkpoint saves) are open.
- [x] 4.1 Install Ghidra + `ghidra-xbe`; load `default.xbe` at base `0x00010000`; verify section mapping and imports.
- [~] 4.2 Apply Xbox SDK / MSVC 7.1 signature libraries (xbox-includes, FLIRT-style sigs) to auto-name CRT,
      XAPI, D3D8, XNet, XOnline, zlib and math routines.
      **Blocked on data the repository may not carry** (U9): the signature databases are built from
      Microsoft SDK/toolchain output. The part that needs no external data is done — the XBE's own
      library table names the statically linked libraries and versions — and a locally supplied
      database is the unblock, recorded in `tools/versions.lock` rather than committed.
- [~] 4.3 Locate the UnrealScript VM core in the XBE: `GNatives`/exec opcode dispatch table, `UObject` layout,
      `ProcessEvent`, `ProcessInternal`, property/`GPropPointers` tables. Document in `docs/NATIVE_VM.md`.
      Table **located** at `0x006313e8` with `EX_Max` 4096, in the uninitialised part of `.data`; the image
      stores none of its bytes, so no slot has an address in the file. The executor is now measured too:
      `UFunction::Func` is filled by the binder at `0x0003a030`, script bodies run in
      `UObject::ProcessInternal` at `0x00048bf0` (dispatch loop over `GNatives`, terminator `EX_Return`
      `0x04`), and `FFrame::Step` is at `0x000398e0` (`docs/NATIVE_VM.md`). `UFunction` fields `+0x80`
      `FunctionFlags`, `+0x84` `iNative`, `+0x90` `Func`; `FFrame::Code` is `+0x0c`.
      `UObject::ProcessEvent` is at `0x0004bf20` (1,390 bytes): it dispatches an indexed native through
      `Func` and calls `ProcessInternal` directly for a script body, with the `FFrame` on its own stack
      (vtable `0x4d9618`) and `ParmsSize` at `UFunction+0x40`. It has **no direct call site** — 492
      aligned pointers in `.rdata` and nothing else — so this fork dispatches it through a table. Still
      open: the two families of class table (U8). **`ProcessEvent`'s dispatch slot is measured**:
      slot 21 (`+0x54`) of 492 packed tables in `.rdata` (`0x004c78c0`–`0x0053b168`, 30–159 slots
      each), every start named by an immediate in `.text`; `tools/class-tables.py`,
      `inventory/xbe_class_tables.csv`.
      **The property half is measured** (`docs/NATIVE_PROPERTIES.md`, `inventory/property_classes.csv`,
      `inventory/property_types.csv`): the type names are reserved **name indices 1..16** in declaration
      order, registered with explicit indices by the initialiser at `0x0005c080` (325 names, 4,096-slot
      table at `0x006ba0c8`, name entry `+0x00` index / `+0x04` flags / `+0x08` chain / `+0x0c` wide
      name, allocation `2*len+0xe`); the **fifteen property classes** are per-class static-class
      constructors in `UnProp.cpp` (`0x00077860` `Property` 0x6c … `0x0007d170` `StructProperty` 0x70;
      three are 0x74), each naming its class and size literal through `FUN_0003af60`; and the walk at
      `0x0003bdc0` reads chain `+0x2c`, count `+0x34`, stride `+0x38`, flags `+0x3c`, offset `+0x48`,
      inner `+0x6c`/`+0x70`, with vtable `+0x94` per-element serialization and `+0xc0` destruction.
      There is **no `GPropPointers`** in the image — the roster is the constructors, not a global array —
      and the property classes have no package exports (`Core.u`: 7 class exports, 643 property exports).
      Still open here: the tagged tag record's own field order (U10).
- [x] 4.4 Auto-generate native symbol names from the packages: each `native(index)` declaration in `inventory/classes.csv`
      maps to the VM native table slot → script this into a Ghidra script that renames the target functions.
      *This is the single highest-leverage task in the project — it names hundreds of engine functions for free.*
      Done from a runtime dump of `GNatives` (16 KB over the xemu GDB stub): **273 of 273 declared indices
      registered**, 356 of 4,096 slots live at the main menu, 356 distinct addresses, 83 slot-only slots named
      `Native_<index>`. Map: `inventory/gnatives_slots.csv` → `symbols/xbe_dispatch.csv` → `XbePost` overrides.
      The table is incomplete until a mission load (U7); the first attempt at this task was withdrawn because it
      had been measured against the CRT initialiser list — see the correction note below and `docs/NATIVE_VM.md`.
- [ ] 4.5 Split the XBE into per-module Ghidra projects using the assert-path map from 2.3 (Engine, Core, IpDrv,
      Gui, D3DDrv, XboxDrv, XboxAudio, XboxLive, Gameplay, Fire, stargategame, stargatevehicles).
- [~] 4.6 Decompile + document `stargategame` and `stargatevehicles` first (small, 10 files, highest game-specific
      value: DeathGlider, AlkeshBomber, GoauldDrone, StargateCopter, Teltak, StargateHover, vehicle physics).
      All 43 functions decompiled and triaged: 39 are ucpp class-registration stubs, named from the build's own
      class literals (`symbols/xbe_overrides.csv`); 4 are real bodies, documented in `docs/NATIVE_NOTES.md`.
- [x] 4.7 Reverse the ucpp-generated class implementations (from 3.9) and verify against the leaked `.ucpp` sources.
      **Closed from the shipped source** (`docs/PACKAGE_SCRIPT_TEXT.md`, `inventory/native_declarations.csv`): every
      class carries its own `ScriptText`, and the `native` declarations name the C++ functions exactly — `SGOrder` 4
      (`isValid`, `updateCondition`, `DrawDesc`, `DrawIcon`), `Electrocutor` 2, `FarClipChange` 2, `ShadowManager` 1,
      `SGFontInfo` 1, `StargateHUD` 1 — all six matching `native_functions` in `inventory/classes.csv`. The indices are
      not in the source (`native function`, not `native(1234)`), so a debugger still needs the function record; but the
      *which* half is no longer open.
- [~] 4.8 Reverse engine-side fork changes: what did Perception change vs stock UE2.5 (renderer for Xbox,
      memory/cache, compiler behaviour, ...). **Script half measured** (`docs/systems/FORK_SURFACE.md`): 503 classes
      across the seven fork packages insert directly onto 83 stock base classes — 200 `Emitter`, 58 `Actor`, 12 `Pawn`
      — but 230 of them add no functions and no properties (data-only renames such as the 21 `Surface` subclasses), so
      the behavioural surface is 237 classes and only **6** carry ucpp natives (`SGOrder` 4, `Electrocutor` 2,
      `FarClipChange` 2, `SGFontInfo` 1, `ShadowManager` 1, `StargateHUD` 1). Remaining: the native half (renderer,
      memory, compiler behaviour) — needs the module split (4.5) or targeted export of stock engine functions.
- [ ] 4.9 Write `src/Native/<Module>/` reconstructed C for game modules with original function names, plus
      `docs/NATIVE_NOTES.md` per subsystem. Engine internals: name + document, decompile only where script-reachable.
- [x] 4.10 Emit a symbol file (`symbols/xbox.map` / Ghidra CSV) that other tools (xemu GDB, xbdm) can consume.
      Done: **15,422 symbols** (14,956 functions + 466 class tables), every address inside the image's
      sections (checked by the tool), `gdb` resolves them from `symbols/xbox.sym.s` assembled with `as`
      (`docs/SYMBOLS.md`). 12,971 stay `FUN_*` — that is 4.2's blocked signature work (U9), not a gap in
      the file. 127 names are file-level and therefore shared by more than one function; the assembled
      form suffixes duplicates with their address.

**Acceptance:** every `native()` declaration resolves to a named address; zero unnamed functions in the game modules;
`symbols/xbox.map` loads into a debugger against the original XBE without address errors.

### Phase 5 — Assets, levels, data (parallel, ~2–4 weeks)

- [ ] 5.1 umodel/UEViewer export pass over `.ukx`/`.usx`/`.utx`/`.uax` with the UE2 profile; build `inventory/assets.csv`.
- [x] 5.2 T3D-dump every `.unr` (actors, properties, level script) into `extracted/maps/*.t3d`; index mission structure.
      **Done for actors and properties, not for the level script.** All 73 maps written to `$WORK/extracted/maps`
      (27 MB, **121,225 actors**, 112,000 with a `Location`), and the writer's actor count equals the independently
      measured property-block count, so nothing is silently dropped (`docs/T3D.md`). `make t3d`. Remaining, recorded:
      object references are written as `ref:<index>` rather than resolved names (so the output is an index, not
      editor-loadable T3D), arrays/structs are emitted by size, and the `Level` export's script sequence is separate
      from the actors' properties and still unread.
      **Class-level half done without T3D** (`docs/MAP_EXPORTS.md`, `inventory/map_export_classes.csv`): all 73 maps
      parse as packages, and level actors are exports, so the actor-class census is a histogram — 6,478 (map, class,
      count) rows, 389 distinct classes, cross-validated against UELib's `map_actors.csv` (identical class vocabulary,
      256 of 389 totals exact, this reader consistently slightly low). **Remaining**: actor *properties* and the level
      script, i.e. the property-stream reader, plus the T3D text itself. `M21A` has 1,540 exports whose class index does
      not resolve — recorded, not explained. **Actor properties now parse too** (`docs/ACTOR_PROPERTIES.md`):
      121,225 of 194,190 exports across the 73 maps carry a readable property block — `StaticMeshActor` 32,191 of
      32,193, `ReachSpec` and `PathNode` at 100% — and the properties are the same `Tag`/`PhysicsVolume`/`Region`/
      `Level`/`Location`/`Rotation` baseline the checkpoint analysis found, arrived at independently. The block is
      located (class prefixes of 0/12/15/16/17 bytes) rather than assumed. **Remaining**: writing T3D text, property
      values at scale, and class defaults.
- [x] 5.3 Extract the WAVs with speech/sound categorization (dialogue, SFX, music) and pull `Speech/` dialogue
      data. All three layers are now counted: 1,364 speech lines (150.8 MiB), 1,923 music WAVs (348 MiB, 16 groups) and
      **3,047 sound effects inside 55 `.uax` packages** (88 MiB) — the last of which needed the package reader, not an
      export pass (`inventory/sfx_index.csv`, `docs/systems/AUDIO.md`). Extraction of the effects as WAVs remains the
      5.1 export pass.
- [x] 5.4 Parse `.sgs` checkpoints and `.sgp` profiles; document the save format (also needed to make rebuilt builds testable).
      A checkpoint is a UE2 package whose exports are the level's actors; 9 files, 19,080 property tags,
      1,739 distinct schema tags (415 in all nine) in `inventory/sgs_schema.csv`. The profile is INI and
      indexes the checkpoints in its `[Saved Games]` section.
- [~] 5.5 Parse `.ka`/`.kaw` ragdolls and `LIPSincData/*.lad`; document formats, map to the native loaders from 4.8.
      `.ka`/`.kaw` are Karma XML and fully documented (8 assets / 141 parts / 133 joints / 256 primitives);
      `.lad` has its header and channel table but not its payload (U4), which needs 4.8.
- [x] 5.6 Produce a "cut content report" merging 3.4 (script), 5.2 (maps), 5.3 (dialogue) and the DM/CTF maps
      that shipped in a single-player prototype. Done: `docs/CUT_CONTENT.md` carries seven findings, each citing a
      committed artifact, and a reachability census (`inventory/class_reach.csv`, `make class-reach`) that asks the
      question from the class end — **553 of 2,319 classes (23.8%) are reached by shipped content** (381 placed,
      79 checkpoint-only, 93 bases of reached classes), 359 of them mission-only against 22 multiplayer-only, and
      it independently reproduces three earlier write-ups (no AI controller placed, vehicles placed, weapons not).
      The 1,766 unreferenced classes are not called cut: defaults-level references are game data this repository
      does not publish.

**Acceptance:** every asset class in `inventory/assets.csv` has a documented extraction path, and the cut-content
report cites file-level evidence for each claim.

### Phase 6 — Rebuild pipeline (~2 weeks, after 3.6)

- [~] 6.1 Scripted rebuild: `tools/build.sh` → compile `src/UnrealScript` → produce `.u` packages → place into a
      working copy of the dump. The container side is measured end to end: tables re-encode and whole files rebuild
      byte for byte (18/18), the placement pass reproduces every package (18/18), and a payload can now be **resized**
      and round-tripped (18/18 packages, 84 sampled resizes, `inventory/package_relayout.csv`). What is left is payload
      *generation*: a compiler producing the bytes we currently copy.
- [ ] 6.2 Solve the package-check problem: regenerate `Packages.md5`-equivalent check data, or patch the check
      off in the XBE for dev builds. Document both paths in `docs/PACKAGE_CHECK.md`.
- [~] 6.3 Ensure deterministic output (fixed timestamps, sorted names) so rebuilt packages diff cleanly between runs.
      **Not claimed yet, and the reason is structural**: every payload the writer places is *copied*, so two runs
      cannot differ — the write path is reproducible (`docs/REBUILD.md` stage 2, 18 of 18 byte-identical), but a
      *build* is not, because generated payloads (the compiler's output, its timestamps and name ordering) do not
      exist yet. 6.3 closes with stage 5 of the pipeline, not before.
- [ ] 6.4 Produce two build targets: `rebuild-hybrid` (our scripts + original assets) and `rebuild-full`
      (our scripts + re-extracted assets) and record which missions boot in each.

**Acceptance:** `tools/build.sh` on a clean checkout produces bootable packages; rebuilt-vs-original package diffs
are limited to expected fields (checksum/timestamp) and documented.

### Phase 7 — Verification harness and CI (~1 week, then continuous)

- [x] 7.1 xemu configured as an Xbox debug kit (128 MB RAM, debug BIOS, HDD image) per Hidden Palace's notes for
      other debug-kit dumps; document exact config in `docs/XEMU.md`.
- [~] 7.2 Smoke test script: boot → title screen → Continue → load each mission → capture screenshot + log tail.
      **Boot half done and passing** (`tools/xemu/smoke.py`, `docs/XEMU.md`): the harness boots the title and proves
      the guest executes game code by sampling `EIP` (8 samples, 7 in user space, 6 distinct). Five emulator traps
      measured and fixed on the way — `-mcpx_path` gone in 0.8.139, `-serial` needs `file:`, extract-xiso 2.7 ignores
      `-o`, `screendump` absent from QMP, and a zero-filled EEPROM (or a fresh HDD) leaving the CPU halted in the
      kernel forever. **Still open**: pixel-level assertion (needs F12 + X input automation, or a GDB-stub hook) and
      mission loading (needs input automation).
- [ ] 7.3 Gameplay probes on keyboard (Tab opens the Unreal console): scripted `exec` commands to dump actors,
      walk objective graph, spawn/test classes in isolation.
- [~] 7.4 CI job per commit: manifest verify → package parse → decompile coverage → compile status →
      bytecode match % → asset count. Publish the scoreboard as the repo's README badge/table.
- [~] 7.5 Nightly regression: rebuild + xemu boot test; fail on any new assert dialog or missing class.
      **The harness exists and runs** (`tools/nightly.sh`, `docs/REGRESSION.md`): `make ci`, then `verify-dump`,
      `class-tables`, `libraries` when `SG1_DUMP` is set, then optionally the xemu boot, one summary and one log per
      stage under `build/nightly/<UTC>/`, exit non-zero on any failure, last 30 runs kept. It reports `git status`
      drift instead of repairing it. **Not yet**: the rebuild stage (blocked on 3.6) and assert-dialog detection (the
      evidence channel is `EIP` sampling, `docs/XEMU.md`).

**Acceptance:** CI is green on a stock machine, the scoreboard moves in the right direction on every PR,
and a deliberate one-line script break is caught by the boot test.

### Execution notes (2026-10-26, the boot harness gets a memory channel)

- **7.2's assertion was CPU-only because the obvious channels are missing** (`screendump` absent, serial log empty,
  `pmemsave`/`memsave` unusable over the human monitor). The GDB stub is a third channel and it works:
  `tools/xemu/mem.py` reads guest **physical** memory, dumps a region, scans it for strings, and reports where a
  given text occurs — measured 512 KiB in 1.7 s in 16 KiB chunks and 2 MiB scanned in 14 s, so 128 MiB would be
  ~7 minutes and a targeted region is seconds.
- **The one non-obvious step is worth the entry**: every read returns `E14` until the stub is switched with
  `Qqemu.PhyMemMode:1`; without it the stub answers against the CPU's current virtual context, which looks like a
  broken stub rather than a wrong address space.
- **Measured proof it reads real content**: `--find xbox` reports two hits inside `0x80000..0x480000` of a booted
  guest, and `--dump 0x1000:0x8000` writes 32 KiB. What it does *not* prove is anything about the game: the guest
  was booted without a disc, because `dvd_path` needs a regular file and no XISO exists on the desktop.
- **Next for 7.2**: build the XISO (`tools/xemu/build-xiso.sh`), boot the title, then assert on memory — the title
  screen's own label in RAM is a *proxy* assertion and must be described as one, since pixels are still unreadable.

### Execution notes (2026-10-26, UNKNOWNS re-measured; the boot harness's limits written down)

- **U3 is answered, and the answer changes a rule**: `UPreviewMapListClient.Created`'s index 2 is not a stray value.
  Exactly two classes carry a token in the record without an index in the text — `Core.Object` (160 vs 143, mostly
  the compiler-synthesised operator records) and that class (1 vs 0). So the record's token is authoritative and the
  source's index is not exhaustive: a rebuild must read indices from the packages, never from the text.
- **U1 gets its cross-check**: 256 declarations spell an index out, 274 records carry a token, and all 256 agree with
  their record (0 disagreements) — the 18-record difference is the U3 pattern, not a parser artefact.
- **U13 is new and is where 3.7 should start.** Two measured negatives: UELib's `UStruct.Deserialize` order validates
  for 1 of 3,931 `Engine.u` functions, and the content-side anchor (`04 27` = `return true;`, exactly 12 bytes before
  the payload end in 424 functions) has no consistent size field before it (11 of 1,224). Next route: decompile
  `UStruct::Serialize`, or find the loader's store to `[reg+0x4c]` (`Function->Script`).
- **Three host facts that cost this session their time**, now in `docs/XEMU.md`: the GL context needs the live
  session's Xwayland cookie (`XAUTHORITY=/run/user/1000/xauth_*`, not `$DISPLAY` alone); `dvd_path` must be a regular
  file, so a title-screen run needs a XISO from `tools/xemu/build-xiso.sh` (none exists today) and a directory mount
  silently idles at the shell; and `pmemsave`/`memsave` are not usable as documented over this xemu's HMP, so guest
  memory cannot yet be read and the CPU stays the only assertion.

### Execution notes (2026-10-25, the rebuild pipeline is scripted; four stages proven, four SKIP)

- **`tools/build.sh`** (`docs/REBUILD.md`, `make rebuild`, a nightly stage) runs the eight stages in order and
  reports PASS / FAIL / SKIP with a reason. Four pass in the container against the copied packages: writer relay
  18/18, rebuild identity 18/18 with 100% of bytes accounted for, the source tree, and the defaults merge into it.
- **Four SKIP, each naming its blocker**: `compile` (no `ucc` matching package version 130 / licensee 4), `repack`
  (needs compiled payloads), `package-check` (bypass decided in `docs/PACKAGE_CHECK.md`, not implemented) and `boot`
  (needs a repacked image; the harness exists).
- **6.3 is deliberately not claimed.** Determinism is trivially true today because every payload is copied, so
  measuring two runs would prove nothing about a build; it closes with the compiler stage. That is the difference
  between a green check and a real one, and the doc says it in those words.
- **The first attempt at this stage was wrong and is worth recording**: it compared two `--rebuild --write` runs, but
  the writer does not have a `--write` for `--rebuild` (it diffs in memory), so the stage "failed" on a missing
  directory. It now greps the writer's own 18/18 claim, which is the evidence that actually exists.
- **Bytecode (3.7) is still the blocked item**, and this session adds to the negative: the payload-anchor route was
  tried from the content side too — trivial bodies (`return true;` = `04 27`) end exactly 12 bytes before the payload
  end in 424 functions, but no consistent script-size field precedes them (11 of 1,224 candidates), so the layout is
  not derivable from the bytes alone. The next attempt should decompile `UStruct::Serialize` in the XBE.

### Execution notes (2026-10-25, the native declarations reconcile to 98.1%, indices to 0 disagreements)

- **`inventory/native_reconcile.csv`** (`tools/native-reconcile.py`, `make native-reconcile`, a nightly stage) puts the
  shipped text and `inventory/natives.csv` side by side per class: 681 `native function` declarations (256 indexed),
  92 operator declarations, 685 function records — **672 matched (98.1%) and 0 index disagreements**.
- **The index check is the useful half**: every `native(N)` agrees with its record's token, and 256 indexed
  declarations is the same index space `docs/NATIVE_VM.md` reports. A rebuild can now cross-check natives instead of
  trusting a list.
- **Three wrinkles had to be handled rather than smoothed**: headers that wrap across lines (statements matched, not
  lines), `Core.Object`'s operator and pre-operator natives (a function-shaped regex misses 86 of them), and the
  compiler's synthesised per-signature operator records (112, counted apart by design).
- **Residual, named not explained**: 22 records over 14 classes — `PlayerController` 4 (`Client*`/`Server*` pairs
  where the text looks truncated), `Core.Object` 2, `Weapon`/`GUIController`/`StargateWeapon`/`HitEffectManager` 2
  each. The two classes whose text the extraction already flagged as truncated are *not* among them, so truncation
  is a candidate cause, not a demonstrated one.
- **Bytecode (3.7) was the alternative and is still blocked**: a full-payload search over the class record no longer
  applies, but a function export's own layout did not fall out of UELib's reader either — the three anchor
  experiments (ScriptText/child index anchoring, compact-vs-int32 field widths, 13 trailing function-header bytes)
  produced no consistent parse, so the next attempt should start from the XBE's `UStruct::Serialize` rather than
  from the payload bytes.

### Execution notes (2026-10-25, 5.6 closes from the class end)

- **`inventory/class_reach.csv`** (`tools/class-reach.py`, `make class-reach`, `--check` in `make ci`) joins four
  committed artifacts into one row per class: placed in a map, present in a `.sgs` snapshot, a base of something
  reached, or named by a spawner. **553 of 2,319 classes (23.8%) are reached by shipped content** — 381 placed
  (359 mission-only, 22 multiplayer-only, 141,796 actors), 79 checkpoint-only, 93 bases.
- **It reproduces three write-ups from a fourth file**, which is why it is worth carrying: 25 controller/bot
  classes with **0** placements (AI.md), `StaffCannon` 21 / `DeathGlider` 12 / `Hummvee` 9 (VEHICLES.md), and 1 of
  15 weapon classes placed (WEAPONS.md). Divergence would have meant one of the four was wrong.
- **The 1,766 unreferenced classes are named, not judged.** Reaching them needs a *default property* (a pawn's
  inventory, a roster's members, an `AIScript`), and defaults values stay in `$WORK` by the no-game-data rule, so
  the report says what the artifact can see and refuses the cut-content reading — the same standard finding 7
  applied to the oracle diff.
- **Negative:** the 601 names in `squad_scripts.csv` match no class — they are `.uc` AI scripts, so the
  spawner→roster link cannot be followed from the map side.

### Execution notes (2026-10-25, the withheld blocks: three explanations tested and rejected)

- **`--explain` now reports the residue**, and it rules out the cheap explanations rather than leaving them for the
  next session: the 64-byte **trailer bound** (relaxing it to 4,096 recovers 2 classes / 4 properties), **missing
  property metadata** (`inventory/class_properties.csv` covers all 11,493 class-owned property exports, 0 missing),
  and **payload size** (clean payloads are also small — median 159 B, minimum 100 — against 112 B for withheld).
- **The signature of the 543 is a record, not a tag stream**: 227 have exactly one unaccounted name and 316 have two
  or more, and the names are dominated by *package* names — `Core` 252, `System` 189, `Engine` 73, `Lighting` 69 —
  which cannot be property names. The bytes between them are hash-like values that repeat across classes
  (`b31d40ff`, `2c08ad6c`, `5911e68c`), so the likely reading is "this class sets no defaults", with a component
  table as the competing candidate. Whether it is either is not settled, and the doc says so.
- **Next:** the reader of that table in the XBE would settle it (the route `docs/NATIVE_PROPERTIES.md` took for the
  tag format). The rest of the defaults residue is small: 18 arrays and 12 `InterpCurve` structs, both refused by
  the tiling test.

### Execution notes (2026-10-25, the locator was scanning 512 bytes)

- **Most of the 532 withheld classes were not ambiguous blocks: the locator never looked that far.** It searched the
  first 512 bytes of each export, and a class export puts its record (script tables, super, package dependencies)
  *before* the defaults block. Every base class has a longer record than that — `Actor` 2,451 bytes, `Pawn` 3,992,
  `PlayerController` 13,665, `GameInfo` 5,682 — so the classes with the most defaults were exactly the ones reported
  as having none. Scanning the whole payload: blocks found 2,084 → **2,209**, fully declared 1,552 → **1,666**,
  no block 235 → **110**, 114 classes recovered — the inheritance spine, which is why rendered properties went
  13,307 → **15,491**.
- **The two defaults artifacts had no generator.** `inventory/class_defaults_coverage.csv` was produced once by hand
  from `--sweep` output and `class_defaults_per_class.csv` came from `--packages`. The coverage file now has a
  `--coverage` mode that reproduces the *old* committed file byte for byte before the window changed (2,319 classes,
  2,084 blocks, 1,552 clean, 411 ambiguous, 235 none), so the semantics are confirmed rather than assumed, plus
  `make defaults-coverage` and `make defaults-per-class` so `make inventory-index` resolves both.
- **Status moved in two places**: class defaults decoded 66.9% → **71.84%**, default properties rendered 99.72% →
  **99.75%**, subobject defaults 94.90% → **94.94%**.
- **Next:** the 543 blocks still withheld (does the tail candidate belong to the class at all?), the 110 classes with
  no candidate, and the 18 arrays / 12 structs the tiling test refuses.

### Execution notes (2026-10-25, subobjects and the `editinline` correction)

- **The previous PR's explanation of the 270 `editinline` arrays was wrong and is corrected here.** Their elements
  are ordinary compact object references; the failure was `INLINE_FLAGS` stripping a single leading flag from
  `array<export editinline ParticleEmitter>`, leaving `editinline ParticleEmitter` as the "type". One regex quantifier
  took rendering from 13,033 to 13,304 (99.7%) — a string bug that had been read as a missing byte format and
  documented as one.
- **An `editinline` reference points at an export whose outer is the class**, and the compiler expects each one
  declared by a `Begin Object Class=.. Name=..` block before the assignment naming it. The emitter now renders those
  blocks with the same locator over the subobject's own export: **1,269 subobjects, 15,687 properties, 14,887
  rendered**, so a class's defaults are 13,307 + 14,887 properties rather than 13,307 alone. Without them the
  references name objects the compiler has never seen.
- **`Plane` is atomic by name and tagged in this build** (29 bytes, four tagged floats), so a name-based atomic
  layout now falls through to the tagged reader instead of refusing: structures 15 → 12 unrendered.
- What is left is 17 arrays and 12 `InterpCurve` structs — element types the tiling test refuses rather than reads
  short. `make defaults-tree` still merges 1,552 blocks, and a second merge reproduces the tree byte for byte with
  the `Begin Object` blocks in place.
- **Next:** the 532 withheld blocks (1,371 properties), then `replication` blocks and state code, which are the
  remaining 3.5 gaps.

### Execution notes (2026-10-25, array and struct defaults decode from the reference)

- **The pinned UELib already contained the spec.** `tools/uelib/src/Core/Classes/UDefaultProperty.cs` is the
  defaults deserialiser, and two facts in it closed the array work: an array is a **compact-index count then that
  many *untagged* elements**, their width taken from the property's inner type, and a struct that is not one of the
  four atomic layouts is a **nested tagged block** (`Field=value` … `None`). The previous session's guess — "each
  element carries its own info byte" — was wrong and measured as wrong (66 of 555). Reading the reference cost one
  fetch and converted the guess into a reader.
- **Rendering went from 12,807 to 13,033 of 13,345 (97.7%)**: 173 array properties (317 elements) and 53 nested
  structs, all with the tiling check that makes a wrong inner type a refusal rather than a short element.
- **The 270 remaining arrays are a different format, not a harder version of this one**: `editinline`
  (`ParticleEmitter` 189, `RosterEntry` 71, `SGInteraction` 6, `SVehicleWheel` 4) serialises each element as an
  inline object instance, which UELib reads through its inline-object path. The last 18 arrays and 15 structs are
  element types that decode in one class and not another — recorded per class, not explained.
  **Corrected in the next note:** that explanation was wrong. Those elements are ordinary object references;
  `editinline` is a flag in the property's type string and stripping only one of two flags made them look like an
  unknown element type. A string bug was reported as a format gap.
- **Two quoting defects the arrays exposed**: an element name with a space (`bip01 Spine`) and an element string
  were emitted raw, which the compiler would read as two tokens. Both now go through the same quoting rule as the
  scalar paths. A rendered string can also contain `}`, which is why `--merge` cuts at the *last*
  `defaultproperties` head instead of matching a closing brace — that is what made the merge non-idempotent.
- **Next:** inline-object array streams (270), then the withheld 532 blocks.

### Execution notes (2026-10-25, class defaults render back to UnrealScript)

- **The defaults half of 3.6's input exists.** `tools/defaults-emit.py` renders each located block as
  `defaultproperties` text: **12,807 of 13,345 properties (96.0%)** over **1,552 classes**, and
  `make defaults-tree` merges them into the `make extract-script` tree (1,552 of 2,319 files get a block, and a
  re-run reproduces the tree bit for bit). `inventory/class_defaults_emit.csv` is the per-package artifact;
  `docs/CLASS_DEFAULTS.md` is the page; the status table gained a *default properties rendered* row.
- **Two value defects were hiding behind correct-looking names.** The FString length prefix **counts the
  terminating NUL** and the NUL is only stored when the declared size has room, so decoding from the prefix byte
  printed `Description` as "\x0cEmpty Order"; and an imported object reference was rendered with its class
  package in the class slot (`icon_mission'Engine'`) — it resolved, so it looked decoded. Both fixed in
  `tools/prop-stream.py`, which the T3D pass also uses.
- **A per-package enum table resolves a third of the byte values; a whole-dump one resolves most of them.**
  224 of 1,211 → 735, because a `StargateGame` class inheriting `Physics` takes its element names from `Engine`'s
  script. Inside the emitted blocks: 722 of 965 (74.8%). This is the same lesson as the defaults locator —
  the reference set, not the layout, was the limit.
- **Emit only from blocks that account for themselves.** 532 of the 2,084 candidate blocks hold a property the
  class does not declare, and they hold almost every impossible value (an `Invalid` type, a 16-byte `Float`, a
  139-byte `Vector`). Their 1,371 properties are counted and withheld; emitting them would have raised the pass
  rate four points and shipped nonsense.
- **The shipped text is not an oracle for defaults, and `--verify` says so.** Only two classes still carry a
  block: `Core.Locale` (50 properties in the text, none in the package) and `GUI.SGVertScrollBar` (13 in the text,
  2 in the package, one matching and `WinWidth` differing — text `0.02`, bytes an exact `20.0`). Three
  explanations fit and none is measured; what does not depend on which is that the packages are the authority.
- **Arrays are still open, and the first guess is measurably wrong.** "count then tagged elements" parses 66 of
  555 instances, so they are counted (`461` in clean blocks) rather than guessed at; the next attempt should start
  from UE2's `UArrayProperty::Serialize`, not from the bytes.
- **Next:** the remaining `ucc`-blocked items are unchanged (`ucc` under Wine), so the finishable work is array and
  struct value decoding, then the withheld 532 blocks (is the tail candidate a truncated real block or a parse
  coincidence?) one class at a time.

### Execution notes (2026-10-25, the writer resizes a payload — and growth does reproduce)

- **`--resize` re-lays a whole file around one payload resized by N bytes, and grow-then-shrink comes back
  byte-identical**: 18 of 18 packages, 84 sampled resizes (`make pkg-resize`, `inventory/package_relayout.csv`,
  `docs/PACKAGE_LAYOUT.md`). `--relay` now runs through the same placement function with a zero delta, so the proof and
  the moving path cannot drift apart.
- **The "growth mis-decodes the first entry" finding does not reproduce on the shipped path.** Driving the regrow
  function that merged in #71 with `extra=64` at export 1 re-parses cleanly — `GamePlay.u` 334,722 → 334,786 bytes and
  `Core.u` 79,165 → 79,234, zero unresolved classes in either, first entry's size grown by exactly the delta. So the
  earlier symptom is narrower than the note above says (a specific export, or the pre-placement regrow), and growth is
  no longer a claim to hedge: it is measured. What makes that safe is not the placement arithmetic but the checks —
  see the next two points.
- **Three checks a resize has to pass, and the old mode had none of them.** Re-read the result **from disk** through
  the reader (not the in-memory dict copy — that is what hides a misplaced table); every other payload byte-identical;
  and **grow, then shrink by the same amount, must equal the original file**. A table diff cannot see a region in the
  wrong place, a round trip can.
- **The sample is aimed at the entries a move can corrupt**: per package, the smallest and largest export, the one
  nearest a 0x3F *size* boundary, the one nearest a 0x3FFF *offset* boundary, and the last export. Those are where a
  compact-index width changes; a naive offset shift gets exactly them wrong and nothing else.
- **A shrink is not reversible and is not claimed to be.** It discards the payload's tail, so the negative direction is
  held to the container instead (tables, lengths and other payloads identical, target back to its old size on re-grow).
  The resized payload's contents are meaningless either way: this proves the container around a payload, not the export
  inside it, and nothing was loaded by the engine.
- **The layout solver is testable with no dump**: `--selftest` (in `make ci`) builds a synthetic package from our own
  encoders and drives five cases through writer and reader, including a 20,000-byte grow that widens every export entry
  behind it and a shrink to zero.

### Execution notes (2026-10-24, the placement pass works; growth does not yet)

- **`--relay` reproduces all 18 packages byte for byte through the writer's own placement**: header, names,
  payloads, then the two object tables, with the header's `export_offset`/`import_offset` patched. Unlike `--rebuild`
  (which places regions at their original offsets), this *chooses* the positions from the model, which is what a
  writer must do once a payload changes size.
- **Payload growth is still not offered.** With a non-zero growth the re-encoded export table mis-decodes its first
  entry and classes stop resolving; the symptom is localised to the first entry and later entries shifting by one,
  the cause is not established. The flag that ships is the one that passes its own check.
- Correcting the previous note: that PR claimed the grow attempt was reverted. It was not — the broken code merged
  and could not pass its own check. Both docs now say so.

### Execution notes (2026-10-24, package layout — and a reverted attempt)

- **Every package lays out as `names < payloads < imports < exports`**: the two object tables sit at the *end* of
  the file, after all payload data, and no header field says where payloads begin (`docs/PACKAGE_LAYOUT.md`,
  `inventory/package_layout.csv`, `make pkg-layout`).
- That order is why a first attempt at `--grow` (enlarge one export's payload, re-lay the file) failed with a
  `struct.error` at end-of-file rather than anything diagnostic: the writer emitted tables before payloads, so
  `export_offset` pointed past the data. **The attempt was reverted, not shipped half-working** — a rebuild that
  changes payload sizes has to relocate both tables and solve the layout to a fixed point, because a shifted offset
  can need an extra byte in the compact index.
- Next step for 6.1 is that fixed-point writer, and the census is what makes it a small job instead of another
  session of wrong hypotheses.

### Execution notes (2026-10-24, whole packages rebuild byte for byte)

- **All 18 packages rebuild from header + tables + payloads and come out identical**, 100% of bytes accounted for
  and **zero gaps** (`make pkg-rebuild`). That upgrades #68's table proof to a *layout* proof: the offsets and
  serial arrangement are recomputed from the parsed model, not copied. The gap report is the useful part — an
  unexplained byte range would name what a writer gets wrong later.
- Still missing for 6.1 is only payload **generation** (a compiler's job) — the scaffolding around it is now
  measured rather than assumed.

### Execution notes (2026-10-24, the package writer's tables are proven)

- **18 of 18 packages re-encode their tables byte for byte** — 37,094 names, 7,500 imports, 57,749 exports
  regenerated from the parsed model by an independently written compact-index encoder (`tools/pkg-write.py`,
  `docs/PACKAGE_WRITER.md`, `make pkg-verify`). That is the strongest evidence short of a rebuild that the container
  format is right, and it closes the layout doubt that `docs/PACKAGE_READER.md` records from six failed attempts.
- **What is still missing for 6.1 is payload generation** (a compiler's job — the writer copies export data rather
  than synthesising it) and the package summary, which is carried through verbatim rather than modelled. State that,
  not "the writer works".
- `--selftest` exercises the index codec with no dump present and runs in `make ci`, so the encoder cannot rot
  silently.

### Execution notes (2026-10-24, the path to a build)

- **`docs/PATH.md` separates the two goals that "builds and runs" hides.** Goal A (our scripts, shipped engine and
  assets) needs one acquisition — a UE2 compiler matching version 130/licensee 4, absent here while Wine is present
  — and then mechanical work: 607 default blocks, a package writer, the check bypass, a boot-and-diff loop.
  Estimate 2–4 months, with the honest caveat that it is months *or never* depending on whether a matching compiler
  exists. Goal B (reconstruct 14,957 native functions, 13.29% named, 0 reconstructed C) is years and does not get
  there by continuing function by function; it needs a change of method (fork classes first, stock engine by
  lineage).
- Recorded so the next session does not re-litigate it: **the next actionable step is acquiring `ucc` and compiling
  one trivial class**, not more measuring.

### Execution notes (2026-10-24, defaults state, and the test that nearly inflated it)

- **Compile readiness is 1,712 of 2,319 (73.8%), up from 66.9%**, and the gain is a distinction rather than a
  parser change: a class with **no properties anywhere in its ancestry** cannot be blocked by an absent defaults
  block, because it has nothing to set. `inventory/compile_readiness.csv` now carries `defaults_state` with four
  values — `read` 843, `not-applicable` 869, `ambiguous` 400, `none` 207 — and only the last two block.
- **The tempting wrong version of that test is the class's own property list**, and it would have reported 79.2%:
  1,215 classes declare no properties of their own while inheriting dozens. The artifact counts the inherited
  closure instead (`properties_inherited`), which is the number the claim actually rests on.
- `docs/COMPILE.md` states both numbers and why one of them was wrong — a measurement page that hides its first
  wrong answer is worth less than one that shows the correction.

### Execution notes (2026-10-24, compile readiness measured per class)

- **1,552 of 2,319 classes (66.9%) have every input a rebuild needs** — source, a default-property block whose
  properties the class declares, and an ancestor that is in the tree. 767 are blocked on the default block alone,
  which is now a number rather than a caveat (`docs/COMPILE.md`, `inventory/compile_readiness.csv` by class,
  `inventory/compile_readiness_by_package.csv` rollup, `make compile-readiness`).
- The ancestry check has to be **case-insensitive**: the inventory spells `GamePlay` while `package_of_super`
  says `Gameplay`, and a case-sensitive join invented 70 "super-not-in-tree" classes that do not exist.
- `inventory/class_defaults_per_class.csv` is new: 2,319 rows from `class-defaults.py --packages`, and its total
  (1,552 fully declared) matches `inventory/class_defaults_coverage.csv` exactly, which is the cross-check.
- 3.6 remains `[~]`: the hard blocker is still `ucc`, and this measures inputs, not compilation.

### Execution notes (2026-10-24, the map-side census)

- **Two of the three remaining orphans now reproduce byte for byte**: `map_property_coverage.csv` (73 maps) and
  `actor_property_coverage.csv` (389 classes) are rebuilt by `tools/map-census.py` / `make map-census`, using the
  same block locator as `class-defaults.py`. Ties in the class table had to keep **first-seen order**, not
  alphabetical, to match the committed file — the values were identical and only the ordering differed.
- **`squad_scripts.csv` is not reproducible with that rule**: 581 `(map, script)` pairs against the committed
  614 (35 only in the old file, 2 only in this pass), so the original enumeration was broader than a located
  property block. The tool prints the delta and refuses to overwrite, and `docs/T3D.md` says why — losing 35
  references that other docs count would be worse than an unreproducible number that is labelled as such.
- Orphans left: 1 (`squad_scripts`).

### Execution notes (2026-10-24, closing the orphan generators)

- **Three of the seven orphaned artifacts now have a committed generator that reproduces them byte for byte.**
  `tools/package-census.py` rebuilds `package_exports_summary.csv`, `package_script_text.csv` and
  `script_text_natives.csv` from the dump, `--check` proves identity, and `make package-census` runs it. The stored
  `script_text_bytes` counts the `TextBuffer` export's bytes rather than the decoded string — the first attempt came
  out 4% low for exactly that reason.
- The fourth, `native_declarations.csv`, was a curated subset: a class declaring a native **no ancestor declares**
  is 11 classes, not 6. The six the ucpp work named are those that also carry a `ucpp_Register_` stub
  (`docs/NATIVE_CLASSES.md`). The artifact is now generated by the measurable rule.
- Remaining orphans: `actor_property_coverage`, `map_property_coverage`, `squad_scripts` — all map-side.

### Execution notes (2026-10-24, the artifact index)

- **Seven committed artifacts have no generator at all.** `tools/inventory-index.py` resolves every committed CSV
  to the `make` target that rebuilds it (through the Makefile, or through the tool that writes it, Ghidra scripts
  and `tools/Sg1Tool` included) and reports the rest. Result: 58 artifacts, 54,647 rows, **7 orphans** —
  `actor_property_coverage`, `map_property_coverage`, `native_declarations`, `package_exports_summary`,
  `package_script_text`, `script_text_natives`, `squad_scripts` — each produced by a one-off script that was never
  committed, so nothing rebuilds it. Nine more have a writer but no `make` target. `docs/ARTIFACTS.md` is the
  generated page and `--check` is wired into `make ci`.

### Execution notes (2026-10-24, the package check file)

- **The check file was being read wrong.** Its 168 records store *hex text* (`00 21 <32 upper> 00 01 21 <32
  lower> 00`, no filename field), and `tools/package-check.py` was searching for raw 16-byte digests — a
  comparison that could never match whatever the transform was. With the comparison fixed, the negative is real
  and much wider: plain `md5` of **all 4,003 files** in the dump, 89 range/zeroing variants on `Engine.u`, and 8
  name-/data-shaped variants across **489 package files** all miss. So 6.2's "regenerate the check" route is out
  of reach; **bypass** is the practical one, and that is now evidence rather than suspicion
  (`docs/PACKAGE_CHECK.md`).

### Execution notes (2026-10-24, the entry point in the analysis)

- **The analysis was being told the wrong entry.** `tools/ghidra/run-analysis.sh` passed `0x10184` — the
  certificate address — to both `XbePre` and `XbePost`, so the CRT entry at `0x0033F317` was never created and
  the function map started at `FUN_0033f389`. The script now resolves the entry through `tools/xbe.py` and
  refuses to guess. One run later: `XbeEntry` at `0x0033F317` (114 bytes), functions **14,956 → 14,957**, one
  addition, zero removals, zero renamed, zero previous starts left uncovered, 15,423 symbols all inside the
  image. `docs/XBE_LAYOUT.md` and U12 record it.

### Execution notes (2026-10-24, the XBE entry point — U12 resolved)

- **The entry point decodes after all: `0x0033F317`, with the debug-build key `0x94859D4B`.** The retail key
  `0xA8FC57AB` gives `0x3C4A39F7`, which is why the field looked unreadable. Three checks agree: the bytes
  before the address are another function's `xor eax,eax; ret 4` tail (so it is a function *start*), the body is
  CRT start-up that reads the XBE header back and aligns the heap, and only the debug key lands in a section.
  `Xbe.entry`/`entry_key` carry it (`docs/XBE_LAYOUT.md`, U12). **Follow-up worth one Ghidra run:** the entry is
  not in `symbols/xbox_functions.csv` (nothing references it, and the header value was never passed to the
  analysis) — seed and name it, which adds the CRT function to the map.

### Execution notes (2026-10-24, squad rosters)

- **The `SQUAD_*` rosters read: 58 of 65, at a uniform `+0x5D`, 129 members in total** and 44 of them
  single-member (`docs/systems/SQUADS.md`, `inventory/squad_rosters.csv`, `make squad-rosters`). `PawnTag` is
  authoring metadata rather than a key — `Dropturret` is shared by three rosters. The blocker is the last hop:
  `Members` (declared `array<export editinline RosterEntry>`) is one count byte then **two bytes per member**,
  and those values are consecutive serial numbers (`SQUAD_Jaffa_Full_Patrol`: 30573…30588), so they index
  neither the name table nor an object table this reader can see. Seven rosters have no `Class` export at all,
  and all seven are vehicles or single minions. Status table gained a "class defaults decoded" row.

### Execution notes (2026-10-24, defaults: three defects fixed, 67% coverage)

- **Class defaults read cleanly for 1,552 of 2,319 classes (67%)**, up from 18 of 306. Three defects were
  found and fixed together: `Str` values were read as name indexes (so `Description` decoded as `BeginState`),
  the block was required to end exactly at the export end (throwing away classes with a trailer), and the super
  chain was followed within one package only (so cross-package inheritance was invisible and correct blocks
  scored as undeclared). `SGOrder` now reads 8 defaults with real values, `Description = "Empty Order"`
  (`docs/CLASS_DEFAULTS.md`, `inventory/class_defaults_coverage.csv`). `--walk` exists because the byte-level
  view is what settled the `Str` question.

### Execution notes (2026-10-24, defaults: block chosen, values partial)

- **The block ambiguity is settled semantically and the values are not.** Scoring candidate blocks by how many
  of their property names the class actually declares (`inventory/class_properties.csv` plus the super chain)
  moves `SGOrder` from a 10-property block containing `Core` and `Range` to its real 8-property block at `+0xB3`
  (`tools/class-defaults.py`, `docs/CLASS_DEFAULTS.md`). But sweeping `StargateGame`'s 306 classes gives only
  **18** blocks whose names are all declared, 188 ambiguous and 73 with no candidate, so names are enough to
  *reject* a wrong block and not to *choose* between two plausible ones. `Bool`/`Int`/`Byte`/object values decode;
  `Str` values do not (`Description` reads as the name `BeginState`). Squad scripts named by maps appear in those
  packages' name tables but are neither imports nor exports, so the roster is referenced from inside the `Level`
  export.

### Execution notes (2026-10-24, class defaults)

- **Class defaults are locatable, not yet readable in full.** `tools/pkg-defaults.py` finds a class's default
  block by the same boundary rule the actor reader uses: `SGOrder`'s 229-byte export carries 10 defaults at
  `+0xAC` with `Bool`/`Int`/`Byte`/object values decoding correctly (`canPerformOneill=True`,
  `maxBotsAssigned=-1`, `Condition=1`). Two problems are recorded rather than worked around: string values
  misalign because two candidate starts two bytes apart both pass the test, and the squad scripts the maps name
  (`TSS_7A_B_Jaffa_01`) are not `Class` exports in any `System/` package or in the referencing map, so the
  roster link from `docs/T3D.md` stays open (`docs/CLASS_DEFAULTS.md`).

### Execution notes (2026-10-24, ScriptText)

- **The shipping packages contain their own UnrealScript source.** Every class carries one `ScriptText`
  `TextBuffer` export: 2,319 classes, 2,319 exports, **6,149,951 bytes** (5.9 MiB), agreeing exactly with the
  class counts in `inventory/classes.csv` for all 18 packages (`docs/PACKAGE_SCRIPT_TEXT.md`). This changes the
  oracle situation materially: 3.7's bytecode comparison can start from the exact text that produced the shipped
  bytecode instead of a decompiler's reconstruction, and `ORACLE_DIFF.md`'s 291-class sample is no longer the only
  readable source. The text itself stays out of the repository (AGENTS.md); only counts and sizes are committed.

### Execution notes (2026-10-24, the package tables)

- **The package reader now decodes both object tables.** `ImportEntry = ClassPackage, ClassName, Outer(int32),
  ObjectName` and `ExportEntry = Class, Super, Outer(int32), ObjectName, Flags(int32), SerialSize,
  SerialOffset`, with the index encoding from `surrealengine`'s format doc (bit 7 sign, bit 6 continue, 6 then
  7 bits). Validated semantically: `Class` exports per package equal `inventory/classes.csv` exactly — 18
  packages, **2,319 classes**. Six earlier attempts failed because `Outer` and `Flags` are plain int32, not
  index-encoded, which kept the parse plausible while the values were nonsense. This is the prerequisite for
  4.7 (function records) and 5.2 (map actors).

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
  remaining opcodes named, which means reading them off the native implementations rather than the
  dispatch table — the table has no bytes in the file and gives an index, never a name (4.3/U7).
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
- **4.6 started, and the game modules are not what the plan expected.** All 43 functions in the two
  modules decompile (`tools/ghidra/export-c.sh`, 13,737 symbols applied first so the C reads as
  names). **39 of the 43 are generated ucpp class-registration stubs**: each calls the class
  constructor with the class name, outer package `System`, object size and flags as literals, then
  asserts `ReturnClass`. The class literal is the build's own data, so they are named
  `ucpp_Register_<Class>` through `symbols/xbe_overrides.csv` (applied by `XbePost` after the assert
  pass), and the object sizes — 3,788 `AlkeshBomber`, 4,344 `StargatePlayer`, 3,344
  `StargateVehicle` — fall out as the class layout sizes for 4.3/4.7. The remaining four functions are
  the real bodies: `StargateCar` velocity projection and clamping, two `SGCameraEffects` depth-of-field
  bodies, and one `GoauldDrone` stub without a literal (U6). Consequence for the plan: the gameplay is
  not in these modules — they are generated glue — so 4.6's remaining value is the four bodies, and the
  next naming win is the three shared helpers (`FUN_00058a30` = assert-style, 43 calls; the constructor
  and `FUN_000340c0`, 40 each). The modules' 21 `native()` declarations all serialise index 0, the same
  signature as U1, which is the 4.7 thread.
- **2.4/5.4/5.5 done (formats).** `inventory/formats.csv` catalogues 12 extensions with the container
  each one really is, and `docs/formats/` documents four: `.ka`/`.kaw` are Karma 1.0 XML (8 ragdoll
  assets, 141 parts, 133 joints, 256 collision primitives; `.kaw` is an authoring workspace, not
  runtime data); `.sgp`/`.PRO` are INI profiles whose `[Saved Games]` section is the checkpoint index
  (142 entries in the default profile); `.lad` is LIPSinc binary with a documented header and channel
  table but an unestablished payload (U4).
  **The finding that matters is `.sgs`:** a checkpoint is an ordinary UE2 package (130/4) whose
  *exports are the level's actors*, so "the save format" is the engine's tagged-property stream, not a
  bespoke blob. All 9 parse with UELib, 0 objects fail, 19,080 property tags reduce to 1,739 distinct
  schema tags (415 present in all nine) in `inventory/sgs_schema.csv`. The engine side is
  `GameInfo.SavePackage`/`LoadSaveGame` (natives with no serialised index); the game side is
  `AutosaveCheckpoint`, `ACTION_SaveGame` and the `StargateMenus` save UI — which of them drives the
  writer is U5. This also means a rebuilt checkpoint is a rebuilt package, so PLAN 6.2 applies to saves.
- **4.3/4.4 corrected — the "dispatch table" was the CRT initialiser list.** The table reported at
  `0x00598e00` (994 entries, 270 `exec*` names applied, 721 fabricated `Native_*` functions) is the
  static-initialiser array: its 991 entries are strictly increasing (990 of 990 deltas positive,
  spaced 32/96/48/16 bytes) inside one 183 KB block of compiler-generated stubs, and its only code
  reference is an `_initterm` loop that walks the range and calls each entry with no arguments
  rather than indexing it. The build names the real array in its own asserts —
  "GNatives[iNative]!=0" and "iNative<EX_Max" — around `0x0003a083`, where the code does
  `cmp ax,0x1000` and `mov ecx,[eax*4+0x6313e8]`: **GNatives is at `0x006313e8` with 4,096 entries,
  in the uninitialised part of `.data`, so the file contains no slot bytes and no index -> address
  map.** `tools/gnatives.py` now derives that from the assert site and rejects monotonic arrays
  (`inventory/gnatives_candidates.csv`), `inventory/gnatives_map.csv` is deleted, the Ghidra pass no
  longer creates or names dispatch targets, and `inventory/native_names.csv` is declaration-only
  (274 declarations, 273 named indices, no addresses). What this costs: the 270 names are gone and
  4.4 is open again. What it buys: the table's real identity, `EX_Max`, and `FFrame` offsets
  (`+0x84` `iNative`, `+0x90` `Code`) as the entry point for 4.3's remaining work.

- **4.4 done properly, from the running table.** xemu exposes a QEMU-style GDB stub, so the table was
  read out of the emulator instead of the file: `gdb -ex "target remote localhost:1234" -ex "dump binary
  memory runtime.bin 0x6313e8 0x6353e8"` at the main menu. **356 of 4,096 slots are registered, all 356
  addresses distinct and in `.text`, and every one of the 273 declared indices is present** (the 274th,
  index 2, is the editor-only claim in U3); 83 slots have no declarer and are named `Native_<index>`.
  3,740 slots still hold the startup default `0x000404a0`, because natives register with the class that
  declares them and the front end has not loaded the mission classes — a complete map needs a dump after
  a mission load, which is blocked on input automation, not on the table. Sanity check on the operator
  group: `execNotEqual_ObjectObject` (0x000463f0), `execAdd_IntInt` (0x00041a50) and
  `execAndAnd_BoolBool` (0x00041120) all decode an immediate operand through `FFrame` at `[frame+0xc]`
  and push it, which is the UE2 exec prologue for an operand read. Procedure and caveats:
  `docs/XEMU.md`, `docs/NATIVE_VM.md`; the raw dump stays under `$WORK`, only the address map is committed.
- **Script readability is now 100%.** Native resolution order Core -> Engine -> own package took
  unresolved `__NFUN_<n>__` sites 51,289 -> 4,230 -> 1,470 -> **0** (0 tokens) across 2,319 classes.
  Oracle quality moved with it (see `docs/ORACLE_DIFF.md`). `docs/DECOMPILER_GAPS.md` is now an
  empty-table regression check.
  Next: 4.2 signature libraries, then resolving the runtime `GNatives` table (U7).
- **7.1 (partial) — xemu installed, firmware missing.** xemu `0.8.139` (debug AppImage) runs on the
  desktop and starts headless; the dump is staged at `~/nfs/sg1-dump/…` (outside the repo) so the DVD
  path needs no copying. `tools/xemu/boot-test.sh` encodes the debug-kit run (128 MB, serial log) and
  fails fast when firmware is absent. Blocked on the console's own `mcpx_1.0.bin` and flash ROM:
  those are Microsoft firmware, deliberately not downloaded or committed (`docs/XEMU.md`).
- **4.3, second half: the executor, from the pointer that names it.** The routine at `0x0003a030`
  that an earlier pass called "the interpreter" is the `UFunction` binder in `Core\Src\UnClass.cpp`:
  it sets `Func` to `GNatives[iNative]` for a native with an index, to the generic executor
  `0x00048bf0` for a script body, and — for a `FUNC_Native` function with `iNative == 0` — resolves an
  implementation by name (`"int%sexec%s"`), which is the measured answer to the index-less half of U1.
  `0x00048bf0` is `UObject::ProcessInternal`: it walks `FFrame::Code` (`+0x0c`) until `EX_Return`
  (`0x04`) and dispatches each opcode through the same `GNatives`, guards 250 nested script calls, and
  carries the strings `"Infinite script recursion (%i calls) detected"` and `"Not executing, due to
  RPC, or nonprobing function"`. `0x000398e0` is the expression decoder (`"Bad expr token %02x"`),
  most likely `FFrame::Step`. None of the three has a call site — they are reached through
  `UFunction::Func` — which is also why they were un-analysed; see the seeding note below.
- **Native coverage: functions the analyser cannot reach are now seeded from asserts.**
  `tools/ghidra/SeedGaps.java` seeds a function at the nearest `0xCC`-delimited entry before an assert
  site that has no function, keeps it only if the assert lands inside the body it produced, and removes
  the rest. Measured: **64 seeds, 52 kept, 12 rejected; 199 assert sites in no function → 104;
  14,220 → 14,280 functions; 12,339 → 12,347 `FUN_*`** (`docs/NATIVE.md`).
- **A second seed source: data tables.** Some entry points have no call site and no assert — they are
  only ever *dispatched*. `ProcessEvent` is the example: 492 aligned pointers to `0x0004bf20` in
  `.rdata`, zero direct calls in the whole image. `SeedGaps` now also counts, for each block that holds
  no functions, how many dwords point at the same address in executable memory with no function, and
  seeds the addresses with at least 8 referrers (a switch table has distinct targets; a class table
  names one address hundreds of times). Measured: **629 entries seeded, functions 14,280 → 14,912,
  assert-attributed 1,253 → 1,256, assert sites in no function 104 → 102, `FUN_*` 12,347 → 12,971** —
  those entries are real code with no name evidence, so they stay `FUN_*` on purpose.
- **7.1 done, 7.2 started — the prototype boots.** With the debug flash (`bios_debug_4627.bin`) and a
  media-patched XISO (`media=DVD-X2`, `region=NA`) at 128 MB, xemu reaches the engine's main menu with
  `Build: Dec 15 2005 12:02:54`. Retail flash + unpatched disc was refused; the two together work.
  Next: drive the `Continue` flow to load a mission (needs input automation) and settle the
  chapter-list claim in `docs/CUT_CONTENT.md`.
- **4.3 property half (2026-10-24).** Measured with `ExportAt` over 39 addresses and reduced by the new
  `tools/property-map.py`: **15 property classes** (`Property` + the 16 type names, `ByteProperty` 0x70 …
  `MapProperty` 0x74; sizes are the constructors' own literals, not observed allocations) whose class
  objects are built in C++ and never deserialised — `Core.u` has 7 class exports against 643 property
  exports. The type names sit at **reserved name indices 1..16**, assigned explicitly by the initialiser
  at `0x0005c080` (324 more names follow at sparse indices up to 1200; 294 of 325 matched by the tool, the
  1..16 block complete). Two fork notes for 4.8: the only `DecoHack` string in the image
  (`"DecoHack: skipping property %s"`, `0x0003a220`) and the absence of any `GPropPointers`-style global
  array. Re-running the analysis changed exactly one line of `symbols/xbox_functions.csv`
  (`FUN_00256280`, 35070 -> 38716 bytes), so the map is not yet byte-reproducible; the committed file was
  left as it was and only the measured CSVs are new.
- **4.5/4.7 groundwork: the class tables are named (2026-10-24).** `tools/class-table-names.py` follows
  the build's own chain — the 493 registrations that call `FUN_0003af60` (class literal + object size +
  constructor thunk) → the thunk → the constructor that stores the table at `[this]` — and names **466 of
  the 492 tables** in `inventory/xbe_class_table_names.csv`, with each class's **object size**. The sizes
  reproduce every previously measured one (`AlkeshBomber` 3,788, `StargatePlayer` 4,344,
  `StargateVehicle` 3,344, `ByteProperty` 0x70, `ClassProperty` 0x74), so the column is usable for the
  classes nothing else measured (`Actor` 908, `Pawn` 2,408). The 327/165 split in U8 is now explained:
  slots 19/20 are one `ret` in 327 tables and real bodies in the 165 actor-side engine classes. Residuals:
  the 26 unnamed tables and which virtuals 19/20 are. `docs/NATIVE_CLASSES.md`.
- **U8 residual, improved (2026-10-24).** `SeedGaps` now collects several plausible entries per orphan
  assert site (nearest first, one attempt per round) instead of only the nearest fill run, so a site
  whose nearest candidate yields a body cut short by a jump is retried from the fill run before it.
  Measured: **76 seeds, 58 verified, 18 rejected** (was 52 of 64); functions 14,912 -> 14,918;
  assert-attributed 1,256 -> 1,262; sites in no function **199 -> 93** at the end of the pass, 88
  against the committed map, and 102 -> 91 by XbePost's xref count. The 88 that remain are rejected
  candidates, not unseeded ones: the assert sits in a block reached only through a jump, so linear
  flow from the entry never covers it (Engine 46, Core 31, D3DDrv 5). `docs/UNKNOWNS.md` U8.
- **U8 residual, block promotion (2026-10-24).** A third seeding pass for the sites the first two reject:
  disassemble the gap before the site, walk back to the head of the block it sits in, create a function
  there, keep it only if the body contains the site. Measured: **26 block heads promoted**, sites in no
  function **93 -> 50** at the end of the pass (**88 -> 49** against the committed map, **102 -> 50** by
  XbePost's xref count), functions 14,918 -> **14,944**, assert-attributed 1,262 -> **1,288**. The promoted
  bodies are not entries and are recorded as such in `inventory/xbe_block_seeds.csv` (26 rows, each still
  containing its evidence site) so nothing downstream treats them as function starts. The 49 that remain
  (Engine 25, Core 21) are regions where the gap does not disassemble into an instruction covering the
  site at all — clustered, e.g. `0x1f850f`/`0x1f852b`/`0x1f8561` — and need an independent decode.
- **U10 closed: the tagged property record (2026-10-24).** The reader at `0x0003ceb0` gives the tag
  order outright: int32 **Name** (index 0 ends the property list), one **Type** byte (low nibble =
  property type 1..16, bits 4–6 = size class, bit 7 = "array index follows"), int32 **StructName** only
  for a struct, **Size** implied by the class (1/2/4/12/16) or read as 1/2/4 bytes, compact
  **ArrayIndex** when bit 7 is set and the type is not bool, then the value. Two independent sources
  agree: the build's own class objects return the type nibble through `vtable+0xcc` (`mov al,5; ret` for
  `ClassProperty`), and UELib's `DeserializeTagUE1` implements the same masks and order for the parser
  that reads all 9 checkpoints and 19,080 tags with no failures. `docs/NATIVE_PROPERTIES.md` §6. Still
  open there was the **writer** branch. That is measured too (`0x0003cd70`): same field order (name
  `+4`, struct name `+8`, size `+0xc`, array index `+0x10`), the type byte OR-ed with the size class
  (1/2/4/12/16 implied; `<0x100` one byte, `<=0x10000` two, else four) and with `0x80` when the array
  index is non-zero or a bool's value is true, and **size measured** by serializing the value through a
  scratch archive. Remaining for 6.2: the caller that writes the tag struct into the archive byte by
  byte.
- **U8's seeding thread is closed out (2026-10-24).** Three passes (entries, data tables, block heads)
  took assert sites in no function from 199 to 50; the residuals are recorded in U8 and the promoted
  blocks listed in `inventory/xbe_block_seeds.csv`.

- **U8's leftover assert sites are resolved, and two of them were never call sites (2026-10-24).** A
  fourth seeding source (`SeedGaps.seedFromGaps`) decodes the gap a leftover site sits in: candidate
  entries are the byte after a 0xCC fill run, and an entry is accepted only when three things are
  measured -- the body contains the site, the instruction over the site is a `PUSH` whose immediate is
  the file string the row names, and the body ends at a terminator. Measured: **sites in no function
  49 -> 6** (5 by `XbePost`'s own count), **functions 14,944 -> 14,956**, **assert-attributed 1,288 ->
  1,300**, `FUN_*` unchanged at 12,971 (every new entry is named from its assert). The 47 code sites
  had been rejected by `looksLikePrologue`: none of the 14 entries behind them opens with a byte the
  list has, because they open with an argument load. The first pass keeps that list so its measured
  numbers do not move; the recovered entries are listed in `inventory/xbe_gap_seeds.csv`. The two that
  remain plus two `push` sites are classified by the new `tools/assert-residual.py` (`make
  assert-residual`) into `inventory/xbe_assert_residual.csv`: `0x00592cb6`/`0x00592cbe` are dwords in
  an 8-byte record table in `.rdata` whose value equals a file string address, and the byte before a
  dword has to be `0x68` for it to be an operand at all. Cost, recorded rather than hidden:
  `FUN_00256280`'s body is not reproducible: 35,070 bytes in four runs and 38,716 in two others of
  identical code, the difference being exactly the 3,646 addresses (0.09% of the image) that leave no
  function in the smaller variant. Recorded in `docs/UNKNOWNS.md` U8 as an open reproducibility
  question, not as a property of this pass. **Also a readability
  win for the engine modules:** `Engine` gains 7 named functions and `Core` 3, all of them bodies that
  were previously only reachable as part of a bigger function.

- **U6: two of the three shared helpers are named (2026-10-24).** `FUN_00058a30` -> `appAssertFailed`
  and `FUN_000340c0` -> `appMallocBeforeInit`, both named from the wide strings they log and recorded in
  `symbols/xbe_overrides.csv` with that evidence. The first logs
  `"Assertion failed: %s [File:%s] [Line: %i]"` from **2,432 direct call sites**, and the body now
  settles what U6 called unmeasured: it widens the expression and the file name through `0x00058740`,
  formats through `0x000574d0` (`vsnprintf` into a `0x1000` stack buffer, log-suppression globals, then
  the output device's vtable) and **returns** -- no `int3`, no abort, so `check`/`verify` logs and
  continues. The second logs `"Called %s before memory init"` with `"appMalloc"` and returns 0 from
  **961 pointer-mediated sites** (`call [reg]`), i.e. the allocation entry point before `GMalloc`. So the
  modules' `FUN_*` soup is now one helper shorter each. Measured: `FUN_*` 12,971 -> 12,969, map symbols
  unchanged at 15,422, `docs/NATIVE.md` regenerated (217 names from Ghidra's own analysis). `FUN_0003af60`
  deliberately stays `FUN_*` -- it installs the `Class` table (`0x004d4870`, object size 1312) and its 493
  callers are the ucpp registrations, but what to call it needs the caller-side evidence 4.7 collects
  (`docs/CONVENTIONS.md`: hypothesis in the comment, not the symbol).

- **CI is in, and its first run found two defects of mine (2026-10-24).** `.github/workflows/ci.yml` runs
  `make ci` -> `tools/ci.py` on every push and pull request: committed CSV shapes, the function and symbol
  maps, that `symbols/xbox.map` reproduces byte for byte from `symbols/xbox_functions.csv` +
  `inventory/xbe_class_table_names.csv`, and that `docs/NATIVE.md` and
  `inventory/xbe_functions_by_module.csv` reproduce from the committed inputs. `docs/CI.md` lists what is
  deliberately not run: every check that needs the dump, because `AGENTS.md` forbids CI from downloading
  the game. The first run failed twice, both times on this repository's own recent work: the two rows
  PR #17 added to `symbols/xbe_overrides.csv` had 11 and 12 fields against a 10-column header (unquoted
  commas in the evidence column -- `XbePost` reads only columns 0-1, so the names had applied anyway), and
  `inventory/xbe_functions_by_module.csv` was stale because that PR regenerated `docs/NATIVE.md` through
  `-o /tmp`. Both are fixed here, and the now-parsed override rows correct the counts PR #17 quoted: **41**
  override-attributed names and **215** from Ghidra's own analysis, not 39 and 217.


### Execution notes (2026-10-24, the XBE header)

- **The reader was mislabelling the certificate as the entry point.** Offset `0x118` is the certificate
  (`0x10184`, verified by the `0x1EC`-byte certificate and its UTF-16 `Stargate SG-1` title), not the
  entry. `tools/ghidra/make-spec.py` had been writing `entry=0x10184` into every Ghidra project spec.
  Fixed: `Xbe.certificate` / `Xbe.entry_field` / `Xbe.entry = None`, and `docs/XBE_LAYOUT.md` records the
  verified header plus all **15** sections (an earlier note said 5; the seven library sections are
  separate mappings after `.text`). The real entry field at `0x128` decrypts outside every section, so it
  is UNKNOWNS U12 now rather than a guess.

### Phase 8 — Documentation, release, handoff (ongoing)

- [ ] 8.1 `docs/BUILDING.md`: end-to-end reproduction, toolchain versions, expected hashes.
- [x] 8.2 `docs/ARCHITECTURE.md`: engine fork overview, VM, script-to-native mapping, level flow.
      Done: the three layers and their sizes, the address space (code, 462 KB of class tables, the name table) with the
      measured finding that code is ordered per translation unit rather than per module, the object model down to the
      five addresses that form the script/native boundary, the property/tag format that doubles as the save format, the
      content layer with the mission/multiplayer split, and an explicit "not measured" for the init call graph. §8 maps
      each claim to the check that reproduces it.
- [~] 8.3 Per-system write-ups: AI (`AIScript`/`AIMsgManager`), vehicles, weapons, mission objectives, checkpoints,
      UI, audio (LIPSinc). **Checkpoints done** (`docs/systems/CHECKPOINTS.md`): the nine `.sgs` files hold 1,739 property
      rows over 96 classes, 40% of them object references, dominated by level furniture (`Mover` 2,227 instances,
      `IntelligentSpawner` 1,860, `SVehicleTrigger` 1,115) and mid-firefight objects (`FragGrenade` 447) — so a
      checkpoint is a snapshot of the live level's actors, not a progress record. 415 rows are the `UObject`/`AActor`
      baseline every actor carries; 533 appear in exactly one file, which is where mission-specific state lives.
      **AI done** (`docs/systems/AI.md`): 45 controller classes carrying 165 states against 758 functions
      (`BotEnemy` alone 41 states), squad talk limited to three messages (`AIMSG_Cloak`, `ConcentrateFire`,
      `TakeCover`), 11 of 12 `SGOrder*` classes adding two functions or fewer, and — the consequential part — **no AI
      class is placed in any map**: bots appear only through `IntelligentSpawner` and checkpoint state (`BotSG` 890
      instances), so "which bots does this mission use" is not answerable from the `.unr`.
      **Vehicles done** (`docs/systems/VEHICLES.md`): `StargateVehicles` is 142 classes but the `Vehicle` tree is
      44 with just 17 states and 260 functions, 13 of them empty shells, and the package is mostly mounted weaponry
      (13 `StargateWeapon`, 12 `StargateAmmo`, 10 `StargateDamageType`, 10 `StargateProjectile`) rather than vehicles.
      Unlike AI, six vehicle classes *are* placed in maps (`StaffCannon` 21, `DeathGlider` 12, `Hummvee` 9), so 5.2
      can answer what a mission fields.
      **Weapons done** (`docs/systems/WEAPONS.md`): 227 classes across four trees (`StargateWeapon` 68,
      `StargateAmmo` 71, `StargateDamageType` 36, `StargateProjectile` 52) with **zero native functions** — the one
      fully-scripted system, hence the best rebuild target; 29 of 68 weapons are two-function-or-less variants, and the
      `X`/`XAmmo`/`XDamage` naming holds for only 17 of 68, so a rebuild must not generate classes from the pattern.
      No weapon is placed in any map; they persist through checkpoints (`FragGrenade` 447, `P90Prototype` 432).
      **Mission objectives done** (`docs/systems/OBJECTIVES.md`): two families that do not touch, and the campaign
      ships the engine's — `Engine.MissionObjective` has no subclasses but is placed **261 times**, while the fork's
      `GameObjective` family (10 classes, 22 functions, 4 empty stubs) is placed **zero** times in all 73 maps. Mission
      logic is level script: 22 `ACTION_*` latent scripted actions with 83 properties, actually placed (`ACTION_Talk`
      719, `ACTION_PlaySGSound` 121). `StoryModeGame` (51 functions, 3 states) owns the rules; `ObjectivesPanel`
      renders state. Consequence: 5.2 is on the critical path for anything playable. `chapters.csv`: 37 entries, 30 on
      disk.
      **UI done** (`docs/systems/UI.md`): 347 classes / 2,137 functions across `StargateMenus` (98 classes, 334
      functions, **zero natives**), the stock `GUI`+`UWindow` (226) and the game-side HUDs — and it corrects the fork
      count: `SGListBox` and seven subclasses live *inside* `GUI`, so 503 insertion points is a lower bound and package
      name is not a stock/fork discriminator. Two full HUDs (`StargateHUD` 57/101, `ReplicatorHUD` 29/69) mean the
      Replicator mode is first-class; `SGMenuInteraction`'s 294 properties are the largest config blob in the fork.
      **Audio done** (`docs/systems/AUDIO.md`): three systems, not one — voice packs are script-only
      (`StargateVoicePack` 34/24, `VoiceSelector` 10/21) with lip-sync as 18 LIPSinc files (6.5 MiB, `.lad` layout is
      U4); music is the only audio with natives (`IMusicActor` 3) and 1,923 WAVs / 348 MiB in 16 groups with two
      scoring shapes (mission cue sets vs `M07A`'s 28 numbered cues); speech is 1,364 lines / 150.8 MiB over 207 scenes
      and 30 speakers, 545 of them untagged. **SFX remain unindexed** — 55 per-character/per-vehicle `.uax` packages,
      blocked on the same package reader as 5.2/4.7. 8.3 is complete: checkpoints, AI, vehicles, weapons, objectives, UI,
      audio.
      Note: `FORK_SURFACE.md` should gain the GUI example when next touched.
- [~] 8.4 Milestone releases: (a) inventory+tooling, (b) scripts decompiled, (c) scripts compile, (d) bytecode match,
      (e) playable rebuild, (f) asset-complete. **Milestone A is met and releasable**: `docs/RELEASES.md` defines all six
      with exit criteria and measured status, and `tools/release-notes.py` generates the release body from the committed
      artifacts (so two releases diff and a stale figure fails rather than misleads). Cutting the tag is a maintainer
      action after merge; B stays partial because the oracle only agrees on 204 of 291 classes, and C–F have not
      started.
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
