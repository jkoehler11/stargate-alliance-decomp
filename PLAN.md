# Stargate SG-1: The Alliance (Xbox, 2005-12-15 prototype) — Decompilation Plan

Status: **phase 0-2 done; phase 3 script tree complete, compile loop not started; phase 4 under way,
4.4 re-done against the runtime dispatch table after the earlier correction, 4.3 executor, binder,
expression decoder and `ProcessEvent` measured; 4.3's property type space, class roster and field
layout measured, the tagged tag record open)**
(last run 2026-11-06). This is the working task list for the
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
- [x] 0.5 Pin toolchain versions in `tools/versions.lock` (Ghidra, ghidra-xbe, UELib commit,
      UE Explorer version, umodel build, xemu version, .NET SDK, Mono/Wine).
      Done, and **checkable**: every line is now a constraint (`>=3.11`, `==12.2`, `~=5.0`) rather than prose, and
      `tools/versions-check.py` (`make versions`, `--strict`, `--check` in `make ci`) compares it with the machine.
      On the dump-free host it reports `python` and `as` matching and the native-side tools absent; `--strict`
      fails on an installed tool that violates its pin and stays quiet about absent ones — absence is a
      stage not yet run, not a broken pin. Eleven entries are pinned; three say out loud why they are not:
      `xbox-sigs` is `none` (4.2 is blocked on a signature database this repo may not carry), `umodel` and `xemu` are
      `UNPINNED` because 5.1 has not been run and the emulator build that booted was never recorded — which is why its
      captures are not comparable. `uexplorer` and `wine` are gone from the lock: neither is used by any stage.
      Verified with two manufactured failures: a violated constraint fails `--strict`, and an `UNPINNED` line without
      a reason fails the structural check.
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
      **Blocked on a physical action, not on work**: this needs a second copy of the dump and read-only media, which
      whoever holds the dump does. Everything in Phases 2–7 has run against copies under `$WORK` regardless.
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
- [x] 2.6 Enumerate XBE imports (D3D8, XAPI, XNet, XOnline) and the D3D8 device caps used — informs the
      renderer-side natives we must understand.
      **Caps measured 2026-11-10 (runtime).** The create call is `0x002b2dcd` into the statically linked D3D8
      (`0x44f040`), with `BehaviorFlags = 0x40` -- hardware vertex processing, a literal `push 0x40` in the body --
      and the present-params block at `[esi+0x4708]` read out of a live guest at that call: 640x480, back-buffer
      format 30 (32-bit), depth format 42 (32-bit), one back buffer, `SwapEffect` 1, auto depth-stencil on,
      `Flags` 0x121, refresh 0, presentation interval 1. The module's own log lines agree
      (`Using back-buffer format 30(32-bit)`, `Using depth-buffer format 42(32-bit)`,
      `Best-match display mode: 640x480x32@60`). U11 answered; the instrument is `tools/xemu/bp-args.py`.
      **Reported caps measured too**: the render device caches the runtime's 53-dword `D3DCAPS8` at `this+0x419c`
      and the post-create test at `0x002b3008` reads `VertexProcessingCaps` from it -- `0xbb`, MATERIALSOURCE7 set,
      so the `hardware` decision is the device's own report and not only the request (4 blend stages, 8 lights,
      4096x4096 textures, 16-bit indices, 96 VS constants). The fill site is `GetDeviceCaps(0, HAL,
      this+0x419c)` at `0x002b1b8a` into `0x004523a0`, the only immediate `0x419c` in `.text`, and six caps are
      logged by name straight after it. `tools/xemu/d3d-caps.py` decodes the window and re-finds the call
      (`--fill-site`); `docs/systems/RENDERER.md` carries the table.
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
      finds a block for 2,209 of 2,319 classes and one that accounts for itself for 1,664 (71.8%) — the previous
      512-byte scan window excluded every base class, whose record is longer than that. 15,517 of 15,521 class-level
      properties render to UnrealScript (99.97%) — 484 arrays (1,502 elements), 219 nested structs — and 1,313
      `Begin Object` subobjects carry another 15,790 of 15,798; enum element names resolve for 771 of 1,052 byte
      values from the shipped `enum` declarations, and `make defaults-tree` merges the blocks into the extracted
      source so one tree carries both halves. **The decompiler gaps are now counted** (`docs/DECOMPILER.md`,
      `inventory/decompiler_gaps.csv`): against the shipped source, `state` blocks are complete (457 declared, 512
      emitted, no class loses one) and so are latent declarations (10 vs 13); **`replication`** is 63 of 142 blocks and
      that is the format's limit, not the decompiler's — instrumenting `FormatReplication` shows every one of the 79
      classes that lose a block has **`net=0`**: not one property carries `PropertyFlag.Net`, so the class record holds
      no group to reconstruct. They declare `persistent` groups, which are compile-time-only.
      Remaining here: 4 values (one `array<SpeechReplacement>` that does not
      tile, three delegate tags), 545 withheld blocks (whether the 535 that a type-first ranking would promote are
      real is measured but not settled — see the doc's "Not measured"), then `replication` blocks, state code and
      labels, latent calls, operator precedence.
- [~] 3.6 Set up the compile loop: matching `ucc` (UT2004 / UE2 Runtime under Wine) with the engine's
      flags, so recompiled script can be compared byte-for-byte.
      **Input problem solved, tool still missing**: the packages ship their own source, and `make extract-script`
      writes all 2,319 classes to `$WORK/source-tree` (6,113,909 bytes, `inventory/source_tree_summary.csv`,
      `docs/PACKAGE_SCRIPT_TEXT.md`). **The pipeline is scripted** (`tools/build.sh`, `docs/REBUILD.md`, `make rebuild`):
      eight stages, five of which run today (writer relay 18/18, rebuild identity 18/18, source tree, defaults merged
      into it, and package check on the hybrid target: 17 of 17 records reproduced) and three SKIP with their reason
      (compile, repack, boot). But **only 15 classes carry
      `defaultproperties`** — the defaults live in the
      package's default-properties data, not in the text — so the loop needs the text *and* a defaults reader, and
      `ucc` under Wine is still absent. Function counts match `inventory/classes.csv` for 1,789 of 2,319 classes;
      the disagreements are recorded, not explained. **The defaults half of the input now exists**: the packages
      yield 15,491 rendered properties over 1,666 classes and `make defaults-tree` merges them into the extracted
      tree, so the compile loop is blocked on `ucc` alone, not on its inputs.
      **And `ucc` turns out not to be the answer** (2026-11-09, `docs/COMPILER.md`, `tools/ue2rt-probe.py`): Epic's
      UE2 Runtime build 2226 runs under Wine, compiles a scratch package, and writes **version 126 / licensee 0** --
      but pointed at this build's 130/4 packages it stops in `SerializeExpr` (`Bad expr token 03` on `Core.Object`)
      before parsing any script. The gap is the token grammar, not a version guard, so a stock compiler from any
      public UE2.5 release (129 or earlier) cannot be the tool, and patching a constant cannot make it one. What 3.6
      is missing is therefore not "a `ucc`" but the fork's own compiler -- the toolchain that wrote these packages --
      or a front end written here against `docs/BYTECODE.md`. Open as U17.
- [~] 3.7 Disassembler + comparator: disassemble original package functions and our recompiled ones;
      **the serializer is identified** (`docs/NATIVE_VM.md`, `inventory/class_serialize_slots.csv`): slot 11 of every
      class table is `Serialize`, `UStruct`'s is at `0x000392b0`, and its decompiled body shows the script stored as
      a **size plus bytes with a `'G'` compression marker**, then **walked token by token** and validated with
      "Script serialization mismatch: Got %i, expected %i". **The disassembler half has landed**
      (`docs/BYTECODE.md`, `make opcode-table`, `inventory/opcode_table.csv`, `tools/uscript-dis.py`): the walker's
      dispatch is a 72-byte opcode→group map at `0x39ec0` over a 31-entry jump table at `0x39e44`, every group's
      operand stream is measured (fixed bytes, nested tokens, `0x16` parameter lists, NUL-terminated strings, label
      tables), 35 of 256 opcodes are refused, and the table is cross-checked against the pinned UELib token map.
      **The locator is solved** (U13): the script starts **16 bytes past `FriendlyName`**, measured three ways — the
      payload floor (25 bytes, with spikes at 26 = 16+3+7 and 28 = 16+3+9), the walk's depth from that offset (3,328
      reach the epilogue and most others stop 16–255 bytes in), and the dword at +12 not being a size (larger than
      the script in 9,298 payloads, equal in 2,052, never smaller). What blocks long scripts now is the **opcode
      table's coverage** — now largely closed: five groups' operand width was four fixed bytes and is the archive's
      compact index, which takes the corpus from 3,444 to 11,602 scripts walking (99.3%), so *long* shipped scripts
      are disassembled end
      to end, and per-function match grades (L0 present / L1 compiles / L2 opcode-sequence ≥98% / L3 byte-exact) need
      the comparator's compiler.
- [ ] 3.8 **The comparator exists; only the oracle is missing.** `tools/uscript-dis.py --compare A B` scores two
      packages function by function (byte-identical = L3, opcode-identical = L2, plus a similarity ratio), and it is
      validated the only way it can be without a build: a package against itself gives 3,352 of 3,821 shared functions
      identical (the rest have no script), and a one-byte mutation inside an operand moves L3 to 3,351 while L2 stays
      at 3,352. Chase L2→L3 on the small packages first (`Core`, `Fire`,
      `UnrealEd`, `IpDrv`, `AmbientCreatures`),
      then `Engine`, then the Stargate packages. Track a running scoreboard in `docs/MATCHING.md`.
- [x] 3.9 Handle the ucpp-native classes (`Pawn`, `ShadowProjector`, others discovered in 2.2): mark their script
      bodies as "native — see src/Native", and cross-link to Phase 4 output.
      **The declaration side is reconciled** (`docs/NATIVE_CLASSES.md`): 96 classes, 681 `native function`
      declarations in the shipped text (256 carrying an explicit index) and 97 operator declarations, against 685
      function records in `inventory/natives.csv` — **683 matched (99.7%) and 0 index disagreements**, so every
      `native(N)` agrees with its record. 11 residual rows over 7 classes (`Core.Object` 2, `Weapon` 2,
      `StargateWeapon` 2, `HitEffectManager` 2, `ScoreBoard`, `UPreviewMapListClient`, `UPreviewSkinnedButton` 1
      each) **each carry a verdict from the image's
      own binder pool now** (`inventory/native_residual.csv`): 2 records the text lacks (both the editor package
      `UPreview`), 2
      records the image binds that the record table lacks (`Core.Object.Divide`/`Round`), 7 declarations nothing
      binds. **The token side is closed per
      record** (`inventory/native_tokens.csv`, `docs/NATIVE_CLASSES.md` *Every token has a basis*): of the 274 records
      that carry a token, 166 spell it in their class's text, 107 are operator signatures the compiler expands, 0 come
      from a declaration that spelled no index (`Core.Object`'s "17" are all operator records) and 1 is an artefact of
      the editor package `UPreview`. **The bodies are marked and cross-linked** (`tools/native-mark.py`,
      `inventory/native_bodies.csv`, `docs/NATIVE_CLASSES.md` *Every body, marked*): 681 of 681 `native function`
      declarations carry a `// native - see src/Native/<Package>/<Class>.cpp` line naming their token, binder and body
      address, and 660 of them resolve to a body in the image (271 of those 660 bodies are absent from the function
      census, which no caller list could supply). **Every `operator` declaration is marked too** (97 of 97 compose a
      record, 85 carry the image's binder literal, all 97 a body address) **and so is every `native event`** — 11
      declarations in 7 classes that every counting pass in the repo matched past, because it looks for
      `native … function`; all 11 carry the binder literal and a body address. The pass now writes **789 marker
      lines in 2,319 class files** (681 functions + 97 operators + 11 events, 0 unmarked, byte-identical on a second
      pass), so every declaration the shipped text carries is cross-linked to the C++ that implements it. This closes
      3.9: what is left is the C++ at the target path, which is Phase 4's reconstruction and what the marker names.
- [~] 3.10 Write class-level documentation for the game systems: mission objective graph, interaction system
      (`SGInteraction`), AI scripts, vehicle driver/passenger weapon structs, checkpoint saves.
      Objectives done — `docs/systems/mission-objectives.md`: the model, the one-event state machine
      (activate, then complete), child aggregation, the panel, placement (61 of 73 maps, exactly one per
      map), and that no checkpoint schema carries objective state.
      Interactions done — `docs/systems/interactions.md`: `SGInteraction` as an inline value object on
      `Actor.InteractionData` (621 of them across 61 maps), the prompt/use path through `StargatePlayer`
      and `CommandInterface`, and the separate Epic `Interaction`/`InteractionMaster` client framework.
      Vehicles done — `docs/systems/VEHICLES.md`: the seat/turret wiring is a default property value rather than
      behaviour, so `tools/vehicle-seats.py` reads `DriverWeapons`/`PassengerWeapons` out of the emitted defaults
      tree into `inventory/vehicle_seats.csv` (25 slots over 11 classes, and the page's table is generated from it).
      The other two systems (AI scripts, checkpoint saves) are open.
- [x] 4.1 Install Ghidra + `ghidra-xbe`; load `default.xbe` at base `0x00010000`; verify section mapping and imports.
- [~] 4.2 Apply Xbox SDK / MSVC 7.1 signature libraries (xbox-includes, FLIRT-style sigs) to auto-name CRT,
      XAPI, D3D8, XNet, XOnline, zlib and math routines.
      **Blocked on data the repository may not carry** (U9): the signature databases are built from
      Microsoft SDK/toolchain output. The part that needs no external data is done — the XBE's own
      library table names the statically linked libraries and versions — and a locally supplied
      database is the unblock, recorded in `tools/versions.lock` rather than committed.
      **The static half is closed by measurement** (`docs/LIBRARIES.md`, `make library-bodies`,
      `inventory/library_bodies.csv`): the seven library sections hold 3,109 bodies / 565,157 bytes and
      **none of them names a source file** -- 169 reference any string at all, the sections' whole
      vocabulary is 363 strings, and RTTI carries four descriptors (all CRT exception classes). So an
      engine-module-style naming pass has nothing to read here; U9's signature data is the only route, and
      the section table is the population a partial database would be scored against.
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
      **The dispatch table is closed as complete (2026-11-10)**: a second dump taken with `M00` loaded holds the
      same 356 slots with the same bodies as the menu dump — 0 gained, 0 lost, 0 replaced — so registration
      finishes at startup and a mission adds nothing (`tools/xemu/gnatives-mission.sh`, `make gnatives-mission`,
      `inventory/gnatives_mission.csv`).
      **The class tree is now a second instrument, and it does not name the 26 (2026-11-10)**: inverting
      the `call_before_store` column of `inventory/class_constructors.csv` gives every table the set of
      classes whose constructor calls it, and that set equals the class's direct subclasses in
      `inventory/classes.csv` for **375 of the 391** named tables that have a child at all (15 disagree,
      76 are leaves with no subclass in the image) — `tools/class-tree-edges.py`,
      `docs/NATIVE_CLASS_TREE.md`. Of U8's 26 unnamed tables it names **3** by the tree
      (`0x004d1468` `Property`, `0x004fc4f0` `LevelBase`, `0x004c9348` `Player`, each read off a
      partly-unnamed child set) and 23 stay invisible because nothing calls them. The registration-site
      join fails for a measured reason rather than a missing link: **24 of the 27 sites the class map
leaves unnamed push one shared pointer** (`0x00034650`, a thunk to `0x00062ce0`), and that body is a
      nine-byte **default constructor** (`MOV EAX,ECX; MOV [EAX],0x004dd2a8; RET`) which stores one fixed
      dword and names no class, so the site cannot identify a per-class table; one more site pushes it
      behind a tying candidate, two resolve to a neighbouring `WaterTexture` registration (`Texture`,
      `FractalTexture`) and `FireTexture`'s resolves to a constructor that installs `IceTexture`'s table
      -- **not one of the 27 lands on its own class's table**. The same test validates the rest (459 of
      493 sites: the pushed constructor installs the site's own class table) -- `tools/ctor-argument.py`,
      `inventory/registration_ctor_arguments.csv`. U8's 26 need an argument that is not the constructor -- and
      the site's fourth/fifth pushes are now read: they are the class and outer *strings*, with no table at
      any of the 493 sites (`docs/NATIVE_CLASS_TREE.md`, `make class-table-residual`) -- so the route left is
      a registration-time breakpoint, and that is now the
      written next step rather than a guess.
      The census of those dispatch sites is measured (`make vcall-dispatch`, 106 in `.text`) and their
      runtime reading is started, not finished: see the execution note above.
- [x] 4.4 Auto-generate native symbol names from the packages: each `native(index)` declaration in `inventory/classes.csv`
      maps to the VM native table slot → script this into a Ghidra script that renames the target functions.
      *This is the single highest-leverage task in the project — it names hundreds of engine functions for free.*
      Done from a runtime dump of `GNatives` (16 KB over the xemu GDB stub): **273 of 273 declared indices
      registered**, 356 of 4,096 slots live at the main menu, 356 distinct addresses, 83 slot-only slots named
      `Native_<index>`. Map: `inventory/gnatives_slots.csv` → `symbols/xbe_dispatch.csv` → `XbePost` overrides.
      The table is incomplete until a mission load (U7); the first attempt at this task was withdrawn because it
      had been measured against the CRT initialiser list — see the correction note below and `docs/NATIVE_VM.md`.
      **The map no longer depends on the boot (2026-11-09):** the `{name, body}` registration records in `.data` give
      all 883 names an address statically (`tools/native-table.py`, `inventory/native_targets.csv`), 261 of 261
      agreeing with this runtime dump and 769 of 797 declarations covered — and 275 of those bodies are function
      entries the analysis does not have, so the same table seeds `inventory/function_modules.csv`.
- [x] 4.5 Split the XBE into per-module Ghidra projects using the assert-path map from 2.3 (Engine, Core, IpDrv,
      Gui, D3DDrv, XboxDrv, XboxAudio, XboxLive, Gameplay, Fire, stargategame, stargatevehicles).
      **The split is per function, not per range — measured.** `inventory/function_modules.csv`
      (`make module-split`, `tools/module-split.py`, `--check` in `make ci`) attributes all 14,957 functions to a module
      with the same rule the rollup uses, and reconciles against `inventory/xbe_functions_by_module.csv` so the two
      cannot drift. The result refutes the wording above: `Engine`'s 586 functions occupy **558 separate runs** over a
      2.5 MB span (11.7% coverage), `Core`'s 574 occupy 570 runs over 3.2 MB (11.9%), and mean functions-per-run is
      1.0–1.1 for every module. There is no module range to split; a module is a set of addresses, so
      `--lists $WORK/export` emits `<module>.txt` for `ExportAt.java @<file>` and per-module work is one exporter pass
      per list. **That input is now measured**: the lists are the union of the assert rule and the locality rule
      (`inventory/function_module_attribution.csv`), 6,063 of 14,957 functions, and `inventory/module_export_plan.csv`
      (`make module-split`) prices them per module at **5,826 contiguous runs, mean 1.04 functions per run** — 3,125
      runs for `Core` alone. So the exporter pass is per *address list*, not per project: there is no region to cut,
      and a per-module Ghidra project would save nothing over one project plus a list (`docs/MODULE_SPLIT.md`).
      **The pass is paid (2026-11-10).** `tools/ghidra/export-module.sh` imports once and runs one round per module
      list: **12 rounds for all 6,063 bodies in 268 s**, 6,063 exported at their own address (0 inside, 0 missing),
      4,625 still `FUN_*`, 2 aborted decompiles, 19.5 MB of C under `$WORK` (`inventory/module_export_rounds.csv`).
      So the split's deliverable is complete as *lists plus a driver* — there is no per-module project to build, and
      4.2/4.9 own the naming that remains.
- [~] 4.6 Decompile + document `stargategame` and `stargatevehicles` first (small, 10 files, highest game-specific
      value: DeathGlider, AlkeshBomber, GoauldDrone, StargateCopter, Teltak, StargateHover, vehicle physics).
      All 43 functions decompiled and triaged: 40 are ucpp class-registration stubs, named from the build's own
class literals (`symbols/xbe_overrides.csv`); 3 are real bodies, documented in `docs/NATIVE_NOTES.md`.
      **The fortieth stub was read, not decompiled (2026-11-10).** `0x003107b0` is
`ucpp_Register_GoauldDrone`, object size 2,532: the constructor call does carry `L"GoauldDrone"`, at
`0x53b42a`, but the decompiler printed that argument as `&DAT_0053b42a` and a text reader took the label
for "no literal". `tools/ucpp-registrations.py --xbe` resolves the class name from the call site's own
pushed immediates when the decompile renders fewer than two, and the same read gives the three game
bodies their assert seams (`Mesh` at `StargateCar.cpp:190`, `DOFResourceRefCount > 0` at
`SGCameraEffects.cpp:140`, `SharedPixelShaderDOFMaterial == NULL && BlurTargets.Num() == 0` at line 119)
around the DOF counter at `0x007b93f8`. The extractor now accepts both spellings of the assert helper:
pinned to `FUN_00058a30` it named **0** of the 40 stubs once `0x00058a30` was named `appAssertFailed`,
and `--merge`, required when the override file already holds rows the run does not produce, keeps its
other 38 rows.
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
      memory, compiler behaviour). **The export's population is now classified** (`docs/MODULE_CENSUS.md`,
      `inventory/module_export_census.csv`, `make module-census`): all 6,063 attributed bodies are function entries in
      `.text`, 1,428 sit in a vtable or data table, **150 are not analysis targets** (133 `ret`-only slots, 17 thunks),
      1,310 declare their own source path and **837 of the 848 whose path is a `.cpp` name the module they were
      attributed to** — so the lists are the right populations and the work list is **5,913 bodies**. `Core` is not
      mostly library *by evidence*: 564 of its 3,238 declare a source path and 2,110 carry no literal at all, so the
      library population is located rather than named (4.2's signatures, U9).
      **Renderer half now read** (`docs/systems/RENDERER.md`, `inventory/d3ddrv_bodies.csv`, `make body-read`):
      the 22 bodies that name their own file are **five engine translation units** — `D3DRenderDevice`,
      `D3DRenderInterface`, `D3DRenderState`, `D3DResource`, `D3DMaterialState` — built from
      `...\GameXBOX\D3DDrv\Src\`, and no body in the module carries a Stargate-specific name. The module
      **calls into no body of `XboxDrv`** and names no asset, and it reaches the device through **20 vtable
      offsets <= `0xbc`** in 11 bodies. Its own strings narrate a stock D3D8 device creation path (back-buffer
      and depth-buffer format probing, `hardware`/`software`/`mixed` vertex processing, a
      `D3DVTXPCAPS_MATERIALSOURCE7` test) plus a vertex-shader lighting path (`VS_Lights_MAX`,
      `Engine_UnPixelShaderMaterial`). So the fork's footprint here is **decisions, not classes**; which
      decisions differ from stock still needs a stock image of this compiler.
      **Console half also read** (`docs/systems/XBOX_DRIVER.md`, `inventory/xboxdrv_bodies.csv`): `XboxDrv` is
      **22 bodies, 4,349 bytes** -- `XboxClient.cpp` and `XboxViewport.cpp`, the engine's viewport manager and
      viewport, not a renderer. `System/Default.ini` names the pair (`ViewportManager=XboxDrv.XboxClient`,
      `RenderDevice=D3DDrv.D3DRenderDevice`), the viewport body at `0x0032d6c0` is what creates the `RenDev`
      (`Couldn't create render device!`), `0x0032d860` reads the pad (`ButtonIndex < 8`) and `0x0032dff0` is the
      XDK's `XBUtil_FindMediaFile` -- so the console renderer is `D3DDrv` plus a 22-body platform shim, and the
      D3D8 functions themselves stay in the unnamed 302-function `D3D` section (U9/U11). 4.8's native half is
      now read: what remains is naming (4.2) and a stock image to diff against.
      **Naming is now measured and bounded** (`docs/NAMING.md`, `make body-name`,
      `inventory/body_names.csv`): of the corpus's unnamed bodies **27** name themselves from a
      `Class::Method` literal in their own log text (and are now in `symbols/xbe_overrides.csv` and the
      symbol map), 23 name only a source file, and 4,575 carry no naming evidence at all; the linker's
      19 byte-identical groups hold 330 unnamed bodies and none of them has a named member, so twin
      propagation adds nothing yet. The name source the image offers is therefore exhausted: the rest is
      U9's supplied database, not another pass.
- [ ] 4.9 **Re-scoped: the committable half is documentation, and it exists.** Writing reconstructed C into
      `src/Native/` would put decompiled game code in the repository, which the hard rule in `AGENTS.md` forbids — so
      the deliverable is the notes, not the C: `docs/NATIVE_NOTES.md` per subsystem, with the function names, the
      addresses and what each body does (4.6 did this for the four real game-module bodies). The C itself stays under
      `$WORK` on the machine that produced it. Engine internals: name + document, decompile only where script-reachable.
- [x] 4.10 Emit a symbol file (`symbols/xbox.map` / Ghidra CSV) that other tools (xemu GDB, xbdm) can consume.
      Done: **15,422 symbols** (14,956 functions + 466 class tables), every address inside the image's
      sections (checked by the tool), `gdb` resolves them from `symbols/xbox.sym.s` assembled with `as`
      (`docs/SYMBOLS.md`). 12,971 stay `FUN_*` — that is 4.2's blocked signature work (U9), not a gap in
      the file. 127 names are file-level and therefore shared by more than one function; the assembled
      form suffixes duplicates with their address.

**Acceptance:** every `native()` declaration resolves to a named address; zero unnamed functions in the game modules;
`symbols/xbox.map` loads into a debugger against the original XBE without address errors.

### Phase 5 — Assets, levels, data (parallel, ~2–4 weeks)

- [ ] 5.1 **Blocked on `umodel`, which is not installed on either machine** (it is the one UNPINNED tool in
      `tools/versions.lock`). umodel/UEViewer export pass over `.ukx`/`.usx`/`.utx`/`.uax` with the UE2 profile; build
      `inventory/assets.csv`. Note the volume before starting: 3,287 WAVs and 73 maps are already counted by the
      package reader, so the export adds meshes and textures — plan disk for it.
- [x] 5.2 T3D-dump every `.unr` (actors, properties, level script) into `extracted/maps/*.t3d`; index mission structure.
      **Done for actors and properties, not for the level script.** All 73 maps written to `$WORK/extracted/maps`
      (27 MB, **121,225 actors**, 112,000 with a `Location`), and the writer's actor count equals the independently
      measured property-block count, so nothing is silently dropped (`docs/T3D.md`). `make t3d`. Remaining, recorded:
      object references **resolve to names**, not to `ref:<index>`: across all 73 maps **1,178,138 of 1,178,141
      properties** carry a `Class'Package.Name'` value and only **3** fall back to an index
      (`inventory/t3d_coverage.csv`, `make t3d`), so the 2026-10 reading is stale. Still open: arrays/structs are
      emitted by size, and the `Level` export's script sequence is separate from the actors' properties and unread.
      **Class-level half done without T3D** (`docs/MAP_EXPORTS.md`, `inventory/map_export_classes.csv`): all 73 maps
      parse as packages, and level actors are exports, so the actor-class census is a histogram — 6,478 (map, class,
      count) rows, 389 distinct classes, cross-validated against UELib's `map_actors.csv` (identical class vocabulary,
      256 of 389 totals exact, this reader consistently slightly low). **Remaining**: actor *properties* and the level
      script, i.e. the property-stream reader, plus the T3D text itself. `M21A`'s 1,540 exports whose class index did
      not resolve are **explained** (next note): the name length is an index, not a byte, and the two maps holding a
      63-character name lost their tables after 146 and 395 entries — with the rule fixed, `M17D`'s 988 and `M21A`'s
      1,540 unnamed actors resolve, `map_export_classes.csv` goes 6,478 → 6,658 rows with no `?` rows left and the
      actor total unchanged at 194,190 (`docs/PACKAGE_READER.md`). **Actor properties now parse too** (`docs/ACTOR_PROPERTIES.md`):
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
      indexes the checkpoints in its `[Saved Games]` section. The container is now fully read and written:
      `tools/sgs-write.py` rebuilds all nine byte-identically (a zero serial size writes no serial offset).
- [~] 5.5 Parse `.ka`/`.kaw` ragdolls and `LIPSincData/*.lad`; document formats, map to the native loaders from 4.8.
      **The `.lad` container is now measured to a bind pose.** `.ka`/`.kaw` are Karma XML, fully documented; `.lad`
      has its header, channel table, 10-byte prelude and then **one 32-byte pose record per channel** (21 or 19, the
      same bind pose in every file, `inventory/lipsinc_pose.csv`) — after which the 32-byte stride that an earlier note
      claimed for the whole body does not hold. What remains is the animation payload (96–99% of each file), which is
      a keyframe decoder's problem rather than a container's: U4.
      **The payload's shape is measured, not assumed**: 32-byte-strided *runs* — 1 of 8 payloads is a whole number of
      32-byte records, gaps of 32/37/64/128/192, with 37 meaning a record five bytes longer than the common case. The
      earlier phrasing ("does not stride at 32") was too strong and is corrected in both pages.
      **And the ragdoll runtime is located** (`docs/formats/ragdoll-ka.md`): `tools/image-strings.py --topic karma`
      attributes `Karma*` strings to **8 functions** — the four `UKarmaParams` constructors (already named), a
      `KarmaGlobals` initialiser called from `Engine_UnGame`, the safe-time/debug toggle, a
      `KarmaGravScale`/`KarmaTimeScale` reader, and a 10,539-byte body called from the same `0x1226a0` function the
      save/load work found. Ragdoll setup is level-load state, which is what `XLevel->Ragdolls` implied.
      `.ka`/`.kaw` are Karma XML and fully documented (8 assets / 141 parts / 133 joints / 256 primitives).
      **`.lad` is now located end to end except its key bytes**: three parts — 20 pose frames, a clip section (17 named
      clips in the four 19-channel packs, each 20 frames, bone lists of 19/11/9/5/3), and a track table of 2,231
      `<Map>.<Animation>` records whose key blocks are 4.36 MiB of the 6.5 MiB corpus (`inventory/lipsinc_tracks.csv`).
      **The key block is decoded, so `.lad` reads end to end** (U4): a track's block is **26 groups** — `u32 2`, a zero
      byte, a 7-bit varint key count, then that many `u32 2` / `i32` time / `f64` value keys — and a `0x00` terminator
      (`tools/lad-keys.py`, `make lipsinc-keys`, `inventory/lipsinc_keys.csv`, `--check` in `make ci`). All 2,231 blocks
      parse (263,900 keys, 58,006 groups, 4,572,880 key bytes, the same bytes the track table counted), the time never
      runs backwards inside a group, and the groups come in mirrored pairs.
      **The 26 slots are a table, and a value's sign says what it is**: slots 0–18 never carry a negative value in any
      of the 208 file × slot rows (widest `1.33783`) and slots 19–25 are signed (−9.45942 to 9.45942), so the table is
      **19 weight curves and 7 signed curves**; and six slots are recoverable from the others (24 = 25 in 2,230 tracks,
      17 = 18 in 1,845, 22 = 23 in 883, 19 = −24 in 1,890, 21 = −22 in 193), leaving **at most 20 independent curves**
      (`tools/lad-groups.py`, `make lipsinc-groups`, `inventory/lipsinc_groups.csv`, `--check` in `make ci`).
      **The table's extent is declared too**: the bytes before the first header are its record count in the same 7-bit
      varint the key counts use — `60 09` = 608 for the carter pack, `52 0c` = 786, `65 07` = 485, `58 05` = 344,
      `00 03` = 3 — matching the walk's record count in all seven files that have a table (`gap_hex`/`declared` in
      `inventory/lipsinc_tracks.csv`). What remains is which weight slot is which bone and what the 7 signed slots
      mean — not the encoding, the layout or the table's extent.
      **The channel table is an ordered bone list and the pose record's last word is an angle** (below): the eight
      files share an eighteen-name prefix and the two shapes differ by exactly the two tongue bones, and the
      record's last four floats are a unit quaternion whose angle is `2*acos(-w)` -- a whole-degree grid in 97.6% of the
      3,200 records, 2,678 exactly 0°, 77.0° at most. The
      weight slots are **not** one per channel — the 21-channel clips carry the same 26-group blocks — so a slot's
      bone is not recoverable by counting. **The runtime half is read** (`docs/formats/lipsinc-runtime.md`): the
      loader links the channel table to the mesh's bone names with `_wcsicmp` and writes one mesh bone index per
      channel, so a channel is a bone and a key-block slot is not; `tools/lad-bone-owners.py`
      (`inventory/lipsinc_bone_owners.csv`) finds all 21 names declared by five `Animations/*_anim.ukx` skeletons
      and by no other package. What is left is which parse stage is which section, not the link.
      **The stages are the sections, and the container is closed** (`tools/lad-parse.py`, `make lipsinc-parse`,
      `inventory/lipsinc_parse.csv`, `--check` in `make ci`): replaying `FUN_000bce40`'s order — two 4-byte scalars,
      channel table, pose block (20 frames × one 32-byte record per channel), clip array, track array, `u32` + three
      strings — consumes **all 8 files to their last byte** (160 channels, 3,200 pose records, 17 clips, 2,231
      tracks, 58,006 groups, 263,900 keys, the same totals the shape-matching tools counted). Every count is the
      loader's 7-bit varint, so the "10-byte prelude" was the first frame's header (`20`, `u32 2`, `21`, `u32 2`) and
      a pose record starts with its `u32 2` and ends with `-1.0f`.
      **The record is a position and a unit quaternion** (U4): `tools/lad-bones.py` reads all seven floats and the
      last four have `|q| = 1` to `5.5e-7` in all 3,200 records with `w <= 0` always, so the three "opaque dwords"
      are its vector part and the `-1.0f` is its `w` — a pitch of at most 77.0° on a whole-degree grid (506 of the
      522 turning records about `±X`). What remains is which weight slot is which bone, what the 7 signed key
      slots mean, and the clip/track constants (`u32 2`, `160.0`/`220.0`), which their serialisers only carry.
      **The consumers are read (2026-11-09)**: `0x000b4710` resolves a slot **by name** through `0x000b45c0`
      (`__wcsicmp` over the key block, `index*0x1c + base`) and evaluates it, answering `0x4c8848` when the
      name does not resolve; `0x000b5f10` and the fade driver `0x000b63f0` store that answer at record
      `+0x40` and call pose assembly only when it is **non-zero**, so a value is a blend weight whose zero
      is a skip and a negative one is applied like a positive one -- the 7 signed slots are ordinary weights.
      `tools/lad-weights.py` (`make lipsinc-weights`, `inventory/lipsinc_weights.csv`, `--check` in
      `make ci`) measures the gate: 34.5% of the corpus's 35,031 populated slots are non-zero at tick 0,
      0.07% at a track's own end, and 19/24/25 are one number and its negation (exact key for key in 1,890
      tracks). What remains is the binding, not the format: which caller fills a record's `+0x34`.
      **And the slot's own name field is empty everywhere**: `FUN_000bc330` reads a key group as a 28-byte
      record — `u32`, a 12-byte `FString`, a 12-byte `TArray` — so the `u8 0` is the name's count byte, and all
      58,006 groups are unnamed (`named` in `inventory/lipsinc_keys.csv`, `--check` refuses a named row).
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
- [~] 6.2 Solve the package-check problem: regenerate `Packages.md5`-equivalent check data, or patch the check
      off in the XBE for dev builds. Document both paths in `docs/PACKAGE_CHECK.md`.
      **The records now pair with files, and the digest is a header-and-name hash.** The first 32-hex field is the
      package's own header GUID (`GUID = [%08X%08X%08X%08X]`, `0x001219d0`), so `tools/package-check-pairs.py`
      (`inventory/package_check_pairs.csv`) pairs **168 of 168** records with shipped files — 499 packages carry a UE2
      header, 8 header GUIDs are shared by copies, and every record's GUID is one of them. The second field is the
      **quick** digest: `0x00121cf0` hashes the header dwords plus one 8-byte record per name-table entry, which is the
      same routine the `-q` switch prints; the full pass `0x001219d0` is a chunked `md5(file)` and matches none of the
      168. Both live behind the `MD5` console command family at `0x00126860` (`-a -c -s -r -f -q -g -l -j -w`), which
      saves through the engine's own `SavePackage` (`0x0006df00`) — that is why the file is a package. Regenerating the
      check therefore needs no cook inputs: it needs the quick transform, whose only unknown is the name-entry layout
      it digests. **The transform is now reproduced**: `tools/package-check-quick.py`
      (`make package-check-quick`, `inventory/package_check_quick.csv`, `--check` in `make ci`) hashes
      the 52-byte header slice + the generation records + 8 bytes per name entry (its flags dword,
      then the first two UTF-16 units of the name) and matches **168 of 168** records, so the record
      is a function of the header and the name table alone and a rebuilt package's record is
      computable from its own file. The digest is coarse by design (two characters per name), so a
      match does not mean an intact file; a relayout does change it, because the count/offset pairs
      are inside the buffer. **The stage is wired**: `tools/build.sh --target hybrid` runs it over the
      tree it places (`--records` points the join at the source's check data) and passes on the shipped
      packages — **17 of 17** records whose package the build places reproduce, and `UPreview.u` is the
      one `System/*.u` package with no record in the file. The writer half is done too: the database is
      fully parameterised by the records (seven names, two imports, one `PackageCheckInfoN` export per
      record, the same `names < payloads < imports < exports` order the container uses), so
      `tools/package-check-emit.py` (`make package-check-emit`, `--check` in `make ci`) assembles it
      through the writer's own encoders and **reproduces the shipped 20,111-byte file byte for byte from
      its 168 records**; the 78-byte record frame (`00`, the GUID string, `01`, the digest string, `01`,
      seven zeros) is measured from all 168 and reproduced verbatim, because its filler bytes never vary
      and their meaning is unmeasured. `build.sh --target hybrid` now runs `write-check-data` — **18
      records for the 18 packages it places**, 17 of them the cook's and one new (`UPreview.u`), read back
      18 of 18 through `ue2pkg` — and still checks the tree against the *source's* records, so the two
      halves stay independent. Still open: which path invokes the check at runtime (probe).
- [~] 6.3 Ensure deterministic output (fixed timestamps, sorted names) so rebuilt packages diff cleanly between runs.
      **Not claimed yet, and the reason is structural**: every payload the writer places is *copied*, so two runs
      cannot differ — the write path is reproducible (`docs/REBUILD.md` stage 2, 100 of 100 packages across `.u`,
      `.sgs` and `.unr` byte-identical), but a
      *build* is not, because generated payloads (the compiler's output, its timestamps and name ordering) do not
      exist yet. 6.3 closes with stage 5 of the pipeline, not before.
- [~] 6.4 Produce two build targets: `rebuild-hybrid` (our scripts + original assets) and `rebuild-full`
      (our scripts + re-extracted assets) and record which missions boot in each.
      **Both targets exist** (`tools/build.sh --target hybrid|full`, `make rebuild-hybrid` / `make rebuild-full`;
      `docs/REBUILD.md` defines them). `hybrid` is the one that can produce something today: it places **18
      assembled packages** — the writer's own output via `pkg-write --rebuild --write`, not a copy — beside
      `Packages.md5`, then `tools/build-manifest.py` writes `manifest.csv` (size + SHA-256 against the source, and
      the stage fails unless all 18 are `identical=1`) and `missions.csv` (the 18 campaign rows the build carries,
      joined from `inventory/campaign_matrix.csv`). `full` is the same stages plus `compile`/`repack`/`package-check`
      /`boot`, which report SKIP with their reason. **What is still missing**: a hybrid build's script half is a
      *source tree*, not packages (no `ucc`, PLAN 3.6), and the "which missions boot in each" column needs an HDD
      image built from these packages, so it stays empty.

**Acceptance:** `tools/build.sh` on a clean checkout produces bootable packages; rebuilt-vs-original package diffs
are limited to expected fields (checksum/timestamp) and documented.

### Phase 7 — Verification harness and CI (~1 week, then continuous)

- [x] 7.1 xemu configured as an Xbox debug kit (128 MB RAM, debug BIOS, HDD image) per Hidden Palace's notes for
      other debug-kit dumps; document exact config in `docs/XEMU.md`.
- [x] 7.2 Smoke test script: boot → title screen → Continue → load each mission → capture screenshot + log tail.
      **Boot half done and passing** (`tools/xemu/smoke.py`, `docs/XEMU.md`): the harness boots the title and proves
      the guest executes game code by sampling `EIP` (8 samples, 7 in user space, 6 distinct). Five emulator traps
      measured and fixed on the way — `-mcpx_path` gone in 0.8.139, `-serial` needs `file:`, extract-xiso 2.7 ignores
      `-o`, `screendump` absent from QMP, and a zero-filled EEPROM (or a fresh HDD) leaving the CPU halted in the
      kernel forever. **The assertion half now has a measured floor**: the title animates, so two frames with no input
      sent differ by 3,525–3,629 pixels, which is what the earlier "4178 differing pixels, the key reached the guest"
      reading was. `tools/xemu/drive.py` runs a *sequence* (`--step`), measures the noise floor (`noise=`), crops the
      assertion (`--crop`, `--fuzz`) and samples `EIP` in the same run (`cpu=`) — `docs/XEMU.md`. **Input is solved** (QMP `sendkey` over an attached
      guest keyboard) and the map list is navigable **by page**: `home`/`end`/`pgup`/`pgdn` move the highlight
      (measured, `docs/XEMU.md`), and the list is 41 entries with the campaign maps inside it. **The single step and the load are measured** (2026-11-03,
      `docs/XEMU.md`): `tools/xemu/vpad.py` puts a uinput pad on port 1 and `--step=pad=` presses it — `pad=down` moves
      the list one entry and `pad=a` **loads the highlighted map** (in-game HUD, no assert dialog). **7.2's printed criterion is met for `M00`** (2026-11-03): `Continue` (a
      profile-carrying EEPROM, `drive.py --eeprom`) opens the list, ten d-pad presses highlight `M00`, and `pad=a` loads
      it -- tutorial overlay, O'Neill's HUD, no assert dialog, frame still changing after 40 s. The list advances one
      entry per press, so `M00` is index 10 and the older page table was read off scrolled windows (31 is `M12F`, not
      `M20A`). **The loop exists**: `tools/xemu/missions.sh OUTDIR INDEX...` boots once per index,
      presses the d-pad that many times and keeps a frame per mission, one row per index in `missions.tsv`; indices 10
      (`M00`, the SGC lab) and 11 (`M02A`, an off-world jungle cutscene) both loaded and were photographed. **Run over the whole reachable set** (2026-11-04, `docs/XEMU.md`): all **31**
      list entries (indices 10-40) load, 27 on the first attempt, 3 on a second, 1 on a third; 62 `M*` maps are on the
      disc and the list offers 31, so the rest are unreachable from `QUICK SELECT MAP` and are a separate question.
      **Run over six indices** (2026-11-04, `docs/XEMU.md`): 10, 11, 13, 14
      and 15 load and keep a frame; 12 does not, and its highlight frame is the main menu, so that failure is input, not
      a map. The stage now defaults to a 135-second boot window (`--boot=`), which turned 14 from a failure into a pass, and
      retries a failed index (`--retries`, default 2) because a swallowed press and a map that will not load look the
      same from outside -- proven by cutting the boot window to 25 s, which fails three attempts in a row and says so.
      **The truncation is retired, and the list is 73 entries in sorted name order** (2026-11-08,
      `docs/MAP_LIST.md`): `GetMapList` has no cap, the file layer hands it 73 names, and the controller's array
      reads `count=73` -- so the "41 names" was a scrolled page table, not the list. The order is the disc's own
      sort, and the two measured highlights pin it: 10 presses reach `M00` (index 10) and 40 reach `M15B`
      (index 40). The 30 campaign maps past `M15B` are at indices 41-70, which more presses reach; what is still
      untested is whether they *load* -- no run has gone past 40.
- [~] 7.3 **The console works and draws its input line on a stock disc; `ret` submits it, only `stat fps` is
      readable, and no `Console` instance is in the object array in any state measured**
      (2026-11-03, corrected 2026-11-06, `docs/XEMU.md`, U15): `Tab` opens it and `stat fps`
      executed -- `60 cur FPS` / `59 avg FPS` appeared in two runs (`seq13` with the keyboard attached during the boot,
      `seq14` after it), so the attach timing is not the variable. Two things hid it: the console renders no window (no
      overlay, 0-1,501 pixels on `tab`), and `bIgnoreKeys` swallows the first keypress, so a command sent cold loses its
      first character. **Next**: the console cannot draw because `Default.ini` picks `Engine.Console`, the
      only class in the tree without a `PostRender` (measured: swapping it for `GUI.ExtendedConsole` in a rebuilt disc
      makes `Tab` draw an input line, `docs/XEMU.md`), but that class opens in `Say` mode, so text is a chat message
      rather than a command. Clearing the `Say: ` prefix is the next measurement; screen-drawing commands
      (`showdebug`, `stat game`) remain the only readable output until then. The console's prompt is now readable off a frame: `tools/xemu/console-line.py` counts
      its cyan pixels, and a 3x crop of them reads `(>Say at_` -- the `(>` prompt with `Say ` pre-filled and the typed
      text after it, drawn only once there is text (`Tab` alone draws 0). So the console opens and is in **say mode**;
      `absent` means "no prompt text", not "closed". **Measured: only `stat fps` draws** -- and the console's command surface is measured now
      (`inventory/console_commands.csv`: 336 `exec` declarations over 37 scopes, 292 names, `stat` declared in
      neither source, so a zero here means "ran and drew nothing" rather than "no such command"). `stat game` (554 pixels), `stat physics` (2,293), `showdebug` (0),
      `behindview 1` (0) and `walk` (0) left no overlay, and `behindview` would be unmistakable if it had run -- so the
      survey is a lower bound, not a table. **The next instrument is `bTyping`**: on the stock disc the console draws
      nothing, so whether it is open has to come from the guest's `Console` object over QMP, like `mem.py`'s other reads.
      **Measured: the prefix cannot be cleared.** Four
      backspaces and `stat fps` still read `(>Say at fps_`, and `Enter` sent a message: on the swapped disc the console is
      a chat console. The stock disc is the opposite -- invisible and executing (`60 cur FPS` twice) -- so **the class
      swap is a dead end for probing**: 7.3 uses the stock disc, runs commands whose effect is drawn, and takes actor or
      objective data from `tools/xemu/mem.py` and the package tools rather than from console text. The paraphrase below is the earlier, wrong
      reading of the same frames, kept because it says what an absent picture cost:
      `System/DefUser.ini:50` binds `Tab=Type` and `Default.ini` sets `Console=Engine.Console`, so the intention is there,
      but measured in live gameplay with a guest keyboard attached, `tab` (683 and 1,501 pixels), `grave_accent` (0) and
      `kp_7` (30, the only live branch in `Engine/Console.uc:149-159`, whose `ConsoleHotKey` arm is dead code and whose
      config byte is 0) all leave the screen unchanged, while a burst of letters does reach a chat-shaped line — the
      keyboard is delivered and the console still does not appear. **Next, in order**: attach the USB keyboard before the
      guest enumerates `XID` (today's `device_add` lands ~75 s into the boot), or trace the scancode the Xbox kernel
      delivers for `sendkey tab`. If neither opens it, the scripted-`exec` probes move to
      **Whole-RAM dump (2026-11-06)**: `pmemsave` as a `drive.py` step writes all 128 MiB inside the boot and
      `tools/xemu/ramscan.py` searches it in 0.1 s; the typed line is in it **exactly once** -- `0x06823b7a`,
      two bytes into `" hist"` at `0x06823b78` -- while the boot whose pad press never took has 0 hits and the
      8-bit form has 0. Nothing in the image points at the buffer (no dword names the match or the 0x40 before
      it, no class table within 0x8000), so the owner is the register file: **next is
      `--step=monitor=info registers` in the same boot**, one step and no new data.
      `--objects` then censuses every class table over all 128 MiB: 492 tables, 1,606 four-aligned
      occurrences, and the `Console` table appears **twice** -- a `mov` at `0x00291bbc` and the class
      default at `0x02661b00` -- so the negative no longer rests on the object array's pages.
      **The submit reads the line (2026-11-06)**: the same boot with `--watch-key=ret` takes four `Z3` stops on
      `Console+0x44`, all at `EIP=0x1b300`, with `ESI=line+0xC` and `EDI=0x0802e610` -- a fresh heap buffer, where
      the append's stop had `EDI` on the line. Read as the register file *after* the 12-byte move, both boots read
      the line, so the stub does honour a read watchpoint and the earlier "the trap is the write" was the
      pre-execution reading of the same registers. `History[HistoryTop]` is still unobserved: the `pmemsave` after
      a watch is a pre-submit snapshot (`TypedStr` unchanged, no `History` field), so the receipt needs a `wait=`
      first. `tools/xemu/console-state.py` reads a dump's `Console` string fields and `--expect` asserts one; a
      second boot measured the needle's own failure (a repeating key grew `TypedStr` to 104 chars, `hist\0` matched
      0 copies, so `ret` was never sent -- `drive.py` now falls back to the bare text).
      **The receipt is measured (2026-11-06)**: a `pmemsave` three seconds after `ret` holds `TypedStr` empty and
      `History[0]` (`Console+0x50`, 16 inline slots of stride `0xC` ending at `+0x110`, where `TypedStrPos` reads
      5 -> 0 and `bTyping` 1 -> 0) holding the line, so the key is delivered and the command reaches
      `ConsoleCommand`; `console-state.py --receipt hist` asserts it and answers 1 on the pre-submit dump
      (`docs/XEMU.md`). **`pad=a` is not a submit (2026-11-06)**: `submit6` repeated the round twice in one boot,
      re-typing the line each time and dumping before and after — round 1 `ret` reproduced the receipt on a second
      heap (`Console` `0x02641b00`, `History[0]` `0x0802e61c`), and round 2's control dump held the line while its
      post-dump did not change, so the A button reaches the console as nothing and a scripted console needs the
      keyboard. `kp_enter` and `pad=start` are both measured from an open console and neither is a submit, and the
      console's *output* still has no sink. **The sink is named now (2026-11-06)**: six commands in one boot all
      reached `ConsoleCommand` (`History[16]`, six slots, `TypedStr` empty, `bTyping` 0) and only `stat fps` drew --
      because `StargateHUD` overrides `DrawHUD` **and** `DisplayMessages`, comments out `AddTextMessage`, and routes
      `Say` to a **2.0 s subtitle** and everything else to `NotificationManager` -> `GameHud.SetNotifications` (both
      conditional). `Engine.HUD.TextMessages[8]` is never filled on this disc. The run's frame comes 3 s after the
      submit, so a 2 s subtitle is already gone, and the dump holds no copy of any message string. **Next**: a boot
      with the per-command frame one second after the submit (the tool now does that) and a dump read of
      `NotificationManager`/`GameHUDPtr`.

      **The 1 s frame was taken, and the census is why the dump read said zero (2026-11-06)**: `msg1` loaded `M00` on
      the first attempt and submitted `say hello`, `addnotification hello` and `stat fps` (`Console` `0x02682b00`,
      `History` 3 slots, `TypedStr` empty, `bTyping` 0); the frame **one second** after the submit still draws nothing
      for the two message commands and `64 cur FPS` / `60 avg FPS` for `stat fps`. `ramscan --objects --class
      StargatePlayer` answered six hits and **all six `not-object`** -- including the object itself at physical
      `0x05aaf800` (`0x4d0b68 0xbb0d 0x7e57640 0x9335d60 0 0xffffffff`): its `ObjectInternalInteger` is `0xbb0d`,
      twice the screen's `0x10000` bound, and the two header words after it are above the 128 MiB dump, so the "two
      in-image pointers" rule rejects a real mission object. On the measured pair instead (integer in range, then `0`
      and `0xffffffff` at `+0x10`) the census names it *and* `StargateHUD`'s object `0x05bc6a00`, and decoding its
      fields through the page tables gives `Pawn`, `PlayerReplicationInfo`, `AIMsgManager`, `StargateHUD` and
      `XboxViewport` -- every one a **linear** address, which is also why `--refs`' raw dword comparison named nothing;
      with `--cr3` it finds `hello`'s owners (`Console+0x5c` is the submitted line). `GameHud` and `NotificationManager`
      are script classes with **no XBE class table**, so neither can be censused yet. **Next**: a package-class-table
      resolver, and a dump taken *at* the submit -- M00's own dialogue rewrites `SubTitleText`, so a dump ten seconds
      later cannot say whether `Say` reached the HUD.
      **A script class has no table of its own, and the player names no `GameHud` (measured 2026-11-07)**: the
      "package-class-table resolver" this item asked for is measured *not* to be the missing piece -- `ramscan.py
      --headers` groups every object-shaped header in a 128 MiB dump by its first word, and **0 of 5,485** words the
      inventory does not know name a slot block, so `GameHud extends Interaction` simply carries `Interaction`'s
      table and a package's classes get no table of their own at load. `ramscan.py --fields` reads one object
      instead: the player (`0x05aaf800`) names 10 objects over 7 classes -- the anchors `objects.py` measured
      (`Package`, `Pawn` x4, `ScriptedController`, `PlayerReplicationInfo`, `AIMsgManager`, `XboxViewport`,
      `StargateHUD` at `+0x748`) -- and **no `Interaction`**, so `GameHudPtr` and `NotificationManager` are unset:
      the two message channels have no sink, which is why a frame one second after the submit drew nothing. The 19
      objects on the `Interaction` table are 52 bytes each (the class's `object_size`) against `GameHud`'s 106
      properties, so none of them is the HUD actor. **Next, corrected (2026-11-07)**: the route is read now --
      `inventory/interaction_setup.csv` (`make interaction-setup`) shows `GameHudPtr` written by
      `StoryModeGame`/`StargateDeathMatch.SetupInteractionsFor` from `InteractionMaster.AddInteraction`, reached only
      from `StargateHUD.PostNetHUDSetup` <- `StargateHUD.Timer` <- the `SetTimer` in `StargateHUD.PostNetBeginPlay`
      -- and nothing in it reads `CurrentGameProfile`, so the checkpoint load this note used to ask for would have
      measured the same profile-free path and is retired (`docs/XEMU.md`, U15). What is left is whether that
      `PostNetBeginPlay` route ran at all: a breakpoint on `UInteractionMaster::AddInteraction` during an `M00` load
      fires (the setup ran, and the class string did not resolve) or never does (the route is dead in this boot, and
      no map will show those five fields).
      **A script breakpoint is a pattern, not an address (measured 2026-11-07)**: `AddInteraction` is script
      (`0x00020803`, no `Native`), so the breakpoint this item asked for has no native target; `drive.py --step=brk=`
      treats a function's bytecode as the pattern instead (`tools/uscript-dis.py --script ... --hex-bytes 48`), finds
      its copies in a 128 MiB dump taken inside the boot, and arms `Z0` on each (`mem.py`,
      `watch(kind="exec")`), reporting `FIRED <label>` with EIP/ESP or `did not fire in N s` per label. All five route
      patterns are verified present in the shipped packages, but the boot meant to arm them never loaded a level (the
      first `pad=a` moved only the highlight at a 135 s window; the last frame is still `MAIN MENU`), so the step
      answered **0 copies of every pattern** -- a fact about that dump, not a verdict on the route. The step now
      prints the dump's length and the label's ASCII name count so the two failures cannot be confused, and the runner
      uses `--seconds=180 --pad-hold=0.35`. **Next**: the same sequence with those two settings, which either fires
      `Timer` (the route ran and the class string did not resolve) or fires nothing (the HUD's `PostNetBeginPlay`
      route is dead in this boot).
      **The loaded copy is not the package's bytes (measured 2026-11-07)**: that re-run would have answered 0 copies
      again, and the boot was blamed wrongly — a 128 MiB dump of a boot that *did* load `M00`
      (`work/msg1/try1/ram.bin`) holds **no 8-byte prefix** of `Timer`'s bytecode, so the byte-exact pattern the step
      searched for does not exist in a guest at all. The package loader **widens every compact index operand to a
      four-byte slot**: `StargateHUD.DrawOrderTarget`'s log call reads `b8 2a` in the file and `a0 1e 26 01` in the
      guest, its one-byte `8a` is `00 62 fe 00`, while its `07 cd 00` jump and fixed operands are unchanged.
      `uscript-dis.py --hex-bytes` now emits that model — opcodes and fixed operands exact, four `??` per index — and
      `drive.py --step=brk=` matches `??` as any byte; on it `Timer` (`0x01573140`), `PostNetBeginPlay`
      (`0x017d8dc0`) and `DrawOrderTarget` (`0x019bf600`) are each found exactly once in the same dump, where the
      byte-exact form finds none of them (`DrawHUD`, the longest tried at 742 bytes, is still not found, so the model
      is measured and not proven). **`--brk-freeze`** (new) issues QMP `stop` before the dump and `cont` after the
      arm: `pmemsave` of 128 MiB is tens of seconds of a *running* guest, so without it a breakpoint on a load-time
      function is armed into a level that already entered it. **Next**: the same boot with the widened patterns and
      `--brk-freeze`; `Timer` firing means the route ran and the class string did not resolve, nothing means the
      HUD's `PostNetBeginPlay` route is dead in this boot.

      **A script function is *read*, not executed (measured 2026-11-07)**: the item above armed Z0 execution
      breakpoints and got nothing three boots running, and the reasons were both the instrument. UE2's VM reads a
      function's bytecode through a pointer and never fetches an instruction from it, so `Z0` on a script function
      cannot fire at all: `StargateHUD.DrawHUD`, which draws every frame, took **no** Z0 hit in 10 s of gameplay while a
      `Z3` read watchpoint on the same first byte (`0x01c4cc00`) stopped immediately. `drive.py --brk-kind=read` is
      now the default and `exec` is kept as that control. Second, the stub's stop reply names the *address*
      (`T05thread:01;rwatch:1c4cc00;`), not a watchpoint index, so the index parser matched nothing and ended a wait on
      its first hit. Third, the guest can be *waiting* on an intro card with the arm in place: `--brk-tap=SECS:BUTTON`
      (with `--brk-tap-step`) presses the pad from inside the wait, because a step list is sequential and no `pad=`
      step can run while a `brk=` step waits; `--brk-wait`/`--brk-kind` take comma lists so one boot can carry a
      control and the measurement. With all three, read watchpoints on the five HUD-setup functions armed 0.5 s after
      the map select and five A taps inside the wait (all landed; HUD up, `04-gameplay` 9,489 px) reported
      **`PostNetBeginPlay`'s first byte read 0.4 s after the first tap** (`rwatch:16b2800;`, eip `0x0003990f`) -- where
      150 s of Z0 had reported nothing. The parse bug ended the wait on that first stop, so the four later links went
      unrecorded, and a post-load dump finds all six patterns at the same linear addresses as the pre-load one (the
      arms were never stale). **Next**: the same boot with the fixed parser, which records whether `Timer` and
      `PostNetHUDSetup` are read after `PostNetBeginPlay` -- that is what separates "the route ran and the class string
      did not resolve" from "the read was the loader's copy" (`docs/XEMU.md`).

      **The reader is the loader's walker, so the route needs an effect, not a byte watch (measured 2026-11-07)**:
      the same boot with the parser fixed recorded all six arms, and every one stopped in the **same** reader --
      eip `0x0003990f` (`+0x2f` into the slot-11 script walker `0x000398e0`) with identical `esp` `0xd0168a30` and the
      same return address `0x00039420`, from `t+8.7` to `t+14.9 s`: one loop over a class's functions in one stack
      frame, where the route's own call chain would deepen `esp` per level and would reach `Timer` (a `SetTimer(1.0)`
      callback) a second after its setter rather than 0.3 s after `AddInteraction`. `DrawHUD`, the control, is read
      from `0x00040900` with a different stack in gameplay and not at all at the title, so the two readers are
      distinguishable. That makes the Z3 measurement above the loader's copy of the scripts, not the VM running them:
      **the route question needs an effect witness** -- `UInteractionMaster::AddInteraction` (reached only from the
      seven `AddInteraction` sites) or an exec breakpoint filtered on the reader -- and "the class string did not
      resolve" stays untested. Harness: each `brk=` hit now prints `t+` since the wait started, and a stop that
      matches no armed address is reported and survived instead of ending the wait (that break is what truncated the
      previous boot's chain).

      **Both readers are load passes, and the route is not seen after them (measured 2026-11-07, wait cut at the stop
      cap)**: with the arms kept (`--brk-keep`) each of the six functions is read **twice**, 0.1 s apart, by
      `0x0003990f` and `0x00058ad4`, and in the walker's order -- `AddInteraction` at 12.7 s *before* `Timer` at 13.0 s
      and `SetupInteractionsFor` at 14.9 s, which a chain through `SetTimer(1.0)` cannot produce. `DrawHUD`, kept armed
      in gameplay, is read from `0x00040900` once and then from `0x00048ce0`/`0x00048ce8` about 5x/s, so the
      interpreter's reader is identifiable. **Next**: the same arms with `--brk-stops=40`, so the window covers the
      whole 150 s through gameplay -- a third read is the route running, and only the two load readers is the route
      dead in this boot. Harness: `--brk-keep`/`--brk-stops` log every reader `eip` of a kept watchpoint, and the
      per-label "did not fire" line is suppressed under it (it contradicted its own READ lines in this run).
      **The route runs, and a read watchpoint can witness that (measured 2026-11-07)**: the same arms with
      `--brk-stops=40` show five of the six copies read a third time from `0x00048ce0`/`0x00048ce8` -- inside
      `UObject::ProcessInternal` (`0x00048bf0`), the pair that reads `DrawHUD` every frame -- 2-3 s after the load
      passes: `SetupInteractionsFor` t+17.8 s, `AddInteraction` t+18.0 s (and eleven further executions by t+20.4 s),
      `PostNetBeginPlay` t+18.2 s, `Timer` t+18.9 s, `PostNetHUDSetup` t+19.0 s. So the five `none` player fields are
      an *effect* question, not a dead route, and the walker-only reading in the note above is corrected: a `Z3` on a
      script function's first byte does witness execution once the reader `eip` is checked. The chain's **order**
      still does not match -- `SetupInteractionsFor` and `AddInteraction` execute before `Timer` and
      `PostNetHUDSetup` -- so the copies are not all the HUD's own functions. Harness: `--brk-keep-stops=N` retires
      one armed copy after N reads and leaves the rest armed, so a hot arm cannot spend the whole budget (both
      earlier waits ended at the stop cap inside the load window; this one used 3 reads per copy). **Next**: which
      export owns each armed copy -- a dump-side object-array walk for a `UFunction` whose script pointer is the
      copy's linear address, no boot needed -- then a `--fields` read of `GameHudPtr` from a dump taken after t+19 s.
      **The copy is not an object's field, and the owner channel is a loader record (measured 2026-11-07)**: the
      "dump-side object-array walk" this note asked for is built (`tools/xemu/owner.py`) and it answers the opposite
      of what the plan assumed. From `brk6`'s frozen dump (`cr3=0xf000`, 128 MiB): 48,606 array slots, **9,403 objects
      reached** (27,444 slots hold a pointer that does not translate -- 24,183 unmapped, 2,700 a zero first word, 561
      past the dump), `Outer` measured at header **`+0x18`** (names a `Class` for 300 of 300 sampled `Function`
      objects; `+0x0c` for none), and **none of the six armed copies is a field of any reached object**. Each copy is
      the third word of a 24-byte loader record -- `(kind, index) | copy | (len, len, offset)`, so `Timer` is
      `(4, 308) | 0x016b0140 | (227, 316, 30526)` and `SetupInteractionsFor` `(16, 2757) | 0x01a7e000 | (574, 793,
      7801)` -- which means the copy's owner is not reachable as a `UObject` field, and the record's index is the
      handle that would name it. Naming needs `GNames` (`FName` index -> string), and this dump has no name table in
      the shapes tried: 0 `(Data, Num, Max)` triples over 128 MiB parse as a name-pointer array, and no dword names
      the UTF-16 class-name strings the dump holds (`StargateHUD` x13, `Console` x112). **Next**: locate `GNames` in a
      live guest -- the `objects.py --find` shape scan applied to `.data`, validated by resolving an object `Name`
      whose class the inventory already names -- then a record's index names the function and `+0x18` names its
      declaring class, which is the answer 7.3's route order needs.


      **`GNames` is measured, and a loader record's index names its function (measured 2026-11-07)**: the "locate
      `GNames` in a live guest" note above is done, and not by the shape scan it proposed. In a guest booted to the
      title the name array is **`0x006be0d8`** -- `data=0x01f30000 num=34,800 max=37,732`, immediately after the
      name system's `Initialized` dword at `0x006be0c8`, entries `+0x00` index / `+0x04` flags / `+0x08` chain /
      `+0x0c` UTF-16 name, positional (slot 0 `None`, 1 `ByteProperty`, 2 `IntProperty`, 100 `Begin`). The
      `--find` shape sweep of `.data` (2,255,828 bytes) is a **negative with a cause**: 4,496 triples pass the
      `(Data, Num, Max)` shape and only `GObjObjects` (`0x006c21b0`) resolves to a real pointer array, because 21
      of the name array's first 64 slot pointers fail to read back (`odd hex length`) and a prefilter reads a
      swallowed retry as a null. The 4,096-slot hash at `0x006ba0c8` is live too (4,093 buckets occupied).
      **The ownership question this closes**: resolving the seven record indices `owner.py` measured names the
      functions -- 308 `Timer`, 2014 `PostNetBeginPlay`, 17040 `PostNetHUDSetup`, 2757 `SetupInteractionsFor`,
      6823 `AddInteraction`, 2427 `DrawHUD`, and 357 **`PostBeginPlay`**, not `PostNetBeginPlay`, so that second
      copy is a different function with the same bytecode shape. Tool: `tools/xemu/names.py` (`--find`, `--hash`,
      `--index`, `--verify`); doc `docs/XEMU.md`. **Next**: `names.py --at 0x006be0d8 --verify` for `UObject::Name`'s
      header offset, then U15's five `none` player fields are a class-level read rather than a route question.
      **`UObject::Name` is `+0x20` and `UObject::Class` is `+0x24` (measured 2026-11-07)**: `--verify` now measures
      both, and the class pointer needs no layout assumption -- a `UClass` object's own vtable is the `Class` table,
      so the header dword pointing at one *is* `Class`, and `+0x24` does it for **400 of 400** sampled objects in a
      guest at the title. `Name` is the class object's dword that resolves through `GNames` to the class name the
      instance's vtable already carries: `+0x20`, **260 of 400** and no counterexample. `+0x44` ties at 260 and is
      not it -- a different value in 32 of 40 objects, and an in-range index for only 117 of 400 against 399 for
      `+0x20`. The header reads `Outer +0x18`, `Name +0x20`, `Class +0x24`; a class default being named after its
      class scores 0 of 400 here, so that cross-check is a weak signal and not a test. `names.py --census` is the
      new walk this enables -- every object's class from its own `Class` pointer, which is the only route to a
      *script* class (`GameHud` carries `Interaction`'s table). **Next**: run `--census` over a boot that has loaded
      `M00` and read the class histogram for `GameHud`/`NotificationManager`, which turns U15's five `none` player
      fields into a class census rather than a route question.
      **The census is measured over a loaded `M00`, and the object array continues past 128 MiB (2026-11-07)**: one
      boot (`census1`, index 10, gdb stub held open) walks `48,646` array entries into **371** named classes, and
      `2,835` of those entries sit at or above `0x08000000` -- the bound `objects.py` uses for the 128 MiB
      `pmemsave`. Sampled, they read as ordinary objects (`StaticMesh`, a `Class` at `0x082ff600`), and they hold the
      boot's live gameplay objects: `GameHud` at `0x09332100` (plus the class default at `0x0682ab00`),
      `NotificationManager` `0x096dc340`, `PlayerProfile` `0x08a75040`, `SGInteraction` 32, `StargateTeamHUD`
      `0x08af0a00`. So U15's five `none` player fields were a bound artifact, not an unset channel. `names.py` gains
      `--csv`, a comma list for `--grep` and `--addresses`, and its own `OBJECT_HI = 0x10000000` for the array.
      **Next**: read `GameHud.SetNotifications` on `0x09332100` over the stub (one boot, `--fields`), and split the
      `FNameEntry` read at the 64 KiB boundary that leaves five `Core` classes unlabeled.
      **The five `Core` classes are named, and the fault was an unmapped page rather than a crossing (2026-11-07)**:
      their `FNameEntry` blocks are the last allocation on a mapped page (`0x95ffc0` -> `0x95ffff`, `0x9affc0` ->
      `0x9affff`), so the 64 bytes inside the page read and the fixed 206-byte request died on the next, unmapped one.
      `names.py` reads the entry one page at a time and keeps the prefix (`read_prefix`), a prefix with no
      NUL-terminated name is still absent, and `--selftest` reproduces the boundary in `make ci` with no guest. Same
      boot: index 5 = `ObjectProperty` (5,867 objects), index 103 = `Function` (8,623), and a re-run census names
      **376** classes over the loaded `M00` with **no** `<unreadable>` row. **Next** stays the HUD read: `GameHud`
      `0x09332100` and `NotificationManager` `0x096dc340` are both in that census, so `SetNotifications` can be read
      over the stub.
      **The live field walk works, and the HUD's text windows exist (2026-11-07)**: `tools/xemu/fields.py` reads an
      object's class from `+0x24` and every dword of its field block, resolving each one that is an object pointer
      through the name table (`--object`, `--properties`, `--compare`). Over the census3 `M00` boot, `GameHud`
      `0x09352100` (class object `0x01886c00`) holds **36** pointer/array fields in 288 bytes, and its three
      `UITextBox` objects sit at `+0x050`, `+0x054`, `+0x058` in the order `GameHud.uc` declares `DialogueWindow`,
      `NotificationWindow`, `InteractionWindow`, with `StaticMesh HUD_Notifywindow` at `+0xd4`. Two negatives:
      the class default (`0x0682ab00`) is **not** in the class object's first 0x400 bytes, and the `Children` head is
      not a `+0x28` chain of property objects there (the best candidate is two `Function` links), so property names
      need that offset measured rather than scanned. `GameHud.uc:1034` shows `SetNotifications` replaces an **empty**
      argument with a fixed 27-character placeholder, which splits the missing-text question in two.
      **The notification window holds the message, not the placeholder (measured 2026-11-07)**: over a fresh
      loaded-`M00` boot (`census4`, stub `12511`) `GameHud` is `0x092e2100` (class object `0x01886c00`) with the same
      three `UITextBox` at `+0x050`/`+0x054`/`+0x058`, and the middle one carries `StaticMesh HUD_Notifywindow` at
      `+0x040`, so it *is* `NotificationWindow`. Its `DisplayText` `FString` sits at **`+0x394`** with `Count=45
      Max=45` -- a 44-character objective line, drawn in the frame captured just before the read -- so
      `SetNotifications` was handed a message and the 27-character placeholder (which would read `Count=28`) is not
      in play. The dialogue window holds a `Count=53` string at the same `+0x394`, and both boxes hold
      `TextBoxFontName` at `+0x4c0`. **Two corrections**: the field is `UIWidget.DisplayText`, not a `Text` member of
      `UITextBox` (`Text` is only `DisplayTextBox`'s parameter), and the tool's `0x200`-byte default read was the
      reason the string was invisible -- the block runs past `+0x4c0`, so `--size` now defaults to `0x600` and a text
      triple prints as an `FString` character count with UTF-16 decoded under `--show-text` (`docs/XEMU.md`).
      **The class object's address is not reusable across boots, and neither is the recorded `GNames` (measured
      2026-11-07)**: over a `census6` loaded-`M00` boot (same script as `census4`, gdb `12519`), the address
      `0x01886c00` census5 reported as `GameHud`'s class object holds UTF-16 package text, and `0x006be0d8` (the
      tools' default `GNames`) holds the ASCII path `xbox\engine\inc\` rather than a `(Data, Num, Max)` triple;
      `.data` itself reads at its declared `0x598e00+0x226bd4`, so the slot is wrong for this boot, not the address
      space. `.data` carries **15,384** TArray-shaped triples, **9,121** of them with a plausible name count, and
      `names.py --find`'s per-candidate scoring had not finished after ~2.5 minutes. **New instruments**:
      `tools/xemu/findcls.py` (an object by its own name, one page per 64 KiB page, printing its address and class
      object) and `tools/xemu/find_names.py` (count-filtered triples, then element 1 must decode as `ByteProperty`).
      **Next**: boot, locate the `GameHud` and `UITextBox` class objects with `findcls.py` and `GNames` with
      `find_names.py`, then run `fields.py --members --start 0x800` on both class objects and compare the offset
      that holds an instance of each class -- agreement there is `DefaultObject`, which the first `0x800` bytes did
      not contain.

      **The class object's own members are a field table, and `+0x4e8` is not the default object (measured
      2026-11-07)**: over one loaded-`M00` boot (`census5`, stub `12515`), `GameHud`'s class object `0x01886c00`
      (class `Class`, name `GameHud`, `Outer` `0x0097e0a8` named `StargateGame`) holds **323** object pointers in
      `+0x28..+0x800`, **307** of them in `GObjObjects`. `+0x28` is the `Interaction` class object -- `GameHud
      extends Interaction` (`StargateGame/GameHud.uc:4`), so that dword is `UStruct::Super` -- and from `+0x98` to
      `+0x494` (36 rows past `0x400`) runs a nearly dense table of `Function`/`Property` pointers: the class's own
      fields (`Cleanup`, `DisplayTextBox`, `SetNotifications`), whose `Outer` (`+0x18`) is the class, interleaved
      with `Object`'s native operators (`Lerp`, `Add_IntInt`), whose `Outer` is the `Object` class `0x00bcf000` --
      a name-keyed field map, not a `Children` chain. `+0x4b0` is the `Object` class object and `+0x4d0` a
      `PlayerController`; **`+0x4e8` is not an object**: `0x013c1e00` reads vtable `0x004d4870`, ObjectIndex `-1`,
      `Outer` 0, Name 0 and every other dword zero -- an unconstructed block carrying only a vtable and a `Class`
      pointer, so the earlier `DefaultObject` reading is refuted. The live `GameHud` instance the census names
      (`0x0682ab00`, ObjectIndex `0xa221`, name `GameHud`) is **not** pointed at anywhere in the class object's first
      `0x800` bytes, so `DefaultObject` is still unlocated. **Next**: the class object past `0x800`, with the same
      `fields.py --members` scan on `UITextBox`'s class object `0x0197a800` as the control.

      **`GNames` is static at `0x006be0d8`, and the locators were reading the wrong thing (measured 2026-11-07)**: over
      one loaded-`M00` boot (`census8`, gdb `12523`), the slot reads `data=0x07720000 num=41,265 max=51,886` right
      after the `Initialized` dword at `0x006be0c8`, and slots 0/1/2/3/5/16/100 resolve to
      `None`/`ByteProperty`/`IntProperty`/`BoolProperty`/`ObjectProperty`/`PointerProperty`/`Begin` with `Index`
      equal to the slot -- so the census6 paragraph that called `GNames` per-boot was a read fault, and only the
      `Data` pointer moves. `findcls.py` walked 48,622 of 48,646 object headers over 347 pages and named none of them
      because its `hasattr(nm, "text")` guard is always false (`names.Names` answers through `__getitem__`); fixed,
      and `names.Names` now caches 64 KiB entry pages (`names.py --selftest`: two entries in one page cost 2 reads),
      because one stub read per object put the walk past ten minutes. `name_table` also refuses a slot that is not a
      `(Data, Num, Max)` triple: the old default answered `num=1735288156` and turned the next read into a 6.9 GB
      walk that never finished -- the 16:00 run died there, with no measurement and no lock release. **Next**: the same
      boot, `fields.py --members --start 0x800 --size 0x1400 --at 0x006be0d8` on `GameHud`'s and `UITextBox`'s class
      objects (named by `findcls.py`) -- `DefaultObject` past `0x800` is still unlocated, and no scan is needed to
      locate `GNames`.

      **A loaded `M00` reads 36,281 names, so that counter is not a load gate (measured 2026-11-07)**: the `census10`
      boot (`~/sg1/work/xemu/census10.sh`, gdb `12541`) is census9's sequence with the first pad press at **145 s**
      instead of 95 s, and it loaded the level -- `02-mission.png` is the gameplay HUD (O'NEILL, `P90` 250, no assert,
      no BIOS prompt), the select moved 111,008 pixels and `GObjObjects` holds 43,741 objects -- while `GNames.num`
      reads **36,281** there, against census9's 34,800 (main menu) and census8's 41,265 (`M00`). The figure moves with
      the level but sits *below* 40,000 in a real mission, so the gate the previous note proposed would have thrown a
      loaded boot away; decide a load from the frame or the object count. `findcls.py --csv` names **43,681** of
      43,741 objects over 289 pages in ~13 minutes (2 min 14 s CPU: stub-latency bound, and the CSV appears only at
      the end), and on it `GameHud`'s class object is **0x01886c00** and `UITextBox`'s **0x0197a800** -- the
      census4/census5 addresses, so class objects are stable for a level while the instances move (two `GameHud`
      instances, three `UITextBox` here, all above 128 MiB). `fields.py --members --start 0x800 --size 0x1400` on
      `0x01886c00` finds no default object either: `+0x800..+0xab0` is the **`Object`** class's native operator table
      (`Split`, `Lerp`, `VRand`, `Rand`) with `Struct` objects beside it, ending in `0x00bcf000`, the `Object` class
      object, which `--members` labels `instance of this class` because its test is wider than `Class == this class`.
      **Next**: stop scanning past `0x800` and settle which `GameHud` row is the class default -- compare
      `0x0682ab00` and `0x080e0000` field by field (a default object carries the class's defaults and no instance
      state), and fix `--members`' label test first so a superclass's class object cannot pass as a CDO.

      **A walk is 255 pages, and this boot never left the main menu (measured 2026-11-07)**: over a `census9` boot
      (gdb `12531`, `~/sg1/work/xemu/census9.sh`) the `pad=down`x10 + `pad=a` sequence that reaches `M00` on most
      boots landed on the **main menu** -- `03-dismissed.png` is `Continue`/`Single Player`, and two one-read
      counters say the same thing: `GNames.num` `34,800` (title) against `41,265` with `M00`, and
      `GObjObjects.num` `42,152` of `51,886`. `sendkey ret` over QMP does not take the highlighted `Continue` (the
      count is unchanged after 40 s), so level entry is a pad step and nothing else. The walk itself is now
      measured: `42,143` object pointers over **255** 64 KiB pages, `42,135` headers read, `8` unreadable, and the
      per-object name resolution is the second half of the cost. A read of an unmapped heap page (`0x04000000`)
      fails whole -- 4 attempts, `0x04100000+0x10000` and a 4 MiB read too -- while `0x006be0d8`, `0x0091ff00`,
      `0x0095ffc0`, `0x01f30000` and `0x041e0000` read at that moment, so a title-screen gameplay address is a
      negative about the address and not a broken stub. `findcls.py` now takes a comma list (and repeats) for
      `--grep`, writes **every** named object to `--csv` as `index,address,class,name`, caches class names by class
      pointer and flushes its progress lines -- one walk answers every name question, because this boot paid for
      two walks and finished neither. **Next**: boot to a mission (the pad's `Continue` entry), confirm
      `GNames.num` > 40,000 before spending a walk, then run
      `fields.py --members --start 0x800 --size 0x1400` on `GameHud`'s and `UITextBox`'s class objects from that
      `--csv`; `DefaultObject` past `0x800` is still unlocated, and no scan is needed to locate `GNames`.


      **Two `GameHud` objects, both named after the class, and neither outered to the level (measured 2026-11-07)**:
      the class default is not a pointer inside the class object -- it is an ordinary object whose own `Class` is the
      class, so the question is a filter over `GObjObjects`, which `fields.py --instances` now asks. Over `census11`
      (gdb `12543`, census10's sequence unchanged) `GameHud`'s class object is `0x01886c00` again (class `Class`
      `0x00bcea00`, `Outer` `Package` `StargateGame`) and exactly **two** of 48,646 objects carry it:
      `0x0682ab00` (index 41,505) and `0x09322100` (index 47,914), **both named `GameHud` and both outered to the
      same `Package`** (`0x0097f928`), so "the level one and the package one" is not the split. `--diff` over `0x600`
      separates them on **34 of 65** pointer/array rows: the first carries a `StaticMeshActor` at `+0x008` and no
      pointer from `+0x324` to `+0x470`; the second carries `ObjectProperty` (`Canvas`), `FloatProperty` (`ClipX`),
      `StructProperty` (`DrawColor`), `IntProperty` (`TeamIndex`) and `Function` (`SetPos`) records there -- metadata
      rather than state, which is what a default looks like, but **which one is the default is not settled**: the
      alternative is a second live HUD with an adjacent allocation in its window. What would settle it: decode
      `+0x324..+0x470` against `GameHud.uc`'s declared fields. Two corrections: a class object's own `Class` is the
      `Class` metaclass, so `--members`' `Class == this class` test can never fire on a class object (the census10
      row read as `0x00bcf000` "the `Object` class object" is one digit from `Class` at `0x00bcea00` -- check that
      transcription), and `--members` now says whether a row's target is named after the class instead of calling
      every instance a default. `Reader.try_u32` is page-cached, so this diff (a 300 s timeout before) takes
      **235 s**; the residue is name resolution.

      **The sink is measured on a level (2026-11-08)**: `sink13` read row `0x08842800` (index 47,885 of 48,646) on a
      loaded `M00` and all four slots are live objects -- `NotificationManager` `0x0968c340`, `GameHudPtr`
      `0x09322100` (`GameHud`), `commandInterfacePtr` `0x096ff230`, `Pawn` `0x089e0000` -- so the console's silence is
      a guard on the message path, not a null sink; the same boot's read cost 85 page reads / 43 s once a page with a
      hole stopped being retried as a whole page (`docs/XEMU.md`, U15).

### Execution notes (2026-11-11, the forced arm is taken and its read-back refuses it)

- **The arm the last round asked for can be taken, and the read-back says it never reaches the game's mapping.**
  `tools/xemu/bp-args.py` now reads every armed byte back through the same context and keeps it in the record
  (`armed_bytes`), because the stub's `OK` is not evidence that the `0xCC` landed where the game executes. Armed at
  the title with the front end in the kernel (`CPL 0`, `EIP=0x80071009`), both slot-19/20 bodies answered `OK` and
  the bytes read back `f6` at `0x000c4f60` and `56` at `0x000c6d00` -- the XBE's own bytes there
  (`f6 81 dc 02 00 00 01 74`, `56 8b f1 8d 8e 00 01 00`), unchanged. So a forced `Z0` in kernel context patches a
  mapping the game does not execute in and no stop can ever arrive; the loaded-`M00` arm keeps its earlier zero (0
  stops in 30 s). The pair stays **unfinished, not negative**, and the remaining question is now a sampling one: the
  arm has to be taken with `CPL 3`, and the front end's halt loop (`0x80024a97`, `0xfffffe5c`, `0x80065996`) means
  waiting for a user-context moment rather than forcing the patch.
- Tool (`tools/xemu/bp-args.py`: the byte read-back, the `armed_bytes` field and the docstring bullet that explains
  why the reply is not the proof) + `docs/NATIVE_CLASS_TREE.md` + `docs/UNKNOWNS.md` U8. No artifact: the record is a
  run artifact under `$WORK`, and `tools/vcall-stops.py` has nothing to read because no stop was recorded.

### Execution notes (2026-11-12, the pair dispatches on an object another object holds -- only 3 sites are `this`)

- **The receiver load is one hop; the object pointer is the second.** `tools/vcall-receiver.py` now walks the
  register the receiver reads from (`origin`, `origin_va`, `origin_chain`, over the same objdump listing), following
  register-to-register copies so that `mov esi,ecx` still counts as `this` and stopping at the instruction that
  produced the pointer. Of the 55 real sites: **28 field** (`mov ecx,[esi+0x4c]`, `[esi+0x48]`, `[esi+0x2c]`,
  `[eax+0x220]`, `[esi+0x1c]`, `[esi+0x310]` -- six offsets), **14 stack** (7 `[esp+..]`, 5 `pop`, 2 `[ebp-0x40]`),
  **4 call-result**, **3 global** (`.data`: `0x5fbed4` twice, `0x5bf514` once, whose pointer `0x5c1a0c` is not a
  class table), **3 `this`** (`esi<-ecx`, `ecx` unwritten), **2 frame** (the stack-slot receiver) and **1
  argument** (`[ebp+0x18]`).
- **What it changes.** The pair is not a virtual a class calls on itself: 46 of the 55 dispatch on an object the
  caller holds -- a field of another object, a stack slot, a call's return -- so the class that carries slots 19/20
  is the class of *those* objects, and the one class the whole image shares is the three global-pointer sites. The
  earlier line "no site loads it from an absolute global" holds for the receiver and not for the pointer: three
  sites do take the pointer from `.data`, which is what a singleton instance looks like.
- Tool (`tools/vcall-receiver.py`: `origin_of`, the three columns, the selftest cases for a `this` chain and a field
  read) + `inventory/vcall_receiver_sites.csv` regenerated (106 rows) + `docs/NATIVE_CLASS_TREE.md` +
  `docs/UNKNOWNS.md` U8. What is left is the class of the held object at run time -- a boot cannot supply it
  (U8's multiplayer/dedicated path), so the next static instrument is the class of the field's owner, which the
  property stream names.

### Execution notes (2026-11-12, the 106 hits are 55 calls: a second decoder corrects the first in both directions)

- **The narrow boundary test was wrong both ways.** `tools/vcall-receiver.py` now runs `objdump -D -b binary -m
  i386 -M intel` over each site's function start and records its verdict per row (`oracle`: `agree` / `spans`),
  because one decoder's "an instruction ends here behind a chain" was neither necessary nor sufficient. **8 hits
  were accepted** as boundaries where the `ff` is the last byte of a `lea`'s displacement -- at `0x002d113c` the
  `lea ecx,[ebp-0x15c]` bytes decode as `add [ebp-0x15b73],ecx`, which lands on the site behind a chain of
  wrong-length decodes -- and **15 were rejected** where a byte the narrow decoder does not know broke the chain in
  front of a real call. The 8 `unread` rows of the previous note are the first group: they are displacement bytes,
  not a decoder gap, and the backward walk that note described as carrying 66 rows never supplied a receiver.
- **55 of 106 are calls, and every one dispatches a vtable on the object's own first dword.** The oracle's listing
  starts an instruction at the site in 55 rows, and it is `call DWORD PTR [reg+0x4c]`/`[reg+0x50]` in all 55. The
  other 51 sit inside a `lea`'s displacement and **all 51 are slot 20**: every slot-19 hit (31) is a call, so
  `+0x4c` is the specific displacement and `+0x50` the noisy one. The read now covers all 55 -- **53 receiver =
  `mov reg,[reg2]`** (24 through `ecx`, 14 `esi`, 8 `eax`, 4 `edi`, 3 `ebx`), **2 = `mov reg,[ebp]`**, **0
  unread** -- and all 55 decode from the function's own start, so the receiver is the last write to the register on
  the way in.
- **The control is the rows the narrow path already read.** On the 40 rows whose narrow sweep landed exactly on the
  call, the oracle sweep picks the same defining instruction in **40 of 40** and the same source shape, so the 15
  recovered rows are read the way the 40 always were.
- Tool (`tools/vcall-receiver.py`: `parse_objdump`, `written_regs`, `oracle_sweep`, `objdump_read`, the `oracle`
  column, `--oracle`/`--no-oracle`, and selftest cases for a spanned site, an oracle-only sweep and an unwritten
  register) + `inventory/vcall_receiver_sites.csv` regenerated (106 rows) + `docs/NATIVE_CLASS_TREE.md` +
  `docs/UNKNOWNS.md` U8. What is left is the object pointer's own provenance (`this` / argument / field / local),
  and the run-time class, which no boot on a stock disc can reach.

### Execution notes (2026-11-12, the 106 pattern hits are 48 calls, and every one is a vtable dispatch)

- **Half the census was displacement bytes.** `tools/vcall-receiver.py` (`inventory/vcall_receiver_sites.csv`,
  106 rows) reads the instruction that loads the register each `.text` `+0x4c`/`+0x50` hit dispatches through, and
  first tests whether the hit is an instruction boundary at all: **58 of 106 are not** (slot 19: 12 of 31, slot 20:
  46 of 75). At `0x002d0d6e` the bytes are `8d 95 e8 fb ff ff 52 50` -- `lea edx,[ebp-0x418]`, `push edx`,
  `push eax` -- so the `ff` the earlier census read as `call [edx+0x50]` is that `lea`'s last displacement byte.
  Each such row keeps the instruction that spans it, so the correction names all 58 instead of dropping them.
- **Of the 48 real sites, 38 load the receiver from the object's first dword** (a two-byte `mov reg,[reg2]`:
  `eax,[ecx]` 8, `eax,[esi]` 6, `edx,[ecx]` 6, `edx,[eax]` 6, `edx,[esi]` 5, then single rows), 2 from `[ebp]` (a
  stack local) and **8 stay unread** with their bytes kept. The pair is therefore called as `call [[obj]+0x4c]` on
  the object's own vtable -- 14 of the 38 through `ecx`, the member-function `this` -- and no site reaches it
  through an absolute class table or a global, which is what makes slots 19/20 a C++ virtual pair of the 165
  `Actor`-subtree tables rather than a separate `UClass` dispatch path.
- **The read is exact-length and says what it could not do.** 40 rows decode from the function's own start and the
  sweep must land exactly on the call; the other 66 fall back to a backward walk from the site, and an opcode the
  narrow decoder does not know stops the sweep rather than shortening an instruction. `--check` covers the verdict
  table on synthetic bytes: every source shape, an unwritten register as `unread`, and a site with no chain of
  instruction ends behind it as `not-a-boundary`.
- Tool (`tools/vcall-receiver.py`) + `inventory/vcall_receiver_sites.csv` + `docs/NATIVE_CLASS_TREE.md` +
  `docs/UNKNOWNS.md` U8. What is left for U8 is the one question the static read cannot answer: which class an
  arbitrary `[obj]` belongs to at run time -- the multiplayer/dedicated path a stock disc never reaches.

### Execution notes (2026-11-12, the harness can judge a console step, and its crop cannot)

- **The console step is part of the sequence now.** `drive.py --step=console=NAME[:want]` captures `NAME.png` and
  judges it with `tools/xemu/console-line.py` instead of with the repaint delta: `want` is `drawn` (default), `typed`
  or `absent`, and the report line carries the state, the pixel count and PASS/FAIL. The `shot=` step it replaces
  measured **0 px and FAILed** on a line that was visibly drawn, twice in two runs, because `--crop` is the menu panel
  (`480x520+760+210`) and the console draws at the bottom left -- a FAIL that a reader could only resolve by hand.
- **`UNREAD` is not `absent`.** A frame the tool cannot read (`console-line.py` missing, or its output unrecognised)
  is a FAIL with `UNREAD` in the reason, so a silent instrument never reads as a console that drew nothing.
- `drive.py --selftest` (in `make ci`) covers the verdict table: `typed` for a typed line, not for an empty prompt,
  `absent` for nothing drawn, `other` for cyan that is not a line, and `UNREAD` for a state of `None`.

### Execution notes (2026-11-12, `RestartLevel` completes the unload and the pair stays silent through it)

- **The graceful unload exists, and it is one console command.** `RestartLevel` is an `exec function` on
  `PlayerController` whose standalone branch is `ClientTravel("?restart", NM_ListenServer, false)`. Typed into a
  loaded `M00` -- `tab`, `spc`, the word key by key, the line read off the frame as `(> restartlevel_` (163 cyan px,
  `242x33`, `console-line.py --require=typed`) -- and submitted with `ret`, it took the level down and brought it back
  with the guest alive: `Loading... M00` / `Starting Story Mode Game` at +5 s, the `O'NEILL` HUD at +20 s, and M00's
  own tutorial modal at +45 s (identical at +85 s, the modal's documented input freeze). So the level restarted from
  its script, not from a checkpoint, and no rebuild was needed to get a second load into one boot.
- **The pair recorded 0 stops across the whole window.** Armed at the title with the control on the sampled `EIP`
  (`0x003ed669`, fired at once), both bodies stayed silent through the select, the `M00` load, gameplay, the
  teardown and 200 s of the reloaded level (380 s window). A full actor teardown not running either body rules out
  the reading that slots 19/20 are a per-actor lifecycle virtual -- `Destroy` would have run thousands of times.
- What is left for U8 is the multiplayer/dedicated path, which a stock disc boot cannot reach; the next instrument
  is a typed receiver at one of the 106 dispatch sites, not another boot.
- Tool (`tools/xemu/restart-level.sh`: the boot-to-level sequence, the console command, the frame verdicts and the
  optional pair arm) + `docs/XEMU.md` + `docs/UNKNOWNS.md` U8 + `docs/NATIVE_CLASS_TREE.md`. No artifact: the record is
  the run directory under `$WORK/rlevel1`.

### Execution notes (2026-11-12, the last two windows: a checkpoint load resets the guest, and the menu holds still)

- **The checkpoint-load window is covered, and the route itself is the finding.** From a loaded `M00` the run typed
  `checkpointload c_m02a_drillsite.sgs` at the console (an `exec function` on `PlayerController` that issues the
  engine's own `LoadCheckPoint` command, checked in the extracted `Engine/PlayerController.uc`), and the line was
  read off the frame rather than assumed: `tools/xemu/console-line.py` counted **400 cyan px in a 542x30 box** and the
  crop read `(> checkpointload c_m02a_drillsite.sgs_`. `ret` submitted it -- the line was gone 50 s later -- and the
  frame at that point was the **BIOS prompt** (`Please insert an Xbox disc...`): the guest reset. So a checkpoint of
  *another* level, taken from a shipped profile, does not load; the sequence unloads the level and leaves the console
  with nothing to boot. `RestartCheckpoint` only ever composes `c_<current level>_<checkpoint>`, so this is
  consistent with a checkpoint being loadable only inside its own level, and it means U16's "the only routes are
  `Continue` and checkpoint loads" is now measured down to one route.
- **The pair recorded zero stops across it.** `tools/xemu/bp-args.py --wait-user --control-self --window 900` armed
  both bodies at the title (control on the sampled `EIP` `0x00030688`, which fired at once) and the two bodies stayed
  silent through the map-list select (22,358 px), the `M00` load, gameplay and the checkpoint-load command. The
  post-reset silence is **not** part of the evidence: a `Z0` lives in guest memory and a reset remaps it, so the
  window ends where the guest resets.
- **The graceful unload did not happen, and the menu is what stopped it.** A second boot opened the mid-game menu
  with `pad=start` and stepped it: `05-menu-down` 575 px, `06-menu-down` 1,188 px, then `07` and `08` **0 px** -- the
  selection had reached the bottom of the list -- and `pad=a` took a confirmation (3,570 px, and byte-identical 20 s
  later, so it was not a level change). Two presses per list is already past its end, so the exit is not the item
  that three downs land on. The pair again recorded **0 stops** (four 60 s timeouts inside a 420 s window, control
  fired at once, dump `ecx` -> `0x004cfba0`).
- What is left is now one window and one instrument gap: a *completed* unload (the menu's own exit, read off a frame
  before pressing, or `RestartLevel` from the console) and the multiplayer/dedicated path.
- Tool: none this round -- the run used `bp-args.py` and `console-line.py` as shipped, and the record is the two
  `$WORK/vslot8`, `$WORK/vslot9` directories. Docs: this note, `docs/UNKNOWNS.md` U8 and U5, `docs/XEMU.md`.

### Execution notes (2026-11-11, the read-back was blind; the control proves the arm, and the pair is a true zero)

- **The previous note's negative was an artefact of the instrument.** `bp-args.py`'s byte read-back cannot witness a
  `Z0` on this stub: a control breakpoint armed at the sampled `EIP` stopped on that address while its read-back was
  still the body's own byte (`8b`, twice), and the two measured bodies read back `f6`/`56` unchanged for the same
  reason. So "the `0xCC` went into a mapping the game never executes" is withdrawn; what the round measured was a
  breakpoint that was registered and never executed.
- **`CPL 3` is unreachable and the docs asked for it.** 88 context samples over one boot were all `CPL 0`, 84 of them
  inside the image (`0x00454510`, `0x00463864`, `0x000c8010`): the front end, the menus and the game all run at ring 0
  here, so a ring gate waits forever. `--wait-user` now gates on the image range and armed on its first sample in both
  boots (`0x000544e8`, `0x002bb740`).
- **The pair is a true zero in every window covered.** With the control firing at once, the two bodies
  (`0x000c4f60`, `0x000c6d00`) recorded 0 stops over 420 s that spanned the map-list select, the M00 load
  (`02-mission` 20,414 px) and 180 s of gameplay (`03-gameplay` 6,829 px). Covered windows: front end, map list,
  level load, gameplay. Not covered: level unload/travel, a checkpoint load, multiplayer.
- **A per-hit timeout was short-circuiting the measurement.** The first boot's tool exited 60 s after the control, in
  the middle of the select; `--window` now keeps the stop loop open across quiet `--timeout` periods, which is what
  turned the front-end zero into a load-and-gameplay zero.
- Tool (`tools/xemu/bp-args.py`: image-range `--wait-user`, `--control-self`, `--window`; `tools/vcall-stops.py`:
  the `control` label, kept out of the slot counts) + `docs/UNKNOWNS.md` U8 + `docs/NATIVE_CLASS_TREE.md` +
  `docs/XEMU.md` (two stub behaviours). No artifact: the record is a run artifact under `$WORK`.

### Execution notes (2026-11-11, the pair's dispatch census: 106 `.text` sites, all of them the object layer)

- **The 82 was an undercount, and the census says where the pair is called.** `tools/vcall-dispatch.py`
  (`inventory/vcall_dispatch_sites.csv`, 141 rows) decodes every `call [reg+disp]` form in the nine executable
  sections and keeps the two displacements a class table's slots 19/20 occupy (`19*4 = 0x4c`, `20*4 = 0x50`).
  `.text` -- the game's own code -- holds **106**: 31 at `+0x4c`, 75 at `+0x50`, through edx 37, eax 30, ecx 15,
  edi 12, ebx 8, esi 4 and never `ebp`. The 35 in the seven statically linked library sections are the control:
  33 of them sit at `+0x50`, a field of some library struct, and no table of this build is dispatched there, so
  `+0x50` is the noisy displacement and `+0x4c` the specific one.
- **Every named function that flag hits is `Core`'s object machinery.** 14 sites sit inside a named body:
  `Core_UnObjBas` 5, `Core_UnTemplate` 2, `Core_UnClass`, `Core_UnObj`, `Native_65`, two
  `MasterServerUplink` destructors, and offsets into `Engine_UnActCol` and `IpDrv_InternetLink`. Nine of the
  ten values are `UObject`/`UClass` translation units, which matches the bodies read last round (they take
  `[this+0xa4]`, then `[that+0xf4]`, and call a virtual on `this` at `+0xc`/`+0x8`): the pair belongs to the
  object/class layer, not to gameplay code.
- **The runtime half is started and its first reading is a zero.** An execution breakpoint on each body armed
  `OK` at a loaded `M00` (`tools/xemu/bp-args.py --arm-after 0`, print of both `OK`s in
  `~/sg1/work/vslot/bp.log`) and **recorded 0 stops in 30 s** of the level, so the pair does not dispatch while
  a level idles. The arm that would sit *before* a level loads -- the window in which an `Actor`-installed pair
  should fire -- cannot be taken by that tool: the front end idles in the kernel (`EIP=0xfffffe5c`,
  `0x80065996`, `0x80024a97` ten seconds apart at the title) and `bp-args` refuses a kernel-context arm. A
  forced arm needs a read-back of the patched byte, because the stub's `OK` is not evidence that the `0xCC`
  landed in the mapping the game executes; `bp-args.py --force` now does both -- it arms in whatever context
  the CPU is in and reads the byte back into its record (`armed_bytes`) -- and the read is negative: the patch does
  not reach the game's mapping (execution note below).
- **Two boots were lost to the desktop's display, not to the measurement**: `drive.py` answered `no xemu window
  on :0` while `xdotool search --onlyvisible --name .` listed 0 of 9 windows. That is a session state, so the
  guest read is unfinished rather than negative.
- Tool (`vcall-dispatch.py` scans with synthetic `--check`; `vcall-stops.py` reads stops with a synthetic
  `--check`) + `inventory/vcall_dispatch_sites.csv` + `docs/NATIVE_CLASS_TREE.md` + `docs/UNKNOWNS.md` U8.
  The stray half-line the previous round left under 4.3 ("shared constructor, and that is now the written next
  step") is removed here.

### Execution notes (2026-11-11, a registration carries a name and never a table, and slots 19/20 are one Actor-level pair)

- **PLAN 4.3's "fourth/fifth pushes" is measured, and it cannot name a table.** `tools/class-table-residual.py`
  (`make class-table-residual`) decodes every `68 imm32` before each of the **493** registration calls and classifies
  the immediate: all 493 call the same function (`0x0003af60`), **493 push at least one string** (median 8 pushes,
  range 7-10), **493 push `0x04084004`**, 3 push a function start, and **0 push a class table**. `Property`'s site at
  `0x000778d9` reads `push 0x624d0` (a one-byte `ret`), `push 0x34650` (the null-check thunk onto `0x62ce0`),
  `push 0x4084004`, `push "System"`, `push "Property"`, `push 0x6c` -- the table arrives through the constructor,
  never as an argument. The decode is validated by a second artifact rather than by itself: the `literals` column
  `registration_sites.csv` mines independently equals the string pushes on **493 of 493** sites, in order.
- **The 27 shared-default sites are the engine base classes, and their name strings are read**: `Exporter`,
  `Factory`, `Field`, `Object`, `Subsystem`, `Language`, `Property`, `VoiceSubsystem`, `Viewport`, `CameraEffect`,
  `Client`, `Channel`, `RenderDevice`, `Engine`, `LevelBase`, `Material`, `BitmapMaterial`, `RenderedMaterial`,
  `NetDriver`, `PendingLevel`, `Player`, `RenderResource`, `VertexStreamBase`, `Texture`, `FractalTexture`,
  `WaterTexture` (each outered to `System`), plus the site `0x0010d6a9` the census leaves unnamed, which pushes
  `NetConnection` outered to `User`. Name known, table not: the two questions are separate.
- **Slots 19 and 20 are one pair, installed at `AActor`.** `--slots` reads both from all 492 tables: **two distinct
  pairs, no third** -- `0x00020f00` twice (a one-byte `ret`) in **327**, `0x000c4f60`/`0x000c6d00` (40 and 69 bytes)
  in **165**. All 165 are under `Actor` by the script super chain with `Actor` itself among them, and **all 26
  unnamed tables are in the 327**, so the division is the `Actor` subtree against everything else. Both bodies test
  `[this+0x2dc] & 1`, take `[this+0xa4]`, then `[that+0xf4]`, and call a virtual with `this` (`+0xc` / `+0x8`, the
  second emptying a 4-byte-stride array at `+0x100` first) -- a delegating notification through two pointer fields.
- **U8's dispatch-site route is counted and is not the next instrument.** `.text` holds 82 `call [reg+0x4c]` /
  `[reg+0x50]` sites, but the displacement is not unique to this table, so each needs its receiver typed first --
  the same missing field name. A breakpoint at the two bodies in a booted guest is what would settle it.
- Tool (`sites`/`slots` modes, synthetic `--check` needing neither dump nor artifact) +
  `inventory/registration_arguments.csv` (493 rows) + `inventory/class_table_slot_pairs.csv` (492 rows) +
  `docs/NATIVE_CLASS_TREE.md` + `docs/UNKNOWNS.md` U8 + the `make class-table-residual` target.

### Execution notes (2026-11-11, the control is a flag — a shuffled declaration still places 2,123 of the receiver sites)

- **The shuffle is `--calls --shuffle` now, not a paragraph.** `shuffled_source` rotates a class's own declared types
  (its `local <Type> <Name>;` lines and its return types, one sorted pool) onto the next name before the walk, so the
  source keeps the same functions and the same type spellings and loses only the binding that placed a site. It
  writes no CSV, and `--selftest` asserts the rotation moves every value and keeps the pool -- a control that dropped
  a type would make its own collapse unreadable.
- **What it measures.** The whole corpus: by-name sites naming a declaring class **13,258 of 13,507 (98.2%)** as
  shipped, **12,753 (94.4%)** shuffled; the receiver group **2,641 -> 2,123**, the sites fronted by a *typed local*
  **582 -> 80** (-86%), the case-folded placements **33 -> 10** and the call-result placements **72 -> 18**.
  `Engine.u` alone: receiver group **357 -> 264**, typed-local front **117 -> 25** (-79%). So a local placement is the
  declaration deciding about six times in seven.
- **The survivors are the honest half of the result: 2,123 receiver placements do not move**, because a rotated type
  still names some class whose chain declares that name (`ClientMessage`, `GetItemName`, `GetTitle` sit on dozens of
  chains), and the properties, imports and array elements the shuffle never touches stay placed by construction. The
  control therefore bounds the *marginal* evidence a declaration adds; it does not license the claim that every
  receiver placement is unique, and the page says so.
- Tool (`shuffled_source`, `_SHUFFLE` in `class_source`, the `--shuffle` flag and its guard, a selftest) +
  `docs/BYTECODE.md` + `docs/UNKNOWNS.md`. `inventory/script_calls.csv` is untouched: a control run is not a census.
- The static call table is dry -- the 41 multi-descendant sites in the residue are a dispatch only the runtime can
  settle, so the next task is off this table.

### Execution notes (2026-11-11, the source's own spelling — a name matched without case places 53 sites, and a call result is typed by its callee)

- **A call's result is a receiver, and its type is the callee's declared return type.** `GetOwner().Foo()` carries no
  variable to type: the receiver is the first call's *result*, and the only statement of its type is the callee's own
  declaration (`function Actor GetOwner()`), which `source_returns` now reads off the class `ScriptText`; a
  `FinalFunction` names its Function export (the callee's class is that export's outer, a `State`'s function belonging
  to the class that owns the state) and a `VirtualFunction`/`GlobalFunction` callee is resolved on the caller's chain.
  The three call tokens had to join `OBJECT_TOKENS` or the branch is unreachable. **822 sites** front a by-name call
  with another call and the reading is asked at every one: **72 type** (`sound` 33, `PlayerController` 22,
  `SGFormation` 6), **534 the callee returns nothing**, **131 the operand names no Function of this package** (an
  imported or native callee -- the declaration is in another package), 72 a value that is not a class, 11 no
  declaration, 2 not on the caller's chain. Only **4** of the sites it places are call results: the residue it was
  pointed at is 39 sites and 22 of them are statement tails (`JumpIfNot` 1,250 asked, `Let` 520, `Jump` 318), not
  receivers at all -- the rule is right and this residue is nearly dry.
- **A local's declaration can be in an ancestor's source, and the compiler folds case while the source does not.**
  `local_chain` reads the ancestor's `ScriptText` for the same function, the walk a by-name self-call takes, and types
  **7** (three classes: `Engine.Vehicle` 3, `Engine.Actor` 2, `StargateGame.DeathMatch` 2). The larger half is
  spelling: `_folded` matches a declared name the way the class index already does and **149 sites type from a
  declaration the source spells in another case** (`Msg` 30, `t` 13, `i` 12, `Dir` 8, `Image` 7) -- the same defect as
  `pawn` and `class<LocalMessage>`, and it takes the untyped local bucket **81 -> 28**.
- **By-name placement is 13,258 of 13,507 (98.2%)**, residue **306 -> 249**: 181 an object expression that names no
  class (28 a local neither its class nor an ancestor declares, 70 a literal or cast, 40 an array element of a
  non-class array, 25 not a variable, 18 a call result with nothing to type), 46 typed to a class whose chain and
  subtree lack the name (`Object` 36 is a dispatch over every class in the build) and 22 a completed value in front.
  The receiver's chain goes 2,584 -> **2,641** (624 names, 267 distinct receiver classes), the receiver-typed local
  share 529 -> **582**, and **57 sites place off the receiver: 53 with a local in front, 4 with a call result**.
- Tool (`source_returns`, `_folded`, `class_source`'s cached per-class source read across packages, `call_result` and
  `local_chain` in `_receiver_reading`, the call tokens in `OBJECT_TOKENS`, five report lines) +
  `inventory/script_calls.csv` regenerated (18,931 rows) + `docs/BYTECODE.md` + `docs/UNKNOWNS.md`.

### Execution notes (2026-11-11, an array element receiver is its array's element type)

- **430 of the 469 "completed value in front" sites were `A[i].Foo()`.** The token in front of the call is
  `ArrayElement` 349 / `DynamicArrayElement` 81, its children are the **index and the array** (measured, not
  assumed), and the element's class is the array's declared element type -- `array<Pawn>` unwrapped by the
  `_class_of_type` that already accepted `array<>`. **The reading branch alone changed nothing**: `--calls` only asks
  `_receiver_reading` about a token in `OBJECT_TOKENS`, so the new branch was unreachable until those two tokens
  joined the set -- a site that types to nothing and a site nobody asked about are indistinguishable in every count.
- **With the set fixed: 501 sites type over 49 (token, class) pairs and 390 place.** Receiver's chain 2,194 ->
  **2,584**, by-name placement **12,811 -> 13,201 of 13,507 (97.7%)**, unplaced residue 696 -> **306**, "completed
  value in front" 469 -> **39**.
- **A declared type with a struct's inlining flags had been read as a class name.** `_class_of_type` answered
  `export editinline Order` / `editinlinenotify GUIComponent` for the tagged type fields, so 21 sites sat on a class
  no table has; a class is one identifier, so a type with a space or an angle bracket is now "not a class" and no
  placement moves (untyped residue 187 -> 221 across the sanitisation, 306 unplaced either way).
- Left: 221 expressions naming no class (81 a local its source does not declare, 70 a literal or cast, 40 an array
  element of a non-class array, 30 not a variable), 46 typed with no declarer (`Object` 36 is a dispatch over every
  class in the build) and 39 a completed value in front. **Next instrument: a callee's declared return type** (12
  `FinalFunction`, 5 `VirtualFunction` receivers) off the same `ScriptText` -- not attempted.
- Tool (the two tokens in `OBJECT_TOKENS`, the `ArrayElement` branch, the identifier rule in `_class_of_type`, the
  `array_fixed` counter and report line) + `inventory/script_calls.csv` regenerated + `docs/BYTECODE.md` +
  `docs/UNKNOWNS.md`.

### Execution notes (2026-11-11, the import's owner class settles the property vote)

- **A disagreeing property import is typed by the class its outer names; requiring the corpus to vote was the wrong
  rule.** An import's `OuterIndex` is a negative import index that names the declaring class -- the declaration the
  compiler resolved against -- so a vote between same-named properties of unrelated classes (`UIWidget.Type`,
  `GUIText.Type`) was never a tie to break by count. `_receiver_reading` now prefers the owner's own record: **423
  receiver readings** come from a disagreeing import (`Pawn` 177, `Actor` 57, `GUI` 57, `LevelInfo` 46, `GUIListBox`
  37, `BotBase` 19; 15 owner classes), the **240 unplaced sites** that were the residue's whole import bucket all
  place, the receiver's chain goes 1,954 -> **2,194**, the unplaced residue 936 -> **696**, and by-name placement is
  **12,811 of 13,507 (94.8%)**.
- **The control is the cases where the corpus already agrees, and it is clean.** At the 698 sites whose property every
  declaring package types the same way, the owner's own class agrees 698 of 698 and differs 0 -- preferring the owner
  adds no disagreement with a consensus, it supplies the vote a tie was missing. That is the check to ask for before
  trusting the 240.
- What is left is 181 expressions naming no class (81 a local its source does not declare, 70 a literal or cast, 30
  not a variable; no import), 46 typed to a class with no declarer (`Object` 36 is a dispatch over every class in the
  build) and 469 with a completed value in front -- all questions about which object, not about the build.
- Tool (the owner map into `_receiver_reading`, the `owner_fixed` / `owner_agree` / `owner_conflict` counters and
  their report lines) + `inventory/script_calls.csv` regenerated + `docs/BYTECODE.md` + `docs/UNKNOWNS.md`.

### Execution notes (2026-11-11, the property import branch was dead code — the tagged stream writes the C++ `U`)

- **The 473 "property no package declares" were a suffix mismatch, not a missing declaration.** The tagged property
  stream writes the property class as `UObjectProperty`; an import writes `ObjectProperty`. An equality test between
  the two rejected every property import in the corpus, and the branch it guarded indexed a fourth field of a
  three-field tuple, so it could never have run. Strip the prefix and **233 more receivers type**: receiver chain
  1,757 -> **1,954** (569 names, 249 distinct, `Pawn` 260), untyped-object residue 654 -> **421**, by-name placement
  **12,374 -> 12,571 of 13,507 (93.1%)**.
- **What is left of the imports is a vote, and the vote is the wrong rule.** 240 sites name a property two or more
  packages declare with *different* types (`UIWidget` 61, `HaakenMK2Weapon` 50, `Controller` 48, `InitComponent` 20);
  the import's own outer names the owning class (`Pawn` 33, `GUI` 30, `GUIListBox` 16, `ScriptedController` 9), so the
  next instrument is to prefer the owner's declaration over requiring the whole corpus to agree.
- The typed-unplaceable residue is 52 before the subtree step and 46 after (6 place on `StargateGame.BotEnemy`),
  receivers `Object` 36, `BotBase` 6, `UnrealScriptedSequence` 3, `Pawn` 1 -- `Object`-typed receivers are a dispatch
  over every class in the build, not a lookup.
- Tool (`_kind_key`, the import-owner index and the conditional report lines) + `inventory/script_calls.csv`
  regenerated + `docs/BYTECODE.md` + `docs/UNKNOWNS.md`.

### Execution notes (2026-11-11, the call residue halves — the index was keyed on the state and the class on its case)

- **Two keyings, not a measurement, closed 24 of the 47.** The by-name index was built from a function's *outer*
  export, so a function declared inside a `State` was indexed under the state's name -- a name no super chain walks --
  and 223 sites that call one resolved nowhere; keyed by the owning class it places them (own class +197, ancestor
  -127, because the nearer declarer is the state the caller's class owns), for **8,321 on the caller's chain**. And
  the class lookup was case-sensitive while the shipped source spells `pawn`/`inventory`/`GameHUD`: folding the case
  places **26** more receiver sites and types 25 more locals, for **1,757 on the receiver's chain**.
- **The residue the last note named is then 16, and only 10 of those are a dispatch.** A base-typed receiver can hold
  any subclass, so the tool now looks *below* the receiver's chain as its last step: **6** place on the one descendant
  that declares the name (`IsUnconscious`, `GetAggroLevel` -> `StargateGame.BotEnemy`, the first declared inside
  `state BotUnconscious`); the rest are `CallToArms` 3, `UnPossess`, `Possess` among several descendants and
  `GetMoveTarget` 2, `GetMyPlayer`, `TakeOver`, `SetPawnClass` outside the subtree. **No name in it is absent from the
  corpus** -- the gap is which object, not which build, so do not look for a name table fix here.
- **By-name placement is 12,374 of 13,507 (91.6%)**, and the 1,133 unplaced are 654 expressions that type to nothing,
  10 typed-and-unplaceable, and 469 a completed value in front. The next instrument on this table is the 473 imports
  no package declares, which needs the native property table (`docs/NATIVE_PROPERTIES.md`), not a better walk.
- Tool (`--calls`: the class-keyed index, `class_pairs`' case folding, `place_below` and the `descendant:` column) +
  `inventory/script_calls.csv` regenerated (18,931 rows, 400 of them moved) + `docs/BYTECODE.md` + `docs/UNKNOWNS.md`.

### Execution notes (2026-11-11, the residue has names — 474 cite a property no package declares, and the type syntax has two spellings)

- **A residue site is now counted by the reason its expression typed to nothing**, and the tool's counts add up:
  **656** unplaced sites with an object expression that names no class, **47** that typed to a class whose chain
  declares no such name (`Pawn` 14, `BotBase` 14, `GameHUD` 4, fifteen more elsewhere) and **489** with a
  completed value in front -- 1,192 in all, so **12,315 of 13,507 (91%)** by-name sites name a declaring class.
  The two labels used to overlap (a receiver that typed and still did not place was counted as "no recorded
  type"), which is why the untyped line reads 656 and not the 717 of the previous note.
- **The largest bucket is not a script gap.** Of the 656, **474 reference a property no package in the corpus
  declares** (`UIWidget` 80, `HaakenMK2Weapon` 59, `Instigator` 56, `Controller` 48, `Pawn` 41, `Outer` 36 --
  several are the `Actor`/`Pawn` properties a rebuild inherits from C++), **81 a local whose class's own source
  has no declaration for the name**, **71 a literal or cast that names no class** and **30 a token that is not a
  variable**. An import names an object in another package, so the 474 are declared outside `System` or
  natively: the reading that would type them is the native property table, not a better walk -- a negative
  worth carrying rather than re-measuring.
- **The split found a spelling bug, which is what it was for.** `_class_of_type` accepted `Class<>` and
  `array<>` but the scripts spell `class<LocalMessage>` lowercase, so a class-typed local came back as a class
  named `class<LocalMessage>` and placed nothing; accepting both spellings places **14 more sites** (receiver
  group 1,718 -> 1,732, local share 490 -> 504).
- Tool (`_receiver_reading`'s reason, the two histograms and the split labels) + `inventory/script_calls.csv`
  regenerated (18,931 rows) + `docs/BYTECODE.md` + `docs/UNKNOWNS.md`. The next instrument on the call table is
  the 47 sites that typed to a class whose chain lacks the call name: that is a dispatch question, not a typing
  one, and the receivers are `Pawn`, `BotBase` and `GameHUD` objects.

### Execution notes (2026-11-11, a local is a record the function owns and its type is in the shipped source — 490 more sites, 91% placed)

- **A local's operand is an export, not a table index.** Every one of the 540 local receiver sites in `Engine.u`
  resolves to a property export whose **outer is the calling function** (389 `ObjectProperty`, 62 `IntProperty`,
  37 `StrProperty`, 23 `ClassProperty`, 9 `StructProperty`, 7 `ArrayProperty`, 6 `BoolProperty`, 4
  `FloatProperty`, 3 `ByteProperty`), so the function's own children are its parameters and locals and the
  record's name is table fact.
- **The record's payload does not name the type in a field this build writes readably.** Of the 281
  `ObjectProperty` locals whose source type is known, no payload offset names that class for more than 30 of them
  (offset 4: 30, offset 5: 16, offset 3: 8), so a guessed `[ArrayDim][class reference]` layout would be wrong two
  times in three -- measured and rejected rather than assumed.
- **The class carries the source it was compiled from, and that binds the name.** `--calls` now parses each
  class's `ScriptText` once per package (`local <Type> <Name>;` and the parameter list, `event` as well as
  `function`, bodyless `native` declarations contributing only their parameters) and answers a local's type from
  it. **490 of the 628 local sites are placed on the receiver's chain**, untyped locals fall **628 -> 138**, and
  by-name sites naming a declaring class go **11,811 -> 12,301 of 13,507 (91%)**; the receiver group grows
  **1,228 -> 1,718 sites over 540 names**, 237 distinct classes. The residue is **1,206**: 717 with an untyped
  object expression (138 locals, 579 properties) and 489 with a completed value in front.
- **The control is a shuffle, and it is the reason to believe the placements.** Permuting the declared types
  inside each class and re-running the same walk collapses `Engine.u`'s local placements **82 -> 13** (its whole
  receiver group 313 -> 244), so a placement is the declaration doing work, not a name that would land on any
  chain.
- Tool (`--calls`, `source_declarations` and the per-function local lookup) + `inventory/script_calls.csv`
  regenerated (18,931 rows, 12 columns) + `docs/BYTECODE.md` "The calls the scripts make" + `docs/UNKNOWNS.md`'s
  call-site entry. The next instrument is the 579 untyped properties, whose declarations are in the tagged
  property stream `class_properties.csv` is read from.

### Execution notes (2026-11-11, the receiver is the expression in front of the call — 1,228 more sites, and a state's function belongs to its class)

- **The site carries its receiver, and it is not an operand.** A by-name call names its target and nothing else,
  because the compiler has already emitted the object expression the VM dispatches on, so the **expression
  immediately in front of the call token** is the receiver (the previous sibling in the token tree). `--calls` now
  reads it and, when the token can name a class (a property's declared type from `class_properties.csv`, a cast's
  target, a `Context`'s inner expression), places the call on the **receiver's** chain: **1,228 of 13,507 sites (9%)
  over 419 names**, 198 receiver classes (`Pawn` 80, `UIWidget` 76, `SquadAIBasic` 65, `Controller` 51,
  `UWindowComboControl` 50) — `Engine.Touch ClientMessage()` has a `Pawn` in front and lands on `Engine.Pawn`;
  `Engine.PlayerTick PlayerInput()` lands on `Engine.PlayerInput`.
- **A function inside a `State` belongs to the class that owns the state, and that correction came first.** The
  census attributed such functions to the state's name, where nothing declares anything; with the class as the owner
  **869 sites moved onto the chain (7,382 -> 8,251)** and the sites with *nothing* in front of them fell from 145 to
  **17** — a self-call always has a declarer on its chain, so the collapse is the check that the attribution is
  right. The corpus-only lookup shrinks to **2,332 (945 names)**.
- **What is left is a typing gap, not a dispatch gap.** 1,696 sites over 412 names: **1,207** have an object
  expression whose type the corpus does not record — **628 a local variable** (a local's type is not a class
  property, so not in `class_properties.csv`) and 579 a property the script does not type — and **489** have a
  completed value in front (a self-call the corpus cannot complete, or a receiver the walk did not reach). By-name
  sites naming a declaring class: **11,811 of 13,507 (87%)**. Ambiguity measured: **820** chain-resolved sites (10%)
  would also be placed by the expression in front, so the pass prefers the chain reading and records the receiver
  class in every row it does place. The next instrument is the local-variable declarations: 628 sites.
- Tool (`--calls`, the receiver pass and the state-owner attribution) + `inventory/script_calls.csv` regenerated
  (18,931 rows, 12 columns: `receiver`, `receiver_type`) + `docs/BYTECODE.md` "The calls the scripts make" +
  `docs/UNKNOWNS.md`'s call-site entry.

### Execution notes (2026-11-11, the script side halves the cut-content residue, and 592 classes nothing places are named by code)

- **The reach census only asked the content, so a class no map builds looked invisible.** `tools/class-reach.py`
  now counts two script-side signals as well: a class named in bytecode -- `tools/uscript-dis.py --class-refs`
  (`make class-refs`) resolves every `ObjectConst`/`DynamicCast`/`MetaClassCast` operand against the export and
  import tables and writes `inventory/script_class_refs.csv`, **5,587 class references over 675 distinct classes**
  (`DynamicCast` 4,153, `ObjectConst` 1,305, `MetaClassCast` 129; `Pawn` 591, `StargatePawn` 433,
  `PlayerController` 388) -- and a class used as a declared property type (`class_properties.csv`'s
  `friendly_type`: 429 classes, 1,908 declarations).
- **Of the 1,766 classes the four content artifacts could not see, 592 are named by the script:** 490 in code and
  102 only as a declared type, which leaves **1,174 (50.6%)** invisible to all seven. The content side is
  unchanged at 553 reached (23.8%), and the two script labels deliberately do not count as reached, because a
  `DynamicCast` target is a test rather than a use.
- **`class_reach.csv` gains `code_refs` and `type_refs` and two `reach` values** (`code-referenced`,
  `type-referenced`, ordered after `base-of-content` and before `unreferenced`), so a query for the strongest cut
  candidates is `reach=unreferenced` and nothing weaker.
- Tool (`--class-refs`) + `inventory/script_class_refs.csv` (5,731 rows) + `inventory/class_reach.csv`
  regenerated (15 columns) + `docs/CUT_CONTENT.md`'s reach section (the table and the residue paragraph) +
  `make class-refs`. `make ci` covers `class-reach --check` against the committed CSVs.

### Execution notes (2026-11-11, the by-name residue is a receiver question, and 44% of it needs no receiver)

- **The 6,125 "unresolvable" sites are not names the build lacks.** `--calls` now indexes every `Function` export of
  the 18 packages by name -- a function declared inside a `State` counted against the class that owns the state -- and
  resolves a site the caller's chain cannot place against that index: **2,712 of the 6,125 (44%), across 1,080
  names, reach a function exactly one class in the corpus declares** (`ShouldCrouch` -> only `Engine.Pawn` 63,
  `AddMenuItem` -> only `UWindow.UWindowPulldownMenu` 58, `SetPosImmediate` -> only `StargateGame.UIWidget` 51,
  `GetLocalPlayerController` -> only `Engine.LevelInfo` 40, `PostAIMsg` -> only `Engine.Controller` 40), so whatever
  the receiver is, the call reaches one implementation. The last **3,413 sites over 736 names** are declared by two
  or more classes (`SetText` 156 over 5, `SetFont` 139 over 4, `GetValue` 56 over 10, `WhatToDoNext` 52 over 16) and
  can only be placed by the receiver's static type, which the operand stream does not carry.
- **The previous note's 9,628 was an arithmetic error.** 5,136 + 2,246 = 7,382, and 13,507 - 6,125 = 7,382, so the
  chain places **7,382 (55%)** of the dynamic sites; `docs/BYTECODE.md` and `docs/UNKNOWNS.md` are corrected.
  `inventory/script_calls.csv` gains `declaring_classes` and `only_class`, and `resolved_in` grows a `corpus:Pkg.Class`
  value for the one-class lookup.
- Tool (`--calls`, the corpus-wide index and the three-way summary) + `inventory/script_calls.csv` regenerated
  (18,931 rows, 10 columns) + `docs/BYTECODE.md` "The calls the scripts make" (rewritten, with the correction) +
  `docs/UNKNOWNS.md`'s call-site entry.
### Execution notes (2026-11-11, a switch's operand byte is the width of the value it compares, and zero is a wide string)

`0x05`'s operand was the last unnamed byte on the bytecode page and the table's note called it a case count, which
the corpus contradicts: four cases and a `1`, sixteen cases and a `0`. The image's interpreter settles it.
**`execSwitch` is `GNatives` slot 5** (`0x0004ca20`, 348 bytes, `Native_5`), it reads the byte itself, evaluates the
expression into a 12-byte result buffer, and then per case reads a 16-bit offset and a value expression into a
second buffer and compares the two with **`FUN_00036bd0` -- `repz cmpsb` over its third argument** (no locals, a bare
`ret`). At byte `0` it calls **`__wcsicmp`** (`0x00036d00`) on the same two buffers instead, and destroys them as
`FString`s afterwards -- the teardown runs only in that arm, so the buffers are strings exactly when the width is
zero. So the byte is **the width, in bytes, of the value being switched on**, and zero is "a wide string".

**The corpus agrees three ways** (`make switch-operands`, `tools/uscript-dis.py --switches`,
`inventory/switch_operands.csv`, 105 switch sites in 11,602 clean-walking scripts): width 1 on 78 sites whose 273
case values are **all `ByteConst`** (`ByteConst` appears under no other width), width 4 on 20 sites whose cases are
the compact int tokens (`IntConstByte` 40, plus `NameConst` 8 and `InstanceVariable` 8), and width 0 on 7 sites, of
which `UWindowHTMLTextArea.GetHexDigit` carries the 16 `StringConst` cases and is the string switch.

**Residue, recorded not explained**: six zero-width sites (`UDebugMenu`/`UWindow` `Combo_SetupSizes`,
`Checkbox_SetupSizes`, `Editbox_SetupSizes`) and one four-width site (`StargateMenus.ButtonClick`) have no case list
at all -- no `0xffff` terminator -- which a switch cannot be; they are artifact rows with `ended=end`. The table's
`0x05` note is corrected in `tools/opcode-table.py` and `inventory/opcode_table.csv` in the same commit. The mode is
dump-side and therefore not in `make ci`; `--selftest` still covers the tool's wiring. This closes the `0x05` half of
3.7's operand-semantics residue; the other half (22% of virtual-call names) stands.

### Execution notes (2026-11-11, a quarter of the by-name calls are declared on an ancestor, and the rest need a receiver)

- **The call graph's dynamic half was asking one package the wrong question.** `--calls` resolved a by-name target
  against its own package's function list; it now walks the **caller's super chain in the package that declares each
  ancestor** (`inventory/classes.csv`'s `package_of_super`) and records the declaring class in a new `resolved_in`
  column of `inventory/script_calls.csv`. Of **13,507 dynamic sites, 9,628 (71%) name a function on the chain** --
  5,136 on the caller's own class, **2,246 on an ancestor** (305 `Engine.Actor`, 244 `Engine.Pawn`, 187
  `Editor.BrushBuilder`, 135 `Engine.Interaction`), which the old census had counted as "not declared locally".
- **What is left of the 2,993 is not a name-table problem.** Those were described as "a native parent's function is
  indistinguishable from a sibling package's until the hierarchy is consulted" -- the hierarchy places 2,246 of them;
  the other **6,125 cannot be placed from the site at all**, because a virtual call resolves on the *receiver's* class
  and no site names its receiver. The top of that residue is UWindow and PlayerController: `SetText` 156, `SetFont`
  139, `ShouldCrouch` 63, `AddMenuItem` 58, `GetValue` 56.
- **The consequence for a rebuild**: a by-name call does not necessarily target a function this build's own package
  declares -- 2,246 of them target a parent's -- so a recompiled package has to carry the ancestor declaration, not
  an invented local one.
- Tool (`--calls`, chain resolution + `resolved_in`, the summary broken down by declaring class) +
  `inventory/script_calls.csv` regenerated (18,931 rows, 8 columns) + `docs/BYTECODE.md` section "The calls the
  scripts make" + `docs/UNKNOWNS.md`'s call-site entry.

### Execution notes (2026-11-11, the native-call opcode is the `GNatives` slot itself, and 0xe7 is `Object.Log`)

- **The previous note's open question is settled, and its reading was off by `0x70`.** `tools/uscript-dis.py
  --native-calls` now scores three index rules side by side: the token itself, `op - 0x70` (what the first census
  used) and `op - 0x60`. Against the caller's own super chain, **42,056 of the 48,422 sites name a `native()`
  declaration under the token-as-index rule against 5,864 under `op - 0x70`** -- and the image's own `.data` record,
  the binder strings `intUObjectexec<Function>` that U7 already measured, spells the body at each token's slot:
  **111 of the 120 direct opcodes, 38,950 sites, name the declaration's function exactly**, the other nine only
  differing in spelling (`execConcat_StringString` for `Concat_StrStr`). Under `op - 0x70`, **0 of 120** match.
- **So there is no operator band and no second numbering.** The "38,143 sites on slots 0..95 that name nothing" are
  slots `112..207` and all declared; the one-byte form spans `112..255`, the declared band, and the two-byte form
  exists only beyond 255, which is exactly where every extended site sits.
- **`0xe7` is `Object.Log`, not `NotEqual_ObjectObject`.** Slot 231 takes one string and 1,072 of its 1,186 sites pass
  one argument; slot 119 (`0x77`) is `NotEqual_ObjectObject` with 5,368 sites of exactly two. The earlier row had the
  two swapped by the offset, which is why its arity never fitted. `0xfe` likewise reads as slot 254
  `Object.EqualEqual_NameName` (414/414 two arguments, `Tag == 'Foo'`), not `EqualEqual_RotatorRotator`.
- **What stays unresolved is the class half, not the index.** 6,366 sites (13%) name no declaration on the caller's
  chain: 1,480 are extended calls into engine classes the script never declares (`intUCanvasexecDrawText` 352,
  `execStrLen` 120, `execDrawActor` 8), and the rest are callers whose class the inventory's super map does not carry.
- Tool (`--native-calls`, three scored rules, `inventory/native_opcodes.csv` gains `body_name` and `body_match`) +
  `docs/BYTECODE.md` section "The opcodes above `0x5f` carry the native index itself" (its "Not measured" bullet
  corrected to the residue) + `docs/UNKNOWNS.md` U7's closure paragraph + `make native-opcodes`.

### Execution notes (2026-11-11, the opcodes above 0x5f are native calls and their arity is constant)

- **The page's last open opcode item is measured, not reserved.** `tools/uscript-dis.py --native-calls` reads every
  token at `0x60` or above as a native call whose slot is the token itself (`0x70 + n`, or `0x60 + (n >> 8)` then
  the low byte from `0x100`): **48,422 sites over the 11,602 scripts that walk clean**, 44,174 direct and **4,248
  extended, every extended one naming a slot of 256 or more** -- the two-byte form is used exactly where the
  one-byte form cannot reach.
- **The extended form resolves to its declaration**: `0x61 0x16` = 278 `Actor.Spawn` (465 sites), `0x61 0x17` 279
  `Actor.Destroy`, `0x61 0x18` 280 `Actor.SetTimer`, `0x61 0x2f` 303 `Object.IsA`; **5,864 sites name a `native()`
  declaration on the caller's own chain.**
- **Arity is constant per opcode, and that is the proof that one opcode is one native.** `0x77` 5,368/5,368 with
  exactly two arguments, `0xaf` 1,598/1,598, `0x9a` 1,538/1,538, `0x96` 1,433/1,433, `0xb0` 805/805 -- a per-class
  ordinal or a chain-decided slot would spread.
- **38,143 of the 48,422 sites are slots 0..95 and name nothing.** No declaration exists at those slots in any
  caller's chain, so they are engine-side natives emitted for the compiler's own operators; the 10,279 at slots
  96..143 are the declared half (`0xfe` 142 `Object.EqualEqual_RotatorRotator`, `0xff` 143 `Object.Subtract_PreInt`,
  312 of 331 sites with the two arguments it declares).
- **The arity is where the two numberings show, and it is the next question**: 1,174 of 5,864 resolved sites match
  their declaration's `parameter_count`, and `0xe7` passes one argument in 1,072 of 1,186 sites while its slot's
  declaration (`Object.NotEqual_ObjectObject`) takes two. Either an operator token's operands are not a parameter
  list, or slots 0..95 and the declared slots are two tables that meet at 96 -- settled by `GNatives` (U7), not by
  another walk.
- Tool (`--native-calls`, `-o` writes `inventory/native_opcodes.csv`, 206 rows with `args` as the arity histogram) +
  `docs/BYTECODE.md` section "The opcodes above `0x5f` are native calls, and each one has a fixed arity" (the
  "Not measured" bullet now states what is left) + `make native-opcodes`. Two slot rules are scored side by side
  (`0x70+n` against `op-0x60`), so the census reports the rule the declarations support rather than a guess.

### Execution notes (2026-11-11, a weapon's bullet is the one it inherits, and nine of the sixty-two never set one)

- **A reference is a setting; the resolved chain is the number a rebuild needs.** `tools/weapon-links.py
  --effective-out` resolves every hop through the super chain (`resolve()`: the nearest ancestor whose *own*
  block sets the tag) and writes one row per weapon: **69 weapons, 62 carry an ammo class -- 53 set it in their
  own block and 9 inherit it -- and 7 carry none.** That corrects the merged block's reading: the script-spawn
  sentence belongs to those 7 classes, not to everything that does not set an `AmmoName`.
- **The nine inherited bullets are the variant pattern, not a chain quirk.** `_Bot`/`_Enemy`/`_Weak`/
  `_JaffaCaptain` subclasses, `P90Torch` <- `P90Prototype`, `GatlingGun`/`HummveeM60Gun` <- `APCM60Gun`. A
  rebuild that writes `AmmoName` into all 62 adds nine settings the build does not have.
- **Every hop moves, and the fallback hop is load-bearing.** 42 weapons reach a projectile class (41 set on the
  ammo, **1 inherited**: `JaffaKreeStaffWeaponAmmo` <- `StaffWeaponAmmo`) and 60 reach a damage type: **39 from
  the projectile's `MyDamageType`, 21 from the ammo's** -- because three grenade and mortar projectiles
  (`FragGrenade`, `ShockGrenade`, `SemektetMortarGun`) name no damage type and their ammo's `Exploded` is the
  value. Without the fallback those three read as damage-less.
- **9 weapons reach no damage type and only 7 of them are ammo-less.** `GoauldKillerDroidWeapon` and
  `ReetouDevice` carry a melee ammo class that names none, so the damage is in the weapon's script -- the same
  conclusion `GRENADE` (22 functions, 8 states, no ammo class) forces for its shot.
- **The vocabulary is smaller than the naming implies.** 52 ammo classes over 62 weapons, 29 damage classes
  over 60, `APCM60GunAmmo` on 4 weapons and 12 damage classes shared; read as the one-to-one triple the naming
  implies, 68 weapon names would invent 16 ammo and 39 damage classes that do not exist.
- Tool + `inventory/weapon_effective.csv` (69 rows: `package,class,ammo,ammo_set_by,projectile,
  projectile_set_by,damage,damage_via,damage_set_by`) + `docs/systems/WEAPONS.md` section "What a weapon fires"
  (generated block; `--check-doc` in `make ci` also fails when the committed CSV disagrees with the graph) +
  `make weapon-effective` (`--from-csv`, so it needs no dump). The `--check` covers inheritance at each hop,
  the ammo fallback and the per-hop counts.

### Execution notes (2026-11-11, a weapon names a bullet, the bullet names the shot, and a class reference had been naming its metaclass)

- **The effects-wiring item is a read, and the graph is 315 class references over 176 of the 231 classes.**
  `tools/weapon-links.py` reads every `class'...'` value in the four trees' default property blocks
  (`tools/defaults-emit.py --emit`) and classifies the **target by its own super chain**, never by the property's
  name: `ammo` 69, `damage` 74, `emitter` 47, `projectile` 40, `other` 85. All **219 distinct** references
  resolve in `inventory/classes.csv`, so none of this is a name the dump does not carry. The 231 counts the four
  roots themselves; the page's 227 counts their descendants.
- **The chain is joined at the ammunition, and that is the structural finding.** `AmmoName` is declared on
  `Engine.Weapon`, but `ProjectileClass` and `MyDamageType` are declared on `Engine.Ammunition`: **53 weapons
  name an ammo class, 35 of those ammo classes name a projectile, 27 of those projectiles name a damage type.**
  A weapon that names no ammo class spawns its shot in script -- `ProjectileClass` is not a `Weapon` property on
  this build at all, which is why the page's naming test and this graph disagree in places.
- **`StargateDamageType` is not pure data.** 30 of the 37 set `LowDetailEffect`, and all 30 name one class, an
  `Effects` sprite (`Blood < UT_SpriteSmokePuff < AnimSpriteEffect < Effects`) -- one shared reference into
  `Stargateeffects`, against 35 distinct emitter classes over the 47 emitter edges.
- **The emitter had been naming the metaclass's package, and this measurement caught it.** A foreign class
  reference rendered as `class'Core.SE_...'` because `render_ref` used the import's `class_package`, which for a
  **class** holds the package of its *metaclass* (`Class` lives in `Core`); the class's own package is the
  import's Outer. Over the whole dump **183 of 885** class references named a package the class is not in, and
  **2** do after the fix -- both the same case difference (`Gameplay` in the name table, `GamePlay` in
  `inventory/classes.csv`). `outer_package()` takes the Outer only when that object really is a `Package`, and
  falls back to the old field for a nested one, which is right for it.
- Tool + `inventory/weapon_links.csv` (315 rows: `package,class,property,declared,declared_type,target,ref,
  ref_package`) + `docs/systems/WEAPONS.md` §"What a weapon names" (generated block, `--check-doc` in `make ci`)
  + `docs/CLASS_DEFAULTS.md` §"A class reference names the package it lives in" + `make weapon-links`. The
  `--check` covers the super-chain classification, the declaring-class walk, an unresolvable name and the
  per-hop chain count.

### Execution notes (2026-11-11, 522 located starts are not property tags, and they are the whole single-declared and incomplete residue)

- **The locator's own start, read as a tag.** The declared-name table that the anchor rule needs also says what the
  `block_start` of the 2,209 located payloads opens with: **1,687 carry a property the class declares, 522 carry
  something else** -- a package (`System` 121, `Core` 89, `Engine` 41), a state (`Lighting` 38), a struct (`Range` 29),
  a class (`Gameplay` 15), a function (`GetMeshName` 5). A tagged property's name is a property, so those starts are
  the payload's own footer read as a block, not a block.
- **The sample is a block whose every byte is in question.** `Engine.Material`'s 118-byte payload "locates" a block in
  its **last nine bytes** (`System` + `DefaultMaterial`), and offsets 107, 108, 110 and 111 parse to the same end with
  `Core`, `Engine`, `Object` and `?` instead. Four adjacent starts, four different stories, one payload end.
- **The split against the reasons is exact, and it is the finding.** All **263** `single-declared` rows and all **252**
  `incomplete` rows are among the 522 (the remainder: 4 of the 30 `type-contradiction` and 3 fully-declared classes). So
  those two reasons are not "a block the renderer could not match": their input is record bytes, and the `--verify` gaps
  the page records for them are the renderer being right about a block that is not there. That is the strongest reading
  of the 545 withheld classes so far, and it does not need a new parse to check -- the column says it per row.
- Tool + `inventory/class_records.csv` (`block_first_name`, `block_first_declared`) + `docs/CLASS_DEFAULTS.md` S"The
  located start opens with a property tag" + `--report` printing the split, the six names and the per-reason breakdown +
  `--check` covering a declared name, a non-property name and an offset past the payload.

### Execution notes (2026-11-11, a tags-only start rule anchors 1,628 of 2,209 blocks and finds no two-tag block in the residue)

- **The start rule the residue asked for is measured, and it is a bounded rule.** `tools/class-record.py` now anchors a
  defaults block on its **tags alone**: a start is an offset past the record prefix where a tag's name index resolves to
  a property the class or an ancestor declares, and a clean parse runs from there with no invented name. No test reads
  the payload's last byte, which is what the 59 asked for. Every suffix of a real block parses too, so the candidate
  with the most properties is its front. Scored against the population that has an answer: **1,963 of 2,319** payloads
  anchor at all and **1,628 of the 2,209** located starts are reproduced (73.7%).
- **The 73.7% is agreement, not correctness, and the miss has a direction.** The four sampled unanchored located
  starts (`Core.Time` 72, `Engine.Material` 109, `Engine.SavedMove` 349, `AmbientCreatures.AmbientCreature` 83) decode
  their first two tags to `Core`, `System`, `<` and `Lighting` — package and operator names, not properties — so the
  locator's "located" is a shape there and the tag rule refuses it. Read as a bound on the locator's semantic claim,
  not as a failed parse.
- **The residue holds no block of two or more tags, and that is the close the doc's question deserved.** `long-no-block`
  (60 rows, 59 of them `empty-block`): **27 name no declared property anywhere in the payload**, **33 hold exactly one
  tagged property** (14 of them end on the payload's last byte, 19 are followed by a 66–478-byte trailer), **0 hold
  two or more**. `record-only` (50): 45 / 5 / 0, the five single tags at trailer 13–70. So a rule that ignores the
  payload end finds nothing the locator missed: the tail is a single tag or no tag, and the 14 that end on the last
  byte are the shape the `single-declared` reason already carries (one tag cannot be told from a table tail).
- **What is left is the payload's shape, not its tags.** The 27 + 45 payloads whose tail names no property of their
  class are record body by elimination — a payload-length question the length window already bounds — and the 19 with
  a 66–478-byte trailer are a tag followed by bytes no tag stream explains. Neither is a start-recovery problem any
  more, which is a stronger statement than the residue had.
- Tool + `inventory/class_records.csv` (`anchor_starts`/`anchor_start`/`anchor_props`/`anchor_trailer` on all 2,319
  rows) + `docs/CLASS_DEFAULTS.md` §"The start rule is measured" + `--report` printing the four counts + `--check`
  covering the rule on a hand-built two-tag payload and a class that declares nothing.

### Execution notes (2026-11-11, the trailing byte of a class payload cannot locate its end)

- **The one-byte terminator for the 109 `empty-block` records is measured and rejected, and the control is what rejects
  it.** The obvious close — read the payload's last byte as the terminator of a zero-tag stream, so the record is
  `payload-1` bytes on all 109 (80–1,395, median 102; the 50 `record-only` 80–99, the 59 `long-no-block` 100–1,395)
  — holds for every payload in the population and so locates nothing: **2,319 of 2,319** class payloads end in `00`,
  and a one-byte empty tag stream (a name index of 0) parses at `payload-1` on **2,319 of 2,319**, including the
  **2,209** whose record end a located block already fixes at a much earlier byte. So the 109 record lengths stay
  bounded by `RECORD_WINDOW` and the length distribution, and the residue is unchanged — which is worth more than the
  number it refuses, because the number was about to be written down as measured.
- **#360's "the class's function table sits inside the record" is a correlation and not a layout claim.** r **0.80**
  with `Function` exports and **0.67** with `Property` exports over 2,189 measured blocks say the record grows with
  the class; nothing decodes its body, and the `empty-block` rows cannot serve as counterexamples because their length
  is exactly what is missing.
- `inventory/class_records.csv` gains `last_byte` and `empty_at_end` (2,319 rows, beside `block_start`/`shape`, so the
  saturation is auditable on the rows whose true end is known), `--report` prints both counts plus the located
  denominator, and `docs/CLASS_DEFAULTS.md` §"Where a class payload stops being a record" carries the negative next to
  the residue. A local `--empties` mode writing a 109-row `class_record_empties.csv` was trialled and not committed:
  per class it recorded the same saturated predicate, which the two columns already show against a control.

### Execution notes (2026-11-11, a class record is anchored on two indices, and the 109 no-block rows split 50/59)

- **The `empty-block` reason is now a measurement for 50 of its 109 rows, and the other 59 are a named residue.**
  `tools/class-record.py` measures where a class payload stops being a record using two indices from the package's
  own export table, neither guessed: the payload's first compact index must equal the export row's `super` (**2,319 of
  2,319** agree), and the `TextBuffer` export outered to the class must be referenced right after it (**2,319 of
  2,319** — at `+2` for 1,451, `+3` for 868, i.e. past `Super` and the zero `Children` byte). The record's end is the
  block start the defaults locator already found, so the block is the measurement.
- **The record is not a header: its length tracks the class's own function table.** Over 2,189 located blocks the
  length runs 8 .. 13,665 bytes (median 96, p90 401, mode 92 with 357 blocks, 431 distinct lengths), and the Pearson
  correlation with the number of `Function` exports the class owns is **0.80**: classes that own none have a 92-byte
  median record, 1–5 functions 123, 6–20 288, 21–60 670, more than sixty 3,410. Twenty located candidates start at
  byte 0 or 1 — inside `Super`/`Children` — and are counted apart, because their "length" is not one.
- **Joining the shapes against `class_defaults_misses.csv` splits the 109 `empty-block` rows 50 / 59 and moves
  nothing else** (252 `incomplete`, 263 `single-declared`, 30 `type-contradiction` and 1 `single-property` keep their
  reasons). The **50 `record-only`** payloads are 81–100 bytes — inside the record-length distribution and inside the
  range where 1,254 located classes *do* carry a block after their record — so "this class sets no defaults" is a
  reading of the bytes, not a parse failure. The **59 `long-no-block`** payloads are 101–1,396 bytes (median 125): a
  record accounts for the front and the rest is still a locator miss, which is the residual this note opens.
- Tool + `inventory/class_records.csv` (2,319 rows, new `functions` column) + `docs/CLASS_DEFAULTS.md` §"Where a class
  payload stops being a record" + `make class-records`. `--report` now counts *blocks* rather than distinct lengths
  (its first version answered 72 where the artifact holds 1,549, caught by reading the number against the rows) and
  joins the misses artifact itself, so every figure the page quotes comes out of the two files.

### Execution notes (2026-11-11, the defaults floor of two is measured, and a one-tag payload gets a reason)

- **A floor of one is not a recovery, and the number that settles it is 1,477.** `tools/class-defaults.py` keeps
  blocks of at least two properties; dropping that to one promotes **320** classes to "fully declared" (1,664 →
  **1,984** of 2,319), and every one of those verdicts rests on a single tag. But **1,477 of the 1,664** classes whose
  block is *already* located carry the same shape — a one-tag parse of a declared property that ends on the payload's
  last byte — and **1,778 of 2,319** payloads do, because the last tag of a real block, read from its own start, is
  exactly that. The promotions are also wrong where the payload is known: `ActionString` is the tag for **53** of the
  320, `VoicePack` would be "fully declared" on `LifeSpan` out of a 108-byte payload, and for **19** classes the
  promoted tag *replaces* a candidate that names two declared names of three (the score's declared fraction, 1/1
  against 2/3, outranks the count). The floor therefore stays at two; `--floor 1` was added so the measurement
  reproduces (`--packages DUMP/System --floor 1 -o $WORK/floor1` prints 1,984).
- **The `*VoicePack` rows split 10 `single-declared` / 9 `incomplete` / 1 `empty-block`, and `--misses` now says
  which is which.** Before calling a class `incomplete`, the tool asks whether the payload ends in a one-tag parse
  naming a declared property with no parse holding two declared names; if so the reason is `single-declared` and a new
  `property` column names the tag (263 rows: **nine name `VoiceSets`**, the array `tools/voice-sets.py` reads
  independently, and `ActionString` alone accounts for 53, which is a menu-table tail shared across classes). The ten
  other pack rows keep their reasons correctly — nine are 108-byte payloads that set no array, and
  `JaffaCaptainVoicePack` parses an empty block. The 24 `empty-block` and 24 `type-contradiction` rows that also end
  in a declared one-tag parse are deliberately **not** relabelled: an empty block at offset 0 is a block-shaped
  structure the single tag does not explain, and a contradicting block is a different finding.
- Tool + `inventory/class_defaults_misses.csv` (655 rows, new `property` column) + `docs/CLASS_DEFAULTS.md` +
  `docs/systems/AUDIO.md`; `--check-misses` now verifies that every `single-declared` row names a property its class
  declares and holds exactly one, so the reason cannot drift into a claim nothing checks.

### Execution notes (2026-11-11, a per-sound `VoiceDesc` is match keys, and the array exists for the takes)

- **A `VoiceSet` block begins at byte 0; only a *class* payload hides its block.** A `*VoicePack` export is a
  class record followed by its one tagged array, so `tools/voice-sets.py` has to hunt for the start. A
  `VoiceSet` is an ordinary object: `VDesc` (Struct `VoiceDesc`, 66 bytes), `VSounds`, `VSoundsDesc`, and the
  block ends exactly on the payload's last byte. Reading it from 0 is not tidiness -- `voice-sets.py`'s
  `block_of` prefers the candidate with the *most* properties, and on these payloads a start a few bytes late
  re-reads `VDesc`'s own tagged fields as seven top-level properties (`Type`, `Type`, `Subject`, `Subject`, ...)
  instead of three. Its totals were right because the tag it wanted survived the mis-parse; a column taken from
  that parse would not have been.
- **An array of structs is `count` + one tagged, `None`-terminated block per element.** `VSounds`' elements are
  bare compact references, `VSoundsDesc`' are not, so the only honest check is that the last element lands
  exactly on the property's size; an element type that happens to stop *inside* it is what a guess looks like,
  and the tool refuses the whole set rather than reporting one element short.
- **1125 descriptors over 273 sets, and every one of them is the same 11 fields.** The array is carried only by
  the SG-1 four -- the extended pack's subclass -- while the Jaffa, Haaken and Reetou sets are `VDesc` +
  `VSounds` + load-priority flags and hold no per-sound descriptor at all. The per-sound match is an SG-1-only
  feature twice over: only `SG1VoicePack` runs the lottery, and only its sets carry the data to run it on.
- **Tone is what the per-sound array exists for.** 189 of the 273 sets name more than one tone among their
  descriptors, and the histogram over all descriptors is `TONE_None` 4, `Normal` 341, `Combat` 553,
  `Stealth` 227 -- `TONE_Critical` and `TONE_Other` are declared and never used. A set is one line of dialogue
  and its `VSounds` are the alternate takes, so `VSoundsDesc[i].Tone` is what puts take *i* in the tone-match or
  tone-generic bucket; `(normal, combat, stealth)` is the common shape (90 sets), with 8 sets holding two takes
  per tone and 7 holding three.
- **A descriptor names its type by string and carries no probability.** `Type` and `GenericType` are 0 in all
  1125 descriptors and `SGenericType` is set in 195: `VoiceSet.Build` runs `GetMsgTypeFromString` when the pack
  builds, and *that* is when the ints `IsSubTypeOf` compares appear -- the file holds the name, not the value.
  The rest of the match keys: `Recipient` 236, `Subject` 153, `bForbidGeneric` 101, `eCustomize` 140 (every one
  of them the recipient-name *prefix* mode, never postfix), `Text` 1121. Every probability lives on
  `VoiceSelector`'s own defaults, so "which probability a descriptor carries" has the answer *none*.
- **96 of the 273 sets have no `VDesc.Id` of their own**, and 379 descriptors name an Id different from their
  set's. The fall-through chain (`GetVoiceID`/`GetVoiceTone`/`GetVoiceRecipient`/... each fall back to the set's
  `VDesc`) is what makes that harmless for everything but `Id`: `SearchVoices` compares the set-level `VDesc.Id`
  when the descriptor does not match, so a mixed-Id set matches on its own Id and its other takes fall through to
  the generic bucket -- which is empty here, because `GenericType` is only ever a string in the file.
- Tool + artifact: `tools/voice-descs.py` (`make voice-descs`), `inventory/voice_descs.csv`, and the
  `AUDIO.md` block it generates (`--check-doc` in `make ci`). `AUDIO.md`'s "not measured" list no longer names
  the descriptor; what remains open is which take the game picks at runtime, which needs a mission.

### Execution notes (2026-11-11, a pack holds one `VoiceSets` array and its sounds are imports)

- **The pack's whole content is one default property, and the emitter's locator cannot see it.** A `*VoicePack`
  class payload is a class record plus a single tagged `VoiceSets` array; `tools/class-defaults.py` requires two
  properties in a candidate block, so the 20 `*VoicePack` rows in `inventory/class_defaults_misses.csv` are
  exactly this shape and stay `incomplete` there. `tools/voice-sets.py` (`make voice-sets`) reads the array
  straight from the package into `inventory/voice_sets.csv`: **9 of the 25 pack classes carry a `VoiceSets`
  array — 337 slots, 336 of them resolving to a `VoiceSet`, 1227 `Sound` references** — and the other 16 set no
  array at all (10 of those payloads are exactly 108 bytes, the class record alone), so they are silent packs.
- **A sound reference is negative, i.e. an import.** The sets live in `StargateContent.u`, but every one of the
  1227 sound references resolves to a `Sound` import from the character's own package; counting only positive
  references made a correctly parsed array read as zero sounds, which is the failure mode to remember here.
- **A `VoiceSet` block is `VDesc` (struct) + `VSounds` + `VSoundsDesc`, and reading it by scanning for tag names
  is wrong twice over.** A struct's name index precedes its size class (so `VDesc` is 66 bytes, not the 19 a
  first walk measured), and `VDesc`'s bytes can spell a `VSounds` + Array coincidence. `tools/prop-stream.py`
  already reads this layout — it is the reader the save/`defaults` work derived from the game's own code — so
  the tool locates the block that terminates at the payload's last byte and takes its properties from there; a
  hand-rolled `parse_tag`/`scan` pair was deleted rather than repaired.
- **Not every set carries sounds, and the three shapes are counted.** Of the 336 sets, 274 carry the full triple
  and in 274 the descriptor count equals the sound count, 25 carry `VSounds` plus load-priority flags
  (`VLoadPriorityLow`, `VOnlyPlayIfLoaded`, `VPreCaching`) and no descriptor array, and 37 are `VDesc`-only
  templates. The per-sound `VoiceDesc`'s *fields* remain unread; that is the page's remaining open question.
- **The numbers are one lower than the probe that opened the task** (Carter 352 not 353, Jackson 241, ONeill 279,
  Tealc 252): the earlier probe counted an empty `0` element as a sound, and the artifact counts references that
  resolve. Six empty elements exist across the nine packs (one each in the SG-1 four, four each in `JaffaL`/`JaffaM`).

### Execution notes (2026-11-11, the voice pack is a per-pawn default and the selector is a lottery)

- **`AUDIO.md`'s open question was two questions, and only one of them had a mission in it.** "Which voice pack a
  given mission uses" presumes a mission chooses a pack; nothing does. `StargatePawn` declares
  `SGVoicePackClass` (`class<StargateVoicePack>`) and spawns that class on itself at begin-play, so the pack is a
  **default property value**, inherited down the class tree — `tools/voice-packs.py` reads it out of the emitted
  defaults into `inventory/voice_packs.csv` (`make voice-packs`), the same reader `#355` used for seats.
- **61 classes under `StargatePawn`; the pack is `own` in 17, `inherited` in 5, `unread` in 1 and `none` in 38,
  over 13 packs.** The Jaffa tiering is the inheritance case: `JaffaHorusM`, `JaffaJackalM` and
  `JaffaSerpentM` take `JaffaMVoicePack` from `JaffaBase`, which is why three heights share three packs rather
  than nine. `unread` is a class whose `defaultproperties` the emitter cannot locate (`Tokra_Armed`); it is
  reported as unread, not as silent.
- **Most characters do not speak.** 38 classes set no pack and inherit none, so no pack is spawned and every
  `Say` is a no-op: the SGC rank and file, `GeneralHammond`, the single-pool Goa'uld and Replicator families,
  `JaffaGrunt`, `SuperSoldier`, `Ptero`, `Jonas`, `Jacob_Selmak`. The voiced population is the SG-1 four, the
  Jaffa officer tier, the Haaken trio, the Tok'ra and the Reetou.
- **The placement route is empty, measured rather than assumed.** Zero pawns are placed across the 73 maps
  (`inventory/map_actors.csv`, 6,658 rows over 389 actor classes); 814 spawner instances
  (`IntelligentSpawner` 808, `ScriptedSpawner` 4, `FishSpawner` 2) fill a mission, and each spawner's squad is a
  per-instance property of the map — so a mission's pack set is not readable from its actor table, and that is
  now what the page says instead of the old "not measured".
- **Which selection code runs depends on the pack's ancestry, not on the caller.** `StargateVoicePack.SelectVoice`
  is an id-only fast search (first `VoiceSet` whose `VDesc.ID` matches, then `Rand` inside it);
  `SGVoicePackExtended` replaces it with `VoiceSelector`'s six-bucket lottery. `SG1VoicePack` is the only subclass
  of the extended pack and the SG-1 four are its only subclasses, so Carter, Jackson, O'Neill and Teal'c get
  tone/recipient matching while the nine enemy and NPC packs do not.
- **The lottery's tail is unreachable while anything better matches.** `VoiceSelector`'s defaults are
  `FullMatchProba=1.0`, `ToneMatchProba=FullGenericProba=ToneGenericProba=0.5`, and `DefaultMatchProba` /
  `DefaultGenericProba` are unset — 0.0. The pool takes a bucket's voice unconditionally when the pool is empty
  and with probability `p` otherwise, so a default-parameter voice can only be heard when no full match, tone
  match or generic match exists. `MaxRecentlyUsedVoices=3` is the repetition guard, and it stands down while only
  one suitable voice remains.
- **One limit, and it is the reader's not the code's.** No emitted block carries a `VoiceSets` value and
  `inventory/class_defaults_misses.csv` lists 20 `*VoicePack` rows the locator cannot complete (19 `incomplete`,
  1 `empty-block`, 2–3 properties of 299–308 declared), so voices-per-pack and the per-sound `VoiceDesc` stay
  unread; the page's *Not measured* list now says that instead of naming the selection logic.

### Execution notes (2026-11-11, the vehicle seat wiring is a default property, and the seats are shared)

- **The page's negative was the wrong question.** `docs/systems/VEHICLES.md` closed with "which seat index maps
  to which turret is configuration, not visible in the class graph". The configuration is a **default property
  block**: `StargateVehicle` declares `DriverWeapons` (`array<DriverWeaponStruct>`) and `PassengerWeapons`
  (`array<PassengerWeaponStruct>`), and `tools/defaults-emit.py`'s array path already tiles one tagged struct per
  element, so the wiring is a read. `tools/vehicle-seats.py` turns the emitted blocks into
  `inventory/vehicle_seats.csv` -- **25 slot rows over 11 classes**: turret 5, seat 10, weapon 6, empty 4 -- and
  the page's table is generated from it (`--check-doc` in `make ci`).
- **The field name differs between the two structs.** A `DriverWeaponStruct` element carries `WeaponClass` and a
  `PassengerWeaponStruct` element carries `WeaponPawnClass`; a reader that matches either one name alone returns
  half the table, which is the failure mode the synthetic `--check` drives on purpose.
- **A seat pawn does not name its vehicle, and a turret pawn need not have a slot.** `Semektet`'s two rear slots
  hold `HummveeInteriorSeat`, and five distinct seat pawns fill the ten seats, so `role` is taken from the super
  chain (`VehicleTurret` -> turret, `StargateVehicleSeat` -> seat) rather than from the name. The slots name
  5 of the 11 turret pawn classes; the six left over -- `HummveeTurretRear`/`…Left`/`…Right` and
  `SemektetStaffTurret`/`…Left`/`…Right` -- are declared in the tree and never wired here, so a turret can exist
  without a slot and nothing in this read says how it is attached. The bone a slot names is a property value and
  stays out of the artifact; the column records only `named` (18) or `none` (7).
- **Unset is empty, not unknown.** `AlkeshBomber`, `DeathGlider`, `Jeep` and `Teltak` emit no slot row; the
  property is declared once on the base, so a class that sets no value inherits no seats. Two of those four are
  the flyers that *are* placed in maps (`DeathGlider` 12, `AlkeshBomber` 6), so their gunnery is not in this
  configuration -- unmeasured, and named on the page.
- **A per-root artifact cannot supply a fixed list.** The first version derived the "no slots" list from
  `inventory/class_graph.csv`, which `tools/class-graph.py` regenerates per root and which now holds the
  objectives graph: it answered 32 `ACTION_*`/objective classes as vehicles. The list comes from
  `inventory/classes.csv`'s own super chains, which is what `role` already used, and `--check` asserts the
  fork-base and seat exclusions against a synthetic tree.

### Execution notes (2026-11-11, the last 21 maps load, and all 61 campaign entries have a row)

- **The sweep finished the range.** `tools/xemu/missions.sh` ran indices 50-70 into `$WORK/mapback3`, one boot
  each, and **all 21 passed on their first attempt**: `M16F`, `M16G`, `M17A`, `M17B`, `M17D`, `M18A`, `M18B`,
  `M18D`, `M19A`-`M19E`, `M20A`-`M20D`, `M21A`-`M21D`. Merged with the eight run directories the two earlier
  sessions left, `inventory/map_loads.csv` now holds **61 of the 61 campaign entries over 74 boots, 61 PASS
  and 0 FAIL** -- every campaign map on this disc loads, and the campaign load table has no gap left.
- **The pace prediction held to the minute.** The first five of the back half took 23 minutes (4.6 per boot);
  these 21 took 97 minutes for the same 4.6, so a sweep of the whole range is one afternoon of boots and the
  earlier estimate was the measurement's own rate rather than an extrapolation.
- **The merge set is nine directories, and the reproduction is the check.** The base eight reproduce the
  committed 40 rows byte for byte, and adding `mapback3` is then the only diff -- which is how the set was
  confirmed, because a directory listing does not name it. Two of the nine were missing from this page's own
  run table: `$WORK/xemu/missions-retry` (the boots behind index 12's 3 attempts and index 14's late PASS) and
  `$WORK/mapback2`. Both are listed now. `map-loads.py` truncates nothing, but it does decide the merge from
  the directories it is handed, so the table of them is part of the artifact.
- **A `PASS` is a load, not a mission, and this table is still not a playability claim.** 61 of 61 loading is
  the opposite of the thing the page warns about in prose: several entries are cutscenes (70) and 41 is a
  first-person scene, so a HUD criterion would fail on the disc's own content. What a row cannot say -- whether
  the map plays, and which of them a player can reach without the list -- is unchanged and still named on the
  page.
- **The names do not predict the index.** The range is dense (10-70) and the names are not: 15 is `M04B` while
  16 is lowercase `M04a`, and 52-54 are `M17A`, `M17B`, `M17D` while 55-57 are `M18A`, `M18B`, `M18D` -- so
  `M17C` and `M18C` sit on no entry. `map-loads.py` names each row from `map-index.py`'s ASCII order for that
  reason; a name read off a highlight frame is the error `docs/XEMU.md` records twice.
- **Two defects this run fixed rather than reproduced.** The page's "What is left" paragraph had been spliced
  mid-sentence by the previous run's edit -- the pace paragraph landed between "walks the range without" and
  "repeating:" -- and the generator's summary line read "61 of the 61 campaign entries ... and 0 do not yet"
  once the range closed. The line now says all 61 have been loaded, and `--check` exercises that branch.

### Execution notes (2026-11-11, the load sweep resumes at index 44 and 40 of 61 entries have a row)

- **Five more rows, and every one loaded on its first attempt.** `tools/xemu/missions.sh` ran indices
  44, 46, 47, 48, 49 into `$WORK/mapback2` and all five passed: `44 M15F`, `46 M16B`, `47 M16C`,
  `48 M16D`, `49 M16E`. Merged with the seven run directories #352 used, `inventory/map_loads.csv` now
  holds **40 of the 61 campaign entries over 53 boots, 40 PASS and 0 FAIL**.
- **The merge set is part of the measurement.** Re-running `map-loads.py` over *every* `missions.tsv`
  under `$WORK` does not reproduce the committed table: `missions/` and `missions-second-half/` re-report
  indices a later run already measured (10, 11 and 41 came back `2 attempts / 2 runs` against the
  committed `1/1`), and `missions-neg/`, `halt-test*/` and `sink*/` exist to prove the retry loop, the
  halt check and the sink read rather than to measure a map. Reproduce the committed rows byte for byte
  first, then add the new directory -- that diff is the check that the set is right.
- **The pace is measured, so what is left is bookkeeping rather than risk**: five boots took 23 minutes
  of wall clock (4.6 minutes each -- 135 s to the title, one `A`, N d-pad presses, a 60 s settle, one
  attempt), which puts the 21 unmeasured indices at about 100 minutes of harness time.
- **The sweep was stopped at index 50 instead of ending the run without a PR.** These five rows are the
  measurement the boot budget bought; `missions.sh` truncates its TSV per invocation, so
  `$WORK/mapback2` is left in place and the next sweep adds its own directory to the merge.

### Execution notes (2026-11-10, the campaign load table is an artifact, and 35 of 61 campaign maps have a row)

- **The verdicts are cumulative now.** `tools/xemu/map-loads.py` merges `missions.sh` run directories into
  `inventory/map_loads.csv` (`index,name,result,attempts,runs,detail`) and renders `docs/MAP_LOADS.md`
  between its own markers (`--write-doc`; `--check-doc` in `make ci`, reading the committed rows so it
  needs no disc). `missions.tsv` is truncated per invocation, so before this a sweep could not be extended
  and the campaign table lived in prose assembled by hand from several runs.
- **The merge rules are the measurement.** `PASS` wins over `FAIL` because a recorded failure is input and
  measured non-deterministic (4 of 31 indices in the 2026-11-04 sweep hit one; 21 and 38 failed both
  attempts there and passed first time when re-run alone); `attempts` sums the boots and `runs` counts the
  invocations; a row that never passed keeps the failure's own reason; and an index outside the disc's
  table or a status other than PASS/FAIL is refused rather than recorded.
- **Three run directories are deliberately not merged**, and the page names them: `missions-neg`
  (`--boot=25`), `halt-test` and `halt-test2` (an ISO whose `M00.unr` was truncated to 1,000 bytes) are
  failures by construction -- they exist to prove the retry loop and `guest-halt.py`, so merging them would
  put `BIOS screen` on a row whose map loads.
- **Measured: 35 rows, 35 PASS, 46 boots -- 35 of the 61 campaign entries (10-70).** The 31 first-half rows
  come from the five sweep directories the 2026-11-04/05 sessions left behind; the second half was swept
  from 41 upward here (`M15C`, `M15D`, `M15E`, `M16A` -- indices 41, 42, 43, 45), one boot each, every one
  on its first attempt, which extends the two second-half entries (41, 70) that `docs/XEMU.md` had measured
  by hand. 26 entries still have no row and the page's generated line names them (`44, 46-70`).
- **What this does not say**: a `PASS` is a load (repaint + not the BIOS screen + not the boot frame), not a
  playable mission; several campaign entries are cutscenes, so a HUD criterion would fail on the disc's own
  content. `docs/MAP_LOADS.md` states that and the two questions it cannot answer.

### Execution notes (2026-11-10, the class object's constructor field is not carried)

- **The route U8 ended on is measured, and it is a negative.** Every registered class owns a `UClass`
  object, so the class object's constructor field looked like the literal-free way to name the seventeen
  tables the installer's-callers edge could only constrain. `tools/xemu/class-ctor-field.py` walks the
  object array in a booted **M00** (`drive.py --gdb=12441 --linger=1800`, 48,646 objects / 1,059 objects
  carrying the `Class` table `0x004d4870`), reads each class object's own `FName` at `+0x20`, and scans
  its whole body for any of the **1,011** constructor and destructor VAs in
  `inventory/class_constructors.csv`. **0 hits**: the read reproduces **0 of the 466** names it already has,
  so it is a statement about the field, not about the nineteen -- this build keeps a class's constructor in
  the registration code and does not store it in the `UClass` instance.
- **What that changes.** The seventeen stay constrained and `docs/NATIVE_CLASS_TREE.md` keeps its status
  quo; U8 now records the closure and names the two routes left (an engine-side `classes.csv` for
  `Core`/`IpDrv`/`Engine` natives, or a breakpoint on a registration call reading the `UClass` as it is
  filled). The tool stays as the instrument, with `--check` (synthetic, no boot) so it cannot rot silently.

### Execution notes (2026-11-10, the installer's callers name five of the 26 and constrain seventeen)

- **The third edge is the installer's callers, and it reproduces the whole map.** A table is installed
  by a function (`inventory/class_constructors.csv`) and every subclass's ctor and dtor calls its
  parent's first, so an installer's direct callers are the classes one level below it.
  `tools/class-table-parents.py` reads that with one `E8` pass plus `symbols/xbox_functions.csv`:
  **466 of 466** committed names `agrees`, 0 adjacent, 0 differs, over **1,978** caller sites
  (`inventory/class_table_parents.csv`, 492 rows).
- **It names five of U8's 26**: `0x004c7958` `Material` (35/37 callers), `0x004c7a10`
  `RenderedMaterial` (7/10), `0x004c7ac8` `BitmapMaterial` (3/5), `0x004c7c78` `CameraEffect` (10/11),
  `0x005040d0` `Engine` (1/3, from `ctor_GameEngine`). The three material tables come out as the exact
  chain `classes.csv` gives, and each name has a registration site whose literal is that class.
- **Two clash, and the clash is evidence**: `0x004cfe68` -> `WaterTexture` (committed `0x004cfba0`)
  and `0x004d0228` -> `IceTexture` (committed `0x004cfc88`); the live read already says `0x004cfba0`
  carries 613 objects of class `Texture`, so the tool corroborates that inversion instead of
  re-naming. Both rows keep `name-clash:<table>`.
- **Seventeen stay constrained and two silent.** Their callers are native classes with no
  `inventory/classes.csv` row (`Struct`, `IntProperty`, `TcpNetDriver`, `ActorChannel`,
  `VertexBuffer`, ...), so the ancestor vote has no super to read; the caller set is kept as the
  constraint. `0x004c9348` and `0x004cfd78` have no named caller at all.
- **Correction: U8's "fourth and fifth pushes, unread" was wrong.** `0x004d0e90` is `L"System"`
  (argument 9 of the same 13-argument call) and the per-site string VA is argument 7, the class
  literal -- `tools/registration-sites.py` already decodes both, which is where 466 names came from.
  A site has no unread literal; its per-site arguments are the name, the size and the constructor.
  The remaining route is the class object's `ClassConstructor` read at runtime, or an engine-side
  class roster. `docs/UNKNOWNS.md` U8, `docs/NATIVE_CLASS_TREE.md`.

### Execution notes (2026-11-10, the shared constructor argument: a nine-byte default ctor, and none of the 27 names a class)

- **The argument is a default constructor, and the decompile says so in nine bytes.** `0x00062ce0` is
  `MOV EAX,ECX; MOV [EAX],0x004dd2a8; RET`; `0x00034650` is a 13-byte null-check forwarder into it
  (`MOV ECX,[ESP+4]; TEST ECX,ECX; JZ +5; JMP rel32`). It takes no class argument and stores one fixed
  dword, so 25 of the 27 unnamed registration sites are described identically by it.
- **`tools/ctor-argument.py` scores every site, and its own gate is the class-table map.**
  `inventory/registration_ctor_arguments.csv` (493 rows) gives each site the constructor it pushes, the
  table that body stores, how many sites share it, and how many classes object memory pairs that table
  with. **459 of 493 sites** have the pushed constructor install the site's own class table -- that is
  what makes the selection rule trustworthy -- and the 26 that do not are exactly the shared/neighbour
  sites. The 27 unnamed ones: 25 -> `0x004dd2a8` (51 classes / 550 objects at M00), 2 -> `WaterTexture`'s
  ctor, 1 -> a ctor that installs `IceTexture`'s table. **No unnamed site pushes a per-class constructor.**
- **Two selection bugs worth remembering in this build.** Every XBE section carries an executable flag
  (`.rdata` reads `0x6`), so `section_for(va, executable=True)` admits the `.rdata` literal `0x004c7008`
  that every site pushes and resolves all 493 sites to it; a push is code evidence only when the body it
  reaches is a **known function start** (`symbols/xbox_functions.csv`). And `C7 /0` at mod=00 masks as
  `modrm & 0xF8 == 0` -- `& 0xC7` rejects `C7 06` (`mov [esi],imm32`), which is how most of these
  constructors install their table, and read 462 of 493 as "no store".

### Execution notes (2026-11-10, the same read in a loaded level: 48,631 objects, and the 26 stay nameless)

- **The read the last note asked for ran: M00, 48,646 objects in the array, 48,631 headers read (15 unreadable), 346
  pages, 189 class tables / 376 rows** (`~/sg1/work/m00live/read.log`, `inventory/live_class_tables_m00.csv`).
  `tools/class-table-reconcile.py --live` scores it: **agree 142, installer 13, shared 32, inverted 1,
  unknown-class 1** -- one more agreement and one more installer than the title's 139/12, no unrelated pair.
- **None of the 26 unnamed tables carries an object at M00 either**, so the level's 6,494 extra objects do not name a
  single one of them: absence is a property of **which packages the disc loads**, not of the walk. The read covers
  **189 of 492** static tables, and **277 of the 466 named** ones are absent too, so the 26 behave exactly like the
  named majority they sit in. The runtime route to U8's 26 is therefore exhausted at two points of the game; the
  remaining instrument is the decompile of `0x00062ce0` (the shared argument 24 of the 27 sites push).
- **`0x004cfba0` is `Texture` again, on 613 objects** (title: 454), so the inverted row survives a second and much
  larger world; `0x004cfe68` (the pair's other half) still carries nothing, as does `IceTexture`'s own table.
- **Ten tables appear only at M00**, every one of them named by the literal artifact and consistent with it: nine
  agree or are shared (`SubActionCameraEffect`, `InventorySpot`, `MaterialSwitch`, `SubActionTrigger`, `SubActionFade`,
  `Pickup` carried by four pickup classes, ...), three are `installer` (`Pawn`/`GoaAutoTurretMK3Pawn`,
  `AIController`/`GoaAutoTurretMK2Controller`, `Projector`/`DynamicProjector`, `GUIScrollTextBox`/`SGScrollTextBox`).
  No table seen at the title disappears at M00.

### Execution notes (2026-11-10, the table->class read taken: `0x004cfba0` is `Texture`, and the 26 stay nameless)

- **The command the last run left one call from an answer returned one.** `tools/xemu/class-tables-live.py`
  against a booted guest at the title (`drive.py --gdb=12641 --linger=1800`, 42,152 objects) walked **42,135 of
  42,152** object headers over 255 pages, paired them with **179 class tables / 312 rows**, and wrote
  `inventory/live_class_tables.csv`: **`0x004cfba0` is `Texture`** (454 objects, every one of class `Texture`),
  and `0x004cfe68` carries no object -- as does `IceTexture`'s own table, so that is the title's package set, not
  a hole in the walk. The `WaterTexture` row in `inventory/xbe_class_table_names.csv` is the neighbour's literal,
  as `docs/NATIVE_CLASS_TREE.md` predicted; the artifact keeps the row (it is that pass's output) and the live
  file is the tiebreaker.
- **`tools/class-table-reconcile.py` is what makes the read scorable** (`--check` is a synthetic world of six
  verdicts and needs no dump): 139 agree, 12 name an ancestor of the object's class, 26 tables are shared by more
  than one class, 1 inverted, 1 non-class (`new`/`System`).
- **The 26 unnamed tables are not settled by it**: none carries an object at the title, and 287 of the 466 named
  ones do not either. The next measurement is the same read in a loaded level (M00, which holds ~6,500 more
  objects), so this run stopped at the title.

### Execution notes (2026-11-10, the table->class read: instrument shipped, no guest)

- **The instrument exists now.** `tools/xemu/class-tables-live.py` answers U8's "one object read" without a
  literal, a window or a tree: it walks `GObjObjects` and pairs each object's `+0x00` table with its `+0x24`
  `UClass` name (`+0x20` through `GNames`), writing `table_va,class,objects` -- so a table is named by the objects
  that carry it, and `--table 0x004cfba0 --table 0x004cfe68` is the WaterTexture pair. `--selftest` covers the walk
  (a table shared by two classes, an object header at a page edge) with no boot.
- **No read: xemu never started QEMU.** Two launches with the prepared image left `AppRun` alive with no QMP
  socket, no `serial.log` and no `-gdb` listener while `xemu.log` stopped at `xemu_settings_set_path`, and `:0`
  answered `xdpyinfo` with the xemu window findable -- so the next run knows the emulator, not the address, is the
  blocker, and the WaterTexture pair stays open with the command above one call from an answer.

### Execution notes (2026-11-10, the 15 class-tree conflicts are four causes, and one of them is a name)

- **The tool now says why, and the answer is mostly "no script row".** `tools/class-tree-edges.py
  --conflicts` decomposes the 15 conflicts that the 375/391 agreement leaves: **11 `no-script-row`**
  (the table's class is absent from `inventory/classes.csv` — 95 of the 466 names are such native
  classes, 84 of them without a measured child), **2 `descendant-edge`** (`Vehicle` 7 extras,
  `PlayerController` 1), **1 `foreign-child`** (`WaterTexture` 5), **1 `native-child`**
  (`Commandlet` 6). `--check` carries all three of the new causes as a synthetic world.
- **The descendant edge is a skipped level, not an inlined parent.** `call_before_store` is the
  first `E8` before the store, and the bodies show it: `StargateVehicle`'s ctor `0x00023080` calls
  `Vehicle`'s `0x00021f10` at `+0x003` with `SVehicle`'s ctor `0x00022020` out of line and calling
  the same, and `StargatePlayer`'s `0x00026a70` calls `PlayerController`'s `0x00026870` with
  `UnrealPlayer`'s `0x00026990` out of line and doing the same. The generated stub skips its own
  parent; the instrument loses exactly one level and still pins a unique table.
- **`WaterTexture` is the one contradiction.** `0x004cfba0`'s five callers all `extend Texture` (or
  `FractalTexture`), while `WaterTexture`'s three script subclasses call `0x002e8260`, which stores
  the unnamed `0x004cfe68` and calls `0x0027e3b0` (the ctor that installs `0x004cfba0`) before its
  store. Read as `0x004cfba0 = Texture` and `0x004cfe68 = WaterTexture`, with `FractalTexture` having
  no out-of-line ctor. The registration literal path puts `WaterTexture` on `0x004cfba0` from a site
  0x57 bytes from `Texture`'s, so the mixed-window caveat reaches this row; what would settle it is
  one `UClass` name/table read at runtime (U8). `docs/NATIVE_CLASS_TREE.md`,
  `docs/UNKNOWNS.md` U8.

### Execution notes (2026-11-10, U7's second dump: a mission load registers nothing, and 356 is the whole table)

- **The table after a level loads is the table before it.** `tools/xemu/gnatives-mission.sh 10` boots to the title,
  presses `Continue`, ten d-pad presses and `A` (index 10 = `M00`), proves with `frame-diff.py` that the frame left
  the menu (92,571 px, 7.53%), then reads the 16 KB array over the stub. `tools/gnatives-mission.py` joins that read
  with the menu-time map: **356 live slots in both, 356 of 356 bodies in the same slot, 0 gained, 0 lost, 0
  replaced** — `inventory/gnatives_mission.csv`, 356 rows, every one `same`. So natives are all registered before
  the front end's menu, a mission adds nothing, and the line that stood in U7 and in `docs/NATIVE_VM.md` ("a full
  map needs a dump after a mission loads") was wrong for this build rather than blocked.
- **A `.data` address is not a physical address, and here the difference was one page.** The first read used
  `mem.py --dump 0x6313e8:0x6353e8`, which in the stub's default *physical* mode dumped 6.5 MB (the flag takes
  `ADDR:SIZE`) with the array at physical `0x7dd3e8`: 353 of the menu's 356 bodies matched and the last 250 slots
  read **zeros**, because `0x635000` is a page boundary 3,846 slots into the array and that page's frame is
  elsewhere. Those zeros read exactly like "the load zeroed the tail". `tools/xemu/mem.py --virtual` reads the
  context's linear space — the space the address belongs to — and returns 356 of 356.
- **The gate threw away one boot, which is why the number is worth trusting.** A first attempt's presses were
  swallowed (highlight frame 331 px against a 710 threshold, then 0 px on the mission shot) and the frame gate
  stopped the read before the stub could serve the menu's table again; the retry at a 170 s boot window
  (`SG1_BOOT`) loaded `M00` first time. Two boots, one read, one committed row set.

### Execution notes (2026-11-10, the 56 without a stub: three channels, and two packages the build never loads)

- **The remainder names itself per declaration, and no declaration is missing.** `tools/native-channels.py` joins
  the `-1` stub, the `{name, body}` record and the class's store-registered slots for all **523** index-less
  declarations: **467** stub+record, **41** record-only (all `Object`), **15** with neither. So the count the last
  run left open was not 56 unexplained registrations but 41 that register by record and 15 whose packages the image
  does not carry.
- **`Object` is the store-registered class, and that is the whole answer for the 41.** All **230** of its live slots
  are filled by `mov [slot], reg` stores, **0** of its 160 indexed natives has an indexed stub, and exactly **one**
  of its 42 index-less declarations (`GetReferencers`) has a `-1` stub. A class that registers by store shipping no
  stub for its index-less half is one fact, not two; the record is what binds those 41, through the `int%sexec%s`
  lookup at `0x0003a030` (U1).
- **The 15 are editor-side packages the build never loads.** `BrushBuilder` (13), `MaterialFactory` (1) and
  `UPreviewSkinnedButton` (1) occur **0** times in the image -- class name, function name, binder literal -- and
  `Editor` (0 of 14) and `UPreview` (0 of 2) are the only two packages with **no** declarations bound by name,
  against 508 of 508 named rows image-wide. Both sit in `System/Default.ini`'s `EditPackages` and in no runtime
  package list. Nothing to register, so nothing registers.
- **A binder column that invented a literal.** `tools/native-reconcile.py`'s `load_binder` filled an empty binder
  with `"int%s%s" % (prefix_expected, function)`, and the residual table then read its own fabrication as evidence
  -- which is why `inventory/native_residual.csv` called `UPreview`'s two records `text-missing` (`intUCreated` and
  `intUSetRenderInfo` occur 0 times in the image). It keeps an empty binder now and both rows read
  `record-unbound`, which is the rule its own self-test already stated. Two rows change; that target's other two
  artifacts are unchanged.
- **Artifact, target, check.** `inventory/native_channels.csv` (523 rows: package, class, function, binder,
  binder_va, stub, record_va, record_body, channel, class_store_slots, class_stub_slots, in_image), `make
  native-channels`, and a synthetic `--check` that `make ci` drives through `tool-check-contract` (the four
  channels, the empty-binder rule, the class slot split, the pool word rule, the join). Image plus five committed
  CSVs; no boot, no Ghidra.

### Execution notes (2026-11-10, the other 467 stubs: the name side is code too)

- **The registrar's `-1` branch has 467 callers.** Every one of them pushes `&body` and `-1` instead of an index, and
  `tools/gnatives-name-stubs.py` measures what that set is: **467** stubs, 467 distinct bodies, a distinct
  `{pool name, body}` record for each (no name twice), **0** bodies shared with the 123 indexed stubs and **0** in
  the live table -- exactly what `cmp eax,-1; jz ret` predicts. So 467 of `native_targets.csv`'s 1,104 records are
  the name side of these stubs, and the index-less registration path is now visible as code rather than inferred
  from the binder.
- **Coverage, and the remainder named.** 467 of the 523 index-less declarations in `inventory/natives.csv` have a
  `-1` stub; the 56 that do not are 41 `Object`, 13 `BrushBuilder` (editor-only), 1 `MaterialFactory` and 1
  `UPreviewSkinnedButton` -- printed by the tool, not left as a total.
- **One encoding, two artifacts.** `gnatives-name-stubs.py` imports `find_auto_stubs` from `tools/gnatives-stubs.py`
  rather than re-writing the pattern, so the two artifacts cannot drift on what a stub is.
- **Artifact, target, check.** `inventory/gnatives_name_stubs.csv` (467 rows: stub, block, body pointer, body, name,
  class, declaration, in-live-table), `make gnatives-name-stubs`, and a synthetic `--check` (the shape, the majority
  registrar, the block rule, the name join). Image plus three committed CSVs; no boot, no Ghidra.

### Execution notes (2026-11-10, U7 closes: the storeless 123 are 123 stubs, and the ten sit in them)

- **The blind 123 were never blind, and they are not records either.** Each storeless slot has its own registration
  stub in `.text` -- `push &body; push <index>; call 0x46c60` -- and the **index is the second `push` immediate**.
  `tools/gnatives-stubs.py` finds **127** candidates, rejects 4 (their second immediate is an address, not a slot
  number), and the remaining **123** name one live slot each: **123 of 123 store exactly the value the guest holds**,
  the stub set is **disjoint** from the 233 store sites, and 233 + 123 = **356 of 356** live slots. So the static map
  is complete up to a name, and the earlier reading ("filled by a walk over the record block") is corrected: the
  record table carries most of the same bodies for its own purpose.
- **The registrar is read, so the write is no longer inferred** (`0x0046c60`): one-time `rep stosd` of 4,096 slots
  with the sentinel `0x000404a0`, a return without storing for a pushed `-1`, and `mov edx,[ecx]; mov
  [eax*4+0x6313e8], edx` otherwise -- the exact `[reg*4+base]` fill a store-instruction scan cannot see. The image
  also holds **467** stubs that push `-1`; the registrar discards them and none of their 467 bodies is a live slot
  value, which is the name-resolved half (`tools/native-binder.py`) visible as code.
- **The ten are ordinary.** 302, 333, 384, 385, 501, 503, 509, 511, 528, 561 are registered by stubs at `0x417850`,
  `0x4186b0`, `0x417810`, `0x417830`, `0x4145e0`, `0x414600`, `0x414620`, `0x4145c0`, `0x4145a0`, `0x414640`, each
  with its index. They were the record pass's residue only because their bodies are pushed as addresses of dwords in
  a bodies-only `.data` array, with no `{name, body}` pair near them. What they still lack is a declarer, not an
  address -- U7's "83 slots with no declarer" row, unchanged.
- **Artifact, target, check.** `inventory/gnatives_stubs.csv` (123 rows: stub, index, body pointer, body, live
  address, name, agreement, registrar), `make gnatives-stubs`, and a synthetic `--check` that `make ci` drives
  through `tool-check-contract` (the shape, all three semantic rules, the `-1` variant). Image plus two committed
  CSVs; no boot, no Ghidra.

### Execution notes (2026-11-10, U7's blind spots: 113 of the storeless slots are a record's body, 10 are not)

- **The store scan's 123 blind slots are mostly not blind.** `tools/gnatives-records.py` pairs each live `GNatives`
  slot's value with the `{pool name, body}` records in `.data` (883 distinct bodies over 1,104 records) and **113 of
  the 123** storeless slots are a record's body -- so the loader walks the record block instead of storing per slot,
  which is exactly why a store-instruction scan cannot see them. **230 of the 233** stored slots are recorded too,
  so the two instruments agree wherever they overlap, and no record's body is held by two slots.
- **Ten slots remain**: 302, 333, 384, 385, 501, 503, 509, 511, 528, 561. Their values do appear as dwords in
  `.data`, but the dword beside each is a null, the float `0x3f000000` or another `.text` address, so those 8-byte
  pairs are not `{name, body}` records. Nine of the ten sit in two neighbourhoods
  (`0x00104840`-`0x00112980`, `0x0020cb70`-`0x0020cbf0`), which is where a second registration shape would live if
  the build has one -- that is the next read, not this run's.
- **Artifact, target, check.** `inventory/gnatives_records.csv` (356 rows, one per live slot: the store flag, the
  record VA and the record's own name), `make gnatives-records`, and a synthetic `--check` that `make ci` drives
  through `tool-check-contract`. It reads the image and two committed CSVs, so it needs neither the boot nor the
  symbol map: the record's own name is the pool's spelling of the function.

### Execution notes (2026-11-10, the registration-site census gets its target, and the artifact still reproduces)

- **`inventory/registration_sites.csv` was the last artifact `docs/ARTIFACTS.md` flagged "no target runs it"**, and
  its own document had the hole in the same place: the sentence introducing the scan read
  "(`tools/registration-sites.py`, `inventory/registration_sites.csv`, )" -- the target belonged in that empty slot.
  `make registration-sites` is now that target (dump, no boot, no Ghidra), the help line and `.PHONY` carry it, and
  the `ARTIFACTS.md` row names it. It is the third census to move off ad-hoc command lines this week, after
  `class-ctors` and `gnatives-writes`.
- **The instrument reproduces its committed artifact byte for byte.** Running the new target against
  `$HOME/nfs/sg1-dump/.../default.xbe` reproduces all 493 rows in the same order with the same literals --
  `diff -q` clean against `inventory/registration_sites.csv` (44,981 bytes). That is the check the missing target
  cost: the file was hash-pinned but regenerable only by remembering a command line, so "does the committed census
  still describe the image" had no answer that did not depend on a session's notes.
- **The 493rd site is a call from a named function, not an unnamed one.** `0x0010d6a9` sits inside `Engine_UnConn`
  (`0x0010d630`, 183 bytes) in the committed map; it is the one constructor call whose window holds two identifier
  literals (`User`, `NetConnection`), so it is still reported unnamed -- but the residue is a boundary in
  `classes.csv`, not a hole in the function map.

### Execution notes (2026-11-10, U7's static half: the image writes 233 of the 356 slots, and all of them right)

- **Following the store sites buys two thirds of the table with no boot.** `tools/gnatives-writes.py`
  (`make gnatives-writes`, `inventory/gnatives_write_sites.csv`, `--check` in `make ci`) scans for the two store
  forms that name an absolute slot -- `C7 05 <disp32> <imm32>` and `89 <modrm> <disp32>` -- and resolves the second
  one's value through the instruction that *ends* at the store. Measured: **233 sites over 233 different slots,
  every one of them storing exactly the address the xemu dump holds there (233 of 233), 0 static-only slots, and
  123 runtime slots with no static store.**
- **The 123 are a structural blind spot, not a gap in the scan.** All 233 share one form -- `mov [disp32], ecx` with
  the value read from a `.data` pointer table first -- while the missing slots are filled through
  `mov [ecx+eax*4], edx`, whose modrm byte encodes no absolute address. So option 2 validates the runtime map over
  two thirds of itself and cannot complete it; U7 now says that, with the numbers.
- **The stores sit outside the function map.** 2 of 233 sites fall inside a symbolised function: they are
  compiler-generated accessor pairs across `0x0040cb1e`-`0x00413ab2`, one setter per slot, each with a branch that
  also writes the same value 16 KB later at `0x006353e8`. That second array is *not* a mirror of the table (614
  stores over 9 offsets), recorded so a later reader does not conflate them.

### Execution notes (2026-11-10, the store sites spell their slot twice, and the three nothing declares are token bodies)

- **Every setter carries the slot index as an immediate.** The 233 `mov [GNatives+slot*4], reg` sites sit in
  compiler-generated setters that also write the slot to one dword at `0x006353e8`
  (`c7 05 e8 53 63 00 <index>`): **231 of 233 carry it** (slots 2-543), and the two that do not are slots 0 and 1,
  whose setters store through `eax` (`a3 e8 13 63 00`). The literal and the displacement agree **231 of 231**, so no
  slot had to be inferred from the address it is stored at. All 233 annotate the slot (the two that carry no
  immediate, slots 0 and 1, write it through `edx`), so the artifact gains a `slot_literal` column and `--check`
  refuses a row whose literal disagrees with its slot. The same count corrects the "614 stores over 9 offsets" line
  `docs/NATIVE_VM.md` had: the region does take 614 stores, but only offset 0 is these annotations and 307 of them at
  `0x00635560` are another structure.
- **Slots 66, 67 and 68 are token bodies, not Object slots.** 356 live slots split 56 below `0x60` / 16 in the
  `0x60`-`0x6F` trampoline band (`0x000464f0`-`0x000467c0`, 48 bytes each) / 284 at `0x70` and above, where the first
  declared index is 112 = `execConcat_StrStr`. Three low-band bodies fix the numbering by their own bytes -- slot 11
  is a bare `ret 8` (`0x00040690`), slot 29 stores a dword read from `FFrame::Code` (`0x00040a60`), slot 30 the same
  through `movss` (`0x00040a80`) -- i.e. stock `EX_Nothing`/`EX_IntConst`/`EX_FloatConst` at `0x0B`/`0x1D`/`0x1E`, so
  66-68 are tokens `0x42`-`0x44`.
- **One is reached by name, two forward a call.** 66 (`0x000404b0`) bails out of an unequal `0x64` guard with no
  result and otherwise hands three dwords and a byte to the first virtual of the global at `0x005fe148`; two bodies
  outside the table (`0x0040510`, `0x0040590`) evaluate an expression, and when the next code byte is `0x42` tail-call
  `GNatives[66]` and take the value from `0x005ff178` -- so `0x42` follows an expression and never answers one. 67
  (`0x000409a0`) resolves an 8-byte member pair through `Context + [op+0x48]` and re-enters via `[vtable+0x54]`, the
  `ProcessEvent` slot. 68 (`0x00040a30`) writes the only 8-byte result in the const block, `{(v ? Context : 0), v}`.
  No package declares any index below 112, so the three stay `Native_66`-`Native_68` with their behaviour measured
  and named from it.

### Execution notes (2026-11-10, the 31 class-table stores that sat in no function are seeded by their own evidence)

- **A store names both the class and the entry.** `SeedStores.java` (new, in the Ghidra chain after
  `SeedGaps`) takes `tools/class-ctors.py --stores-out` — the whole 1,036-store census, not today's orphan
  list, so a re-import reproduces the same seeds — and for every store outside a function tries the byte
  after the nearest `0xCC` fill run, keeping a candidate only when three things are measured: the body
  contains the store, the instruction at the store is a `MOV` whose scalar operand is the table the row
  names, and the body ends at a terminator. Measured: **15 entries seeded, 23 of the 31 stores now inside a
  function, functions 14,957 → 14,973**, and `inventory/xbe_store_seed_residual.csv` lists the rest.
- **The registration chain closes 13 of its 15 gaps.** `tools/class-ctors.py` then classifies the shapes:
  **1,028 stores in 1,014 functions, 522 constructor-shaped / 493 destructor-shaped / 13 unclassified**,
  **928 names (463 `ctor_` + 465 `dtor_`)**, `FUN_*` **12,020 → 12,023** (16 new functions, 13 named), and
  the independent check — of the 466 classes a registration literal names, does the registration reach the
  function classified as the constructor — goes **451 → 464 with 0 disagreements**. The nine classes with no
  constructor name at all are down to three (`SGScreenDOF`, `TextBuffer`, `HTTPDownload`).
- **Two things the pass corrected rather than assumed.** The "`BoolProperty` block" near `0x24afc7` is not a
  set of constructors: all seven stores there belong to the 809-byte function at `0x0024af30`, which calls
  `0x00055ed0` first and does not return `this`, so it is one function writing several property tables (the
  unclassified count 5 → 13 is that finding, not a regression). And `HTTPDownload`'s registration names
  `0x2c2650` while the seeder's promoted block head is `0x2c2675` — 37 bytes inside the function the
  registration already knows, which is a boundary question a store seed cannot answer; it is the one
  residual where a name is still missing for that reason.
- **What is left is counted.** 8 stores sit in no mapped function, each with the test it failed in the
  artifact — seven where the body from the nearest boundary ends before the store
  (`0x38d04`, `0x38d4b`, `0x3a180`, `0x3a5dd`, `0x3a727`, `0x3c41d`, `0x3c494`, `0x6cefe`) and two where the
  body does not end at a terminator. Nothing is named from them.
- **The artifacts it moved are regenerated, and six locality labels moved with them.** The 13 new names gave six
  functions a nearer assert on the other side of their boundary, so `inventory/function_module_attribution.csv`
  now says `Engine` where it said `Core` for `0x0012ea70`, `0x0012eb30`, `0x001c4e60`, `0x001c4f30`, and `IpDrv`
  for `0x002c50b0`, `0x002c5180`; the totals move to **6,092 of 14,973 (40.69%), 4,717 propagated against 1,375
  asserted, 5,854 runs** (the older notes' 6,063 / 40.54% / 5,826 runs are the pre-seeding values). That is the
  disputed half of the rule behaving as documented: `--holdout 400` scores **95.7% (266/278) where both
  neighbours agree and 68.8% (53/77) where they disagree**, so `module-export-census --check` now holds the
  attribution to its **`assert`** rows only and reports the propagated rows that moved since the export round
  (`6 propagated label(s) moved`, naming them) instead of failing on a snapshot of an older run of a heuristic.

### Execution notes (2026-11-10, every class's constructor and destructor is named by the table it installs)

- **A vtable store is a constructor's signature.** `tools/class-ctors.py` (`make class-ctors`,
  `inventory/class_constructors.csv`) scans `.text` for every `C7 /0` store whose immediate is one of the
  492 measured class tables: **1,036** stores, **1,005** inside a mapped function, **998** distinct
  functions (7 install two tables), **31** in no function. Two independent signatures — the store comes
  *after* a call into the base constructor and the body returns `this` — classify **507** constructors and
  **493** destructors, and leave **5** unclassified rather than averaging a disagreement.
- **915 names, and `FUN_*` 12,935 → 12,020** (450 `ctor_<Class>`, 465 `dtor_<Class>`). The independent
  check is the registration chain: of the 466 classes a registration literal names, **451** reach exactly
  the function classified as the constructor and **0** disagree. The 15 that do not are classes whose
  constructor's store is one of the 31 in no mapped function — a seeding gap, not a naming one.
- **What stays `FUN_*` is counted, not glossed.** 25 classes have more than one constructor installer and
  keep only the registration-reached name (so `FUN_0003af60` is `Class`'s 52-byte-argument overload, and
  U6's last open item closes as *classified, unnameable as an overload*); 5 stores are unclassified; the 31
  orphans are listed by address; the 26 tables with no registration literal stay unnamed because a name
  needs a class name.
- **The override file and the function map had drifted and the census caught it.** `#331`'s
  `ucpp_Register_GoauldDrone` sat in `symbols/xbe_overrides.csv` but not in `symbols/xbox_functions.csv`,
  because no import had run since it was added. `--apply` now applies the committed overrides to the map as
  well as adding its own rows; `docs/NATIVE.md`, `inventory/function_modules.csv`,
  `inventory/xbe_functions_by_module.csv`, `inventory/module_export_census.csv`, `docs/MODULE_CENSUS.md`,
  `docs/STATUS.md` and the README badges were regenerated from the resulting names.

### Execution notes (2026-11-10, all 493 class registrations are read from the call sites, and the 26 the table missed are engine base classes)

- **The count in `docs/FORK.md` is now an artifact, not a citation.** `tools/registration-sites.py`
  (`inventory/registration_sites.csv`) scans the executable sections for every
  `E8 rel32` into the class constructor `0x0003af60`: **493** sites, matching the number the class-table
  pass cited. For each it reads the `68 imm32` pushes in the 96 bytes before the call and resolves them
  as wide or ASCII literals; a normal site carries three -- `"new"`, the outer package `"System"` and the
  class -- so the class is the literal `inventory/classes.csv` knows, the outer is a package name, and a
  site with more than one unknown identifier is reported unnamed rather than picked.
- **466 of 493 reproduce `inventory/xbe_class_table_names.csv` exactly, 0 disagree**, and the tool exits
  nonzero on a disagreement: the table's `C7 /0` store and this literal walk are independent, so 466/466
  is the accuracy measure rather than a restatement.
- **The 26 residue sites are all engine base classes** -- `Object`, `Engine`, `Player`, `Texture`,
  `RenderDevice`, `Viewport`, `Material`, `Subsystem`, `Channel`, `NetDriver`, `LevelBase` and the like --
  which is exactly why a class-table pass misses them: their registration is the engine's own constructor,
  not a generated `ucpp` stub, so there is no stub body for a table store to sit in. All 26 push `System`
  as the outer package. One site (0x0010d6a9) carries no class literal in its window and stays unnamed.
- **What is still not claimed.** A literal names the class being registered; it does not prove the class
  object was created by that site at runtime, and this is a static scan of `.text`, so a site inside
  unreachable code would count too. What it does establish is the population: every registration site is
  found, and 492 of 493 name their class from the image's own bytes.

### Execution notes (2026-11-10, U6's four bodies: one was a stub the decompiler mislabelled, three are the DOF counter)

- **`0x003107b0` is `ucpp_Register_GoauldDrone`, and the evidence was in the argument list all along.** The
  decompiled call reads `FUN_0003af60(0,0x9e4,4,…,&DAT_0053b42a,uStack_4,L"System",…)`; `0x53b42a` is the
  UTF-16 `L"GoauldDrone"`, pushed at `0x00310806`, and the body asserts `ReturnClass` at
  `...\stargatevehicles\src\GoauldDrone.cpp:5`. Object size 2,532 makes it the fortieth row in
  `symbols/xbe_overrides.csv`. **`&DAT_` is a decompiler admission of doubt, not a missing string**, and the
  listing block beside every decompile still holds the `PUSH` that carries the VA.
- **The other three of the four are game code, and their seams name them without a decompiler.** Each asserts
  through `appAssertFailed` at its own source line, and both the expression and the
  `\<Module>\Src\<File>.cpp` path are immediates: `0x00311520` asserts `Mesh` at `StargateCar.cpp:190`
  (three seams), `0x00316b10` `DOFResourceRefCount > 0` at `SGCameraEffects.cpp:140`, `0x00318060`
  `SharedPixelShaderDOFMaterial == NULL && BlurTargets.Num() == 0` at line 119. A third `SGCameraEffects`
  body outside the 43, `0x00318140`, asserts `DOFResourceRefCount == 0` at line 162, and all three read the
  counter at `0x007b93f8` — so what "not a registration stub" means here is the depth-of-field resource
  counter and its constructor/destructor pair.
- **The extractor had been naming nothing for a while, silently.** `appAssertFailed` was named from its own
  literal on 2026-10-24, the export prints the name, and `tools/ucpp-registrations.py` was still matching
  `FUN_00058a30` — so it found no assert in any body, reported **0 registration stubs** against 40 that hold
  one, and said so only inside a long "left unnamed" list. A pinned symbol is a stale symbol: the tool now
  accepts both spellings and `--check` drives each. Its `--merge` mode exists because 38 of the override
  file's 78 rows are other tools' work, which a plain write drops.
- **What is still open in U6 is the constructor's own name** (`FUN_0003af60`, 493 call sites, installs the
  `Class` table at `0x004d4870`): the callers now carry class names for the 40 game-module stubs, but the
  name has to fit the engine-side registrations too, and that is the address-level caller pass PLAN 4.7
  keeps; until then it stays `FUN_*` per `docs/CONVENTIONS.md`.

### Execution notes (2026-11-10, the 6,063 bodies are 5,913 analysis targets, and the attribution survives its own literal check)

- **The export's population is classified, not guessed.** `tools/module-export-census.py` (`make module-census`,
  `--check` in `make ci`) reads the 6,063 bodies `export-module.sh` leaves under `$WORK/module-export` and measures
  four independent things per body: its XBE section, its direct `E8 rel32` call sites over the whole image, its dword
  references in `.rdata`/`.data`/`.data1` (vtable reach), and what its string literals say (`__FILE__` source path,
  asset path, ini/config, printf format, prose, none). One pass over 6,063 files takes **0.7 s**.
- **All 6,063 are function entries in `.text`** and the attribution now has an independent check: of the 848 bodies
  whose literals name a `.cpp`, **837 name the module they were attributed to**. The eleven that do not point one
  way -- each is attributed `Core` and names `Engine` (8), `IpDrv` (2) or `XboxAudio` (1) in its own `.cpp`, because
  `UnClass.cpp`/`UnTemplate.cpp` compile under `Engine` in this build's tree and the `Core_` name prefix is the
  weaker label of the two -- so they are 4.5's address-level correction, not a census edit, and the census page
  lists them from the artifact rather than in prose.
- **150 of the 6,063 are not analysis targets**: 133 `ret`-only slots (median 5 bytes -- a native registration whose
  C++ side is empty) and 17 jump thunks. The native half's work list is **5,913 bodies**, 1,310 of which can be tied
  to a translation unit today and 4,753 of which cannot; 1,428 are reached through a vtable rather than a call and 519
  by neither. 1,298 bodies carry a module-prefixed name, 39 `ucpp_*`, 26 `Native_*`, and **4,608 are still `FUN_*`**.
- **`Core` is not mostly library by the evidence it carries, and the census will not call it that.** 564 of 3,238
  `Core` bodies declare their own source path, 102 are `ret`-only, 2,110 carry no literal at all; across the export
  3,472 of 6,063 (57.3%) carry no literal. That is where a static link lives -- the libraries assert nothing -- but a
  UE2 function without an assert has the same signature, so the population is located and not named: the 100
  most-called bodies are 83 `Core`, 45 of them literal-free, headed by `FUN_00055ed0` (37 bytes, 3,279 call sites) and
  `appAssertFailed` (2,448), which is 4.2's signature work (U9) rather than a fifth census.
- **Two things measured about the image.** `Section.executable` cannot separate code from data on this build: `.rdata`,
  `.data` and `.data1` all carry flags `0x7`, the same `0x4` bit as `.text`, so the first version found **zero** data
  references by skipping the data as code -- the split by *name* is what holds, and it finds 23,821 dword references
  into 1,428 bodies. And one body's Ghidra extent runs past its census size (`0x000816e0`: 128 vs 72, the only
  disagreement in 6,063), so `--check` does not gate on size and the exporter's "0 inside another function" stands.
- **Next**: the renderer half is small enough to read -- `D3DDrv` is 129 bodies, 22 with a source path, 53 carrying
  prose and 4 a format string -- so 4.8's next measurement is one of those bodies, not another pass over the corpus.

### Execution notes (2026-11-10, naming: 27 bodies name themselves, the linker's twins name none, and the names are in the map)

- **The pass the last two notes asked for, with its ceiling.** `tools/body-name.py` reads every exported
  body and classifies it: `already named`, `own method literal`, `ambiguous method literals`,
  `byte-identical twin of a named body`, `source file only`, `none`. **27** unnamed bodies carry exactly one
  distinct `Class::Method` token in their own log text, so the image names them outright -- Core's file
  manager and shutdown paths, the Xbox Live task methods (`XboxLiveManager_StartStats*`,
  `StatsReadTask::Finished`), `USpline`'s three script-visible `exec` natives and `ASVehicle::physKarma`.
  One keeps the game's own misspelling (`SessionQueryTask::Finshed`) because the literal is the evidence,
  and `FUdpLink::BindPort` names two bodies, which is an overload pair reported rather than deduplicated.
- **Two refusals are part of the design.** A body naming two methods is `ambiguous method literals` and is
  not named at all (a body that logs about another object is common here), and a byte-identical twin under
  32 bytes is refused -- two different getters compile to the same six instructions. The refusal counts are
  in the artifact, so the next session can see what was skipped instead of trusting a total.
- **The twin route is a measured zero.** The linker did fold code: **19** groups hold **330** unnamed
  bodies -- but not one group has a named member, because what is folded here is the 33-byte stub shape,
  not a function anyone has named. The instrument stays in `make body-name`, so the first named member of a
  group names the rest for free; today it names none.
- **Applied, not proposed.** `--write-overrides` appends the candidates to `symbols/xbe_overrides.csv`
  (append-only, ten fields, CRLF, idempotent: a second run adds no row), and the names were carried into
  `symbols/xbox_functions.csv` **names-only** -- 27 renamed, 0 added, 0 removed, 0 size or module changes --
  so `symbols/xbox.map`, `docs/NATIVE.md` and `inventory/xbe_functions_by_module.csv` all carry them. That
  CSV is otherwise the Ghidra product (`run-analysis.sh` applies the same override file on a full import);
  this pass made the same edit and proved it moved no address, size or module.
- **The ceiling is the finding**: 27 of 4,625 unnamed corpus bodies (0.6%) name themselves, and the library
  sections name none (`docs/LIBRARIES.md`). Together they close the "read the strings for names" idea for
  good -- 4.2 is signature-data shaped (U9), and the next thing worth doing with names is a runtime pairing,
  not another static pass.

### Execution notes (2026-11-10, the library sections: 0 of 3,109 SDK bodies names itself, so 4.2's static half is closed)

- **The question 4.8 asked next was a naming pass, and this is its bound.** The engine modules name their
  translation unit in an assert (`1,310` of `6,063` bodies), which is what `tools/body-read.py` turns into
  file, class and work list. `tools/library-bodies.py` asks the same of the seven sections the XBE's library
  table puts SDK code in (`XNET`, `XONLINE`, `D3D`, `D3DX`, `XGRPH`, `DSOUND`, `XPP`): **3,109 bodies,
  565,157 bytes, and not one references a build-tree source path.** 169 reference any string at all (107 of
  them through a `push imm32`, so 62 of the 169 are a data-table dword landing on a literal rather than an
  assert), and the whole vocabulary is 363 strings.
- **What the vocabulary says is function, not name** -- and that is still worth having: `XGRPH` + `D3DX` are
  the XDK's shader assembler (instruction-combination diagnostics, `ps`/`xvs` version statements, the
  `PS_COMBINEROUTPUT_*` tables, libpng's messages in D3DX's texture loaders), `XONLINE` is an Xbox Live
  client (UPnP `WANIPConnection`/`WANPPPConnection` URNs, `/xpnfront/xmessaging.srf`, `contentmeta.xbx`),
  `D3D` is silent apart from one body carrying the SDK version `1.00.5849.1`, and `DSOUND` carries no
  string at all.
- **The two class-level routes are closed too.** RTTI would have named classes from `.?AV<Class>@@`
  descriptors; the image holds four, all CRT exceptions (`exception`, `std::logic_error`,
  `std::length_error`, `std::out_of_range`), so the map's single real C++ name in these sections
  (`?assign@?$basic_string@...`) is that same corner and not a method.
- **The boundary is two-way.** 9,551 of the sections' 11,479 direct calls resolve to a known body and
  **1,463 of those leave the section**, i.e. the SDK code calls back into `.text`'s CRT/XAPI half; 4.8
  measured the engine calling *into* the device through 20 vtable offsets, and this is the other direction,
  which is why neither module reads independently.
- **Reconciled, not contradicted:** `docs/IMPORTS.md` says 22 names in these sections; 21 are `thunk_FUN_*`
  jump stubs and the 22nd is the mangled C++ one, so the semantically named count is **1**. The artifact
  carries `named` and `thunk` as separate columns rather than one "not FUN_" flag.
- **Next**: 4.2 has no static route left, so the next measurement on it is a *supplied* database (U9) or a
  runtime pairing (U11's route); the runtime one has more value now, because `symbols/xbox.map` can name the
  device's vtable targets once the caps are observed.

### Execution notes (2026-11-10, the console half: `XboxDrv` is a viewport manager of 22 bodies, and `Default.ini` says so)

- **The module named after the console is not the console's renderer.** One more `body-read.py` pass:
  `XboxDrv` is 22 bodies and 4,349 bytes against `D3DDrv`'s 129 and 37,786 for the same job description, and its
  five asserting bodies name `XboxClient.cpp` and `XboxViewport.cpp` under `...\GameXBOX\XboxDrv\Src\`.
- **The config settles the wiring, not an inference.** `System/Default.ini` carries
  `ViewportManager=XboxDrv.XboxClient` (with `WinDrv.WindowsClient` commented out) and
  `RenderDevice=D3DDrv.D3DRenderDevice`, and both classes have class tables in the image (`XboxClient`
  `0x004c9560`, 196 bytes; `XboxViewport` `0x004c9618`, 464 bytes). `0x0032d6c0` is the body that builds the
  `RenDev` (`Couldn't create render device!`), so the platform layer *creates* the device rather than calling into
  it -- the two modules sit on opposite sides of that boundary (4 slot offsets <= `0x5c` here, the device's 20
  offsets <= `0xbc` there).
- **The pad arrives through the viewport.** `0x0032d860` asserts `ButtonIndex < 8`, so there is no separate input
  driver module to look for; and `0x0032dff0` is the XDK's `XBUtil_FindMediaFile(): Could not find file [%s]`, i.e.
  XDK utility code compiled into the platform module.
- **Next**: with both halves read, 4.8's remaining native question is naming -- the 302 unnamed bodies of the
  statically linked `D3D` section (U9's signature work) and the 4,608 `FUN_*` bodies of the corpus. A stock UE2.5
  image of this compiler is the only thing that would turn "decisions not classes" into a line-by-line diff.

### Execution notes (2026-11-10, 4.8's renderer half is read: five engine translation units, no call into `XboxDrv`)

- **One module, four readings per body, one instrument.** `tools/body-read.py` reads `$WORK/module-export/<module>`
  and commits one row per body: the translation unit the body's own assert names, its literal counts (paths vs
  text), its callees by module (address-form names through `function_module_attribution.csv`, module-prefixed
  names by prefix, runtime names as `runtime`), and the byte offsets of `call [obj + off]` sites. 129 `D3DDrv`
  bodies, 37,786 bytes, 194 distinct literals of which 172 are not a build path; 22 bodies name their file —
  21 a `.cpp`, one a `.h`.
- **The module is the engine's renderer, not a game-specific one.** The five `.cpp` names in those asserts are
  `D3DRenderDevice`, `D3DRenderInterface`, `D3DRenderState`, `D3DResource`, `D3DMaterialState`, all under
  `...\GameXBOX\D3DDrv\Src\`, and the class labels the asserts produce are the engine's D3D8 driver
  vocabulary -- zero bodies carry a Stargate name. The fork compiled the engine's renderer into the game image
  rather than shipping a console-specific render device of its own.
- **The negative is the finding.** No body makes a resolved call into `XboxDrv` (cross-module callees are
  `Core` 29 bodies and `Engine` 18), no body references an asset path (the export-wide count is 9, none here),
  and the device is reached through a table: 11 bodies, **20** distinct offsets, all `<= 0xbc`. Naming that
  table is 4.2's signature work, so the offsets are recorded as offsets.
- **What the strings say the device path does**, in the module's own words: display-mode enumeration and
  back-/depth-buffer format selection (`Best-match display mode: %ix%ix%i@%i`, `CheckDepthStencilMatch failed`),
  device creation with a vertex-processing choice among `hardware`/`software`/`mixed` after testing
  `D3DVTXPCAPS_MATERIALSOURCE7`, resolution changes guarded by a render-device lock, vertex/index buffer
  creation, and a vertex-shader lighting path (`VS_Lights_MAX` in 4 bodies, `Engine_UnPixelShaderMaterial`
  called from three 85-byte siblings). `docs/UNKNOWNS.md` U11 stays open: the create path names the capability
  it tests, not the cap bits it passes.
- **Next**: `XboxDrv` is the other half -- 4.8's console-specific question is whether the fork's Xbox code (the
  module with no assert-derived names at all) touches the render device's table or is only the D3D8/push-buffer
  library, which one `body-read.py --module XboxDrv` pass would size the same way.

### Execution notes (2026-11-10, the exporter pass costs 12 rounds, not 5,826: 6,063 bodies in 268 s)

- **One `@file` is one invocation.** `ExportAt.java` loops the addresses of the file it was handed inside that same
  invocation, so the pass is one import plus **one `-process` round per module**, not one round per address: the
  previous note's "3,125 exporter rounds for `Core`" read a per-*address* cost where the cost is per *round*.
  `tools/ghidra/export-module.sh` drives the real shape, keeps the project at `build/ghidra-module` (the import is the
  reusable half; `run-analysis.sh` deletes it) and times every round, so the summary is the committable artifact.
- **All twelve lists, measured.** 6,063 of 6,063 exported at their own address — 0 fell inside another function, 0
  missing — so the attributed address set is a subset of the function census `XbePost` names from. 268 s of rounds
  (`Core` 179 s, `Engine` 36 s, the ten small modules 4–6 s each), 19,454,009 bytes of C, **44 ms per body**; Core's
  bodies cost 55 ms each against Engine's 17 ms, so cost tracks body size and not address count.
- **Deterministic.** Two full runs minutes apart wrote the same 6,063 files with identical per-module byte counts
  (266 s and 268 s), so a re-run after a tool change diffs cleanly.
- **The residue is naming, not decompilation.** 4,625 of the 6,063 bodies are still `FUN_*` and 2 decompiles aborted
  (both in `Core`). The pass exports code; it cannot name it — that is 4.2/4.9's work, and the unnamed share per
  module (80% `Core`, 71% `Engine`, 58–86% for the rest) is its to-do list.
- **Next**: 4.8's native half now has its input — the bodies exist under `$WORK/module-export` — and what is open is
  classification rather than export: whether `Core`/`Engine` are mostly library code is settled by a classifier over
  the exported bodies (section, call fan-in, string refs), not by another pass. The 8,894 unreached functions still
  have no list to run over.

### Execution notes (2026-11-10, 4.5's per-module input: 12 lists, 6,063 addresses, and the pass costs 5,826 runs)

- **The lists now carry the file, not the asserts.** `make module-split --lists` reads both attribution artifacts:
  the name/override rule (1,348) plus `inventory/function_module_attribution.csv`'s propagated labels (4,715), so one
  list per module holds **6,063 of 14,957 addresses (40.54%)** instead of 1,348. `inventory/function_modules.csv` is
  unchanged byte for byte and still reconciles with the rollup; `--check` also recomputes the new plan and fails on a
  stale row.
- **`inventory/module_export_plan.csv` prices the pass.** Per module: functions, asserted vs propagated, bytes,
  contiguous runs, first and last address — `Core` 3,238 in 3,125 runs, `Engine` 2,094 in 2,002, then `IpDrv` 157 and
  nine more; 1.75 MB of code, **5,826 runs at a mean of 1.04 functions per run**.
- **So the unit is the address list, not the project.** 4.5's wording assumed a module had a region; it has a scatter,
  and `ExportAt.java` takes one address per invocation (`@<file>` loops it), so a module's pass is 3,125 exporter
  rounds, and a per-module Ghidra project buys nothing over one kept project plus a list. The doc says this with the
  numbers rather than leaving the next session to re-derive it (`docs/MODULE_SPLIT.md`).
- **Not run**: the exporter pass itself. The plan sizes it; no body has been exported from these lists yet, and the
  doc's `## Not measured` says so.
- **Next**: either pay one module's pass (`Gameplay` 24 addresses is the cheap sample that proves the loop end to end)
  or take 4.8's native half from `Core`/`Engine`, which are 88% of the covered functions.

### Execution notes (2026-11-10, the attribution artifact was a scoring run's output: 6,063 after the fix)

- **The tool wrote its artifact from a `--holdout` run, and #318 shipped that file.** `main()` wrote whatever rule
  result the run produced, so the committed `inventory/function_module_attribution.csv` came from `--holdout 400`:
  **354 rows were labelled `propagated` from a neighbour while their own assert names a module** (43 of them a
  *different* module — `Engine_AxPickupBase` and `XboxAudio_XboxAudioSubsystem` both written `Core`), and **430
  rows the full-seed run attributes were left unlabelled**, because their nearest label had been hidden. Every
  seed-dependent total in the note below and in `docs/MODULE_ATTRIBUTION.md` was therefore 400 low.
- **The score was never wrong, the artifact was.** The same `--holdout 400` run still recovers 311 of 354 (87.9%),
  so the rule's accuracy claim stands; what was wrong is the file and the counts read off it. Corrected: **6,063 of
  14,957 carry a module (40.54%), 4,715 of them propagated, `(unnamed)` 8,412** — 632 module labels move (430 rows
  gain one, 202 change) and 784 methods (`propagated` -> `assert` 354, `none` -> `propagated` 430).
- **Two guards, both in `make ci` from now on.** A `--holdout` run writes nothing (the artifact's md5 is unchanged
  across a scoring run), and `--check` now fails on any row that carries a real `asserted_module` but is not an
  `assert` row — the invariant the shipped file breaks on its first row, `0x0001afc0 carries the asserted module
  Core but is labelled propagated`. The old check validated shape and one direction of the label relationship, which
  is why a 40%-poisoned artifact passed it.
- **`tools/status.py`'s "functions attributed to a module" now reads this artifact** rather than the rollup, and
  counts only the twelve real modules (the three name-only buckets are not modules): 12.84% -> **40.54%**.
- **Next is unchanged**: point `make module-split --lists` at this artifact's `module` column, which turns 4.5's
  per-module exporter pass from the 1,348 asserted functions into the file.

### Execution notes (2026-11-10, module attribution: locality at 87.9%, 1,348 asserted labels, 6,063 labelled)

*Corrected above: this note and its artifact were written by a `--holdout 400` run, so the seed counts below are
400 low and the artifact was replaced; the rule's 87.9% is unaffected.*

- **The obvious rule is wrong, and now measured.** Voting a module from a function's attributed callers and
  callees recovers 169 of 313 held-out labels (54.0%) and reproduces `Core` 159/159 while missing 135 of 144
  `Engine` labels: every file calls Core, so the biggest module wins votes that mean nothing. Weighting a callee
  by how many functions call it removes the bias by removing the labels (24 of 50, 48.0%). Both rules ship in the
  tool and both are scored by one command, so the finding is reproducible rather than asserted.
- **Address locality is the rule that works.** This build orders code per translation unit, so a function takes
  the module of the nearest asserted function in address order within `--max-gap`. At the default `0x800` it
  recovers **311 of 354 held-out labels (87.9%)**; the sweep says accuracy barely moves with the bound while
  coverage halves with it (`0x200` 91.9% on 1,912 labels, unbounded 85.8% on 14,009 — the labelled column is the
  scoring run's, so it undercounts), which is why the strict subset is one flag away rather than a second tool
  (`inventory/function_module_attribution.csv`, `docs/MODULE_ATTRIBUTION.md`).
- **`(unnamed)` falls from 13,609 to 8,412**: 4,715 functions gain a module from the rule, and **6,063 of 14,957
  (40.54%) carry one against 1,348 that carry an assert**. The distribution stays plausible against the seeds
  (`Core` 3,238 vs 574 asserted, `Engine` 2,094 vs 586, `Gui` 145), which is what the call-graph rules do not do.
- **Attribution, not a name.** No `FUN_*` is promoted: the artifact records the rule, the round, the votes and the
  address of the assert that produced each label, and `docs/CONVENTIONS.md` still governs what a module means.
- **Next**: point `make module-split --lists` at this artifact's `module` column. That turns 4.5's per-module
  exporter pass from the 1,348 asserted functions into the file, which is what the split exists for.

### Execution notes (2026-11-10, the event form reaches the counting pass: 683 of 685, residual 22 → 11)

- **One regex was the whole gap.** `native-reconcile`'s statement parser now knows `native event` as a declaration
  form (`NATIVE_EVENT`) and matches it like a function — it names one record of its own class — while counting it in
  a new `text_events` column. `inventory/native_residual.csv` fell from **22 rows to 11** and the match from 672
  (98.1%) to **683 of 685 (99.7%)**. The eleven rows that left are exactly the phantom `text-missing` set:
  `Commandlet.Main`, `Actor.DemoPlaySound`, `PlayerController`'s four `Client*`/`Server*`, `SVehicle.UpdateVehicle`,
  `GUIController.GetMenuFont`/`GetStyle`, `GUIFont.GetFont`, `MasterServerUplink.LogStatLine`. The two `text-missing`
  rows that remain are `UPreview`'s editor package, where the text really does declare nothing.
- **The return type is the trap, and it caught the first attempt.** Five of the eleven put a return type between
  `event` and the name (`native event int Main( string Parms )`, `native final event Font GetFont( int XRes )`), so
  `event\s+(\w+)` captures `int` or `Font` as the function and the pass matched six of eleven while reporting the
  other five still missing. The pattern now takes an optional modifier run and an optional return type, and
  `--selftest` pins both forms plus the `native data` negative (16 cases, up from 11).
- **The two passes share one pattern now.** `native-mark`'s private copy is gone (`EVENT = REC.NATIVE_EVENT`), and
  the full marking pass re-run over the 2,319 class files is byte-identical to the shipped tree (`1073aef4…`, 789
  markers, 0 unmarked) with `inventory/native_bodies.csv` unchanged — so the mark and the count cannot drift apart
  again. `inventory/native_tokens.csv` is byte-identical too: the event declarations carry token 0, so no basis moved
  and the single `unresolved` row is still `UPreviewMapListClient.Created`.
- **What is left in the residual is decided, not open:** 2 editor text gaps, 2 record gaps (`Core.Object.Divide`,
  `.Round`) and 7 declarations the build binds nowhere. The next task is therefore Phase 4 work — naming bodies —
  rather than another counting pass.

### Execution notes (2026-11-10, the third declaration form: 11 `native event` bodies, marked — 789 lines)

- **Every counting pass in this repo matches `native … function`, and the text has a third form.** A statement-level
  scan of all 18 packages finds **11 `native event` declarations in 7 classes** — `Commandlet.Main`,
  `Actor.DemoPlaySound`, `PlayerController`'s `ClientTravel`/`ClientHearSound`/`ClientValidate`/
  `ServerValidationResponse`, `SVehicle.UpdateVehicle`, `GUIController.GetMenuFont`/`GetStyle`, `GUIFont.GetFont`,
  `MasterServerUplink.LogStatLine`. They are exactly the eleven `record-only` rows of
  `inventory/native_residual.csv` whose verdict is `text-missing`: the text is not missing them, the regex cannot see
  `event` between `native` and the name, so the residual table has been reporting a phantom gap.
- **They are marked, and they are the best-covered kind.** 11 of 11 carry the image's binder literal and a body
  address (the function side has 21 with no body and 12 operators without a binder). 789 marker lines in 2,319 class
  files, 0 unmarked, byte-identical on a second pass (`ff550c3b…`); against the tree the operator pass left behind the
  diff is 108 added lines (97 + 11) and 0 removed, every one a marker.
- **The last two `record-only` rows are the editor package's** (`UPreviewMapListClient.Created` declares only
  `Super.Created();`, `UPreviewSkinnedButton.SetRenderInfo` spells nothing), so `text-missing` is right for them: the
  editor build's natives have no text in the shipped packages.
- **Next measurement, named and not done — a counting change belongs in its own PR:** teach `native-reconcile` the
  event form and re-derive the residual table. Eleven of the thirteen `record-only` rows move to matched, so the
  reconciliation reads 683 of 685 (99.7%) instead of 672 (98.1%) and `text-missing` shrinks to the two editor rows.
  *(Done in the note above: 683 of 685, residual 11 rows.)*

### Execution notes (2026-11-10, every native declaration is marked — 778 lines, 681 functions and 97 operators)

- **One pass, both kinds, no removals.** `tools/native-mark.py` marks `operator` declarations now: **778 marker lines
  in 2,319 class files, 0 unmarked**, and the tree is byte-identical on a second pass (`d0aa6c05…` over 2,319 files).
  Diffed against the tree the previous pass wrote, the change is **97 added lines and 0 removed**, every added line a
  marker — so the 681 function markers are untouched and the function half of the table still reads
  674/660/660/389.
- **The join cannot be by symbol.** `Core.Object` declares `==` eight times (`bool`, `int`, `float`, `vector`,
  `rotator`, `string`, `Object`, `name`), each composing a different record, so `(package, class, function)` cannot be
  the key for an operator. `inventory/native_bodies.csv` gains `kind` and `record`, the marker gains `record <name>`
  between the token and the binder, and the pass joins the ordered declaration list to
  `inventory/native_operators.csv` **by declaration text** — a text the table does not carry raises and the tree is
  not written, so a marker cannot land on the wrong declaration.
- **Operators cover better than functions do.** All 97 compose a record and **all 97 carry a body address** (the
  function side has 21 with no body anywhere in the image); 85 carry the image's binder literal, and the twelve that
  do not are the string operators the pool spells `_StringString` where the script writes `_StrStr`. 96 of the 97
  bodies are in the function census — the exception is `Object.ClockwiseFrom_IntInt`, the operator-expansion record
  the census note already names, which is why `not-in-census` reads 272 here against 271 on the function rows alone.
- **The 92-to-97 gap was the span parser, not the table.** `declaration_spans` counted operators with
  `native-reconcile`'s one- or two-character symbol class, which cannot spell `$`, `$=`, `>>>`, `ClockwiseFrom`,
  `Dot` or `Cross`; operator spans come from `native-operators`' own `DECL` now, and `--selftest` pins those six. The
  five missed declarations are all `Core.Object`'s (86 there against the text's 91; 92 in total against 97), which is
  why "0 unmarked" read 0 before: the missing declarations were not spans, so nothing counted them as missed.
- **3.9 is closed by this.** What it does not cover, by construction: the thirteen operator records the compiler
  synthesises from other operators (no declaration exists to mark; they are in `inventory/native_tokens.csv` under
  `operator-expansion`), the 21 declarations whose bodies are not in the shipped image (14 editor-only, 7 unbound —
  `inventory/native_residual.csv`), and the C++ itself, which is Phase 4.

### Execution notes (2026-11-10, an `operator` declaration is one record, not a set: 97 declarations, 97 records)

- **The "92" was the marker's regex, and the text has 97.** `native-reconcile`'s `NATIVE_OP` takes a one- or
  two-character symbol and no identifier form, so it cannot see `$`, `$=`, `>>>`, `ClockwiseFrom`, `Dot` or `Cross`
  — six of `Core.Object`'s declarations. `tools/native-operators.py` parses per *statement* (a regex over the whole
  text lets `native` from one statement and `operator` from the next compose a phantom declaration: 91 against 87)
  and composes the record name by the rule the records themselves show: symbol stem (`==`→`EqualEqual`,
  `$`→`Concat`, `@=`→`AtEqual`, `>>>`→`GreaterGreaterGreater`, `**`→`MultiplyMultiply`) plus one type suffix per
  operand in declaration order, `out`/`coerce`/`skip` dropped.
- **Every declaration binds exactly one record: 97 of 97**, across `Core.Object` 91, `Engine.Actor` 4 and
  `Engine.StreamBase` 2 — so the worry that one statement names several records has it backwards. The record count
  is *higher* than the declaration count because the compiler synthesises records: 13 in `Core.Object` for
  `++`/`--` and unary `-`/`!`/`~` (104 operator records for 91 statements; 107 token-carrying operator records
  overall, which is `inventory/native_tokens.csv`'s count).
- **The two spellings are both measured, not corrected.** Script `string` composes `Str` (`At_StrStr`), the pool
  registers `String` (`At_StringString`, 12 rows); the composed name is kept in its own column so the alias stays
  visible. A prefix test is also not the classification: five `Core.Object` names merely begin with an operator stem
  (`Atan`, `Round`, `Divide`, `Nothing`, `OrthoRotation`).
- **What this unblocks:** the marker for an operator is one line naming its record and token, the same shape as a
  function's marker, and `inventory/native_operators.csv` is the table it needs. Wiring that into the tree writer is
  the next step in 3.9, not a new question.

### Execution notes (2026-11-10, the 274 native bodies are function starts: the census is 15,231, not 14,957)

- **The verdict is the disassembler's, not a byte pattern's.** `tools/native-census.py` writes
  `inventory/native_census.csv` (883 rows, one per distinct body address) with three kinds of evidence: the address's
  relation to `symbols/xbox_functions.csv`, the image's byte before it plus its first 8 bytes, and the answer
  `tools/ghidra/ghidra_scripts/NativeCensus.java` gets by *creating* a function at the address. The pattern columns
  only corroborate — `SeedGaps`' opening-byte list once hid 47 real entries — but they corroborate completely: 274
  of 274 follow a function boundary (`0xCC` 251, `0x00` 15, `ret` 8) and 274 of 274 open on an MSVC prologue shape
  (`arg-load` 117, `push-reg` 115, `seh` 21, `alloc` 15, `empty-return` 4, `push-imm` 2), 0 unclassified.
- **All 274 are accepted function starts.** One Ghidra import over the 883 addresses: 609 already functions,
  **0 inside another function's body**, 274 accepted, **0 rejected**. Bodies: median 106 bytes / 39 instructions;
  smallest 3 bytes (four `UObject` log-filter natives whose body is `ret 8`); largest 2,016 bytes / 571 instructions
  (`Actor.GetClosestBone`); all 274 end on a flow terminator. Because the script creates them in address order, a
  false start would be reported as `inside` the body created immediately before it — none was, so no native body is
  a tail shared with its neighbour.
- **The denominator moves 1.8%: 14,957 → 15,231**, and the named share 1,988/15,231 = 13.05% (`docs/PATH.md`). The
  call-graph census is not wrong, it is short: nothing in it was displaced, the 274 are outside every range.
- **275 records, 274 addresses, 271 declarations.** 275 registration records name one of the 274 addresses
  (`Object.ClockwiseFrom_IntInt` carries two), 271 are the body of a `native function` declaration, and 3 are not
  (`Object.ClockwiseFrom_IntInt` — an operator-expansion record — plus `SVehicle.UpdateVehicle` and
  `GUIFont.GetFont`, whose declaring text spells no binding; why those two bind a body is unmeasured).
- **Repeating it is two commands, and the image half is separate on purpose:** `make native-census` recomputes the
  static and image columns and carries the disassembler verdicts over from the committed table; a fresh verdict set
  is one `~/sg1/work/native-census/run.sh` (import ~3.5 min). `--check` re-derives the census column from the two
  committed tables and needs no dump, which is what `make ci` runs.

### Execution notes (2026-11-10, every native body is marked — and 271 of the 660 sit outside the function census)

- **One tool, one table, and the tree stays out of git.** `tools/native-mark.py` writes a marker line above every
  `native function` declaration in the extracted tree and `inventory/native_bodies.csv` (681 rows: `package, class,
  function, native_token, params, binder, binder_va, body_va, module, src_native, marker`). The `marker` column is
  the exact line written into the tree, so the game-derived tree is regenerable and auditable without being
  committed. 681 markers in 92 classes, 0 unmarked, 2,319 class files written, and a second pass hashes identically.
- **Coverage, measured:** 674 of the 681 have a `natives.csv` record, 660 carry the image's binder literal *and* a
  body address, and the 21 with neither are the 14 editor-only natives (`BrushBuilder` 13,
  `MaterialFactory.ConsoleCommand`) plus exactly the 7 `declaration-unbound` rows the residual table names — the two
  instruments agree on the same seven.
- **271 of the 660 bodies are not functions in `symbols/xbox_functions.csv`.** A native has no direct caller — its
  only reference is the registration table — so a call-graph census never makes it a function. 274 of the native
  table's 883 distinct body addresses are in that position: the native registration table is an independent source
  of function starts, and the 14,957-function denominator in `docs/PATH.md` is short by them.
- **`src/Native/<Package>/<Class>.cpp` is a safe target path:** of the 5 bodies whose module attribution is named
  at all, every one agrees with its declaring package. One file per class is also the engine's own source shape.
- **Operators stay unmarked on purpose:** 92 `operator` declarations expand into 107 records, so a per-declaration
  marker would name one record of several. Their per-record mapping is `inventory/native_tokens.csv`.

### Execution notes (2026-11-09, the save path loads and cannot write)

- **The two natives, from the table the previous PR landed.** `GameInfo.LoadSaveGame` is `0x00149370` and calls the
  loader `0x00134d40` (2,472 bytes, `[VL:LOADGAME]` trail, `FAILED because no viewports!` → `Disconnect`);
  `GameInfo.SavePackage` is `0x00298bb0`, 145 bytes, and decodes its string argument and stores 0 — no call, no log,
  no writer.
- **So `bool SavePackage(string)` is false at every call site.** Both script callers
  (`GameProfile.StartNewMatch`, `GameProfile.ContinueSinglePlayerGame`) ignore the result, so the profile save fails
  silently in the shipped build and the nine `.sgs` files on the disc are authoring output rather than something the
  build writes.
- **The writer is elsewhere, and dispatched rather than called.** `FUN_00138680` (2,278 bytes) carries
  `[VL:SAVEGAME]: Saving game to file: %s` (`0x00509b50`, site `0x00138c7b`) and has zero direct call sites — the
  same shape as the two execs, which are reached through the registration table. The exporter's own summary literal
  (`SaveGame Summary Names=%d Import=%d Export=%d`, site `0x00070938`) is inside `Core_UnObjBas`, so the serializer
  behind a checkpoint is the engine's, as U5 suspected.
- **Correction**: the three `PlayerController` console commands were the right candidates and the wrong instrument —
  none is a native, so none has a `native_targets.csv` row. They are read as script (`tools/pkg-script-text.py`), and
  the body to decompile is the exec they all funnel into. Measured in the note below.

### Execution notes (2026-11-09, `0x001226a0` is the engine's name table, not the save reader)

- **The 7,486-byte body U5 pointed at is startup code.** `0x001226a0` builds **290** `FName`s (one
  `CALL 0x0005bed0` with find mode `2` each) and stores them into 296 globals across
  `0x007aae9c`–`0x007b5dbc` (`0x001226bf`–`0x00124171`), in alphabetical order from `Accept` to `ZoneChange`,
  then resolves the `Engine` and `Core` packages, walks the global class array with flag `0x2002`, and logs
  `Unreal engine initialized` (`0x0050494c`, site `0x001243b9`).
- **`LoadSaveGameInfo` is row 118 of 290** (`0x00505b14`, site `0x0012318a`), between `LIPSincAnimEnd` and
  `Login`; `PostLoadSavedGame` is row 176 and `PreSaveGame` row 189. The save names are ordinary rows in the
  engine's vocabulary, not a module marker.
- **Why the lead looked real**: a wide literal has one xref, and every engine name is in this table exactly
  once, so the body an xref search finds is always the one that appears to "start" that subsystem. The lesson is
  the same one the `vtable-reach.py` note carries — a single xref identifies *a* user of a string, not the
  function that implements it.
- **Consequence**: the save/load native half has no missing reader (loader `0x00134d40`, writer `0x00138680`,
  profile writer `FUN_001be520`); what is open is the writer's serializer arity and whether the profile row's
  slot `+0x34` is a `SetString`.
- **Next**: the serializer's arity. A `C2 xx xx` scan of `0x0006f4b0`'s whole declared body (5,552 bytes, ending
  `0x00070aa0`) finds no `RET n` at all — only `c2` bytes inside instructions — and the two `C2 28 00` at
  `0x00070b0f`/`0x00070b26` sit past the declared end, so they belong to the next function. The arity has to come
  from the call graph (an argument-count pass over the serializer's callers), not from a byte scan of its tail.

### Execution notes (2026-11-09, the write is whole and the index row is a second artefact)

- **The writer's literals, read from the image instead of the decompiler's text.** `ExportAt` labels this
  build's wide literals two bytes late, so `tools/ghidra/ghidra_scripts/ExportLiterals.java` lists a function in
  full and resolves every operand that lands in memory: on `0x00138680` it reads `Engine` at `0x004c7948`,
  `Saving` at `0x004df84c`, the log literal at `0x00509b50` and `%s%s` at `0x004d491c`.
- **`Engine`/`Saving` are a `GetString`, not the profile update the note below left as "next".** They are the
  arguments of `FUN_000584a0` → `FUN_00058010`, which formats `%s%ssystem%s%s.%s` and calls slot `+0x10` of the
  config object at `0x005fbed4` with a `0x400`-byte buffer (the `.int` file is the fallback); the writer also
  calls slot 3 of the object at `0x005fbed0` before the serializer and slot 4 after it — a begin/end pair.
- **The flag's two values are 2 and 0**, written at `0x00138c96` and `0x00138ebc` on the first element of the
  level's actor array (`[[[this+0x11c]+0x2c]]+0x708`); the serializer call sits between them and nothing else in
  the body writes that byte.
- **The writer takes seven stack arguments** (`RET 0x1c` at `0x00138f63`), so the kind of save arrives in the
  arguments. The serializer call at `0x00138cf2` pushes seven words with `ECX = [level+0x18]` as `this` and the
  caller cleans only 16, so the callee returns cleaning the rest — its arity is the one number still open.
- **The profile index is a separate artefact with its own address.** `[Saved Games]` is a 142-row section of
  `DefaultProfile.sgp` (key = `.sgs` file name, value = description); none of its rows has a shipped file and
  none of the nine files is indexed, so index and files come from different builds. The `Saved Games` literal at
  `0x00515664` has exactly three xrefs, all in `0x001be5xx`–`0x001bfax`; the save routine reaches `FUN_001be520`
  through `FUN_001be720` (`0x00138d9d`), which writes one row per element of the profile array at `+0xa8`
  (count `+0x38`, names `+0x28`, descriptions `+0x34`).
- **Instrument**: `tools/sgs-profile.py` (`make sgs-profile`) counts the index and reconciles it with the `.sgs`
  files on disc; `--check` is a synthetic self-test, so `make ci` keeps it honest without the dump.
- **Next**: answered in the notes above — `0x001226a0` is the engine's startup name table, not a save reader, so
  the native half has no hole, and the serializer's arity is now measured (note below): seven arguments, `cdecl`.
  What is left is the rebuild side.

### Execution notes (2026-11-09, the serializer takes seven arguments and cleans none)

- **The number a rebuild needed, from the code.** `tools/argc-pass.py --va 0x0006f4b0` (`make argc-pass`) reads
  the arity three ways: the frame slots the body reads (`+0x08` x10, `+0x0c` x2, `+0x10`, `+0x14`, `+0x18` x3,
  `+0x1c`, `+0x20` x8 — a contiguous run, so **7 arguments**), its epilogues (**3**, all plain `RET` at
  `0x00070981`/`0x00070a88`/`0x00070a9b`, so `cdecl`: it pops nothing), and the block at its one call site
  `0x00138cf2` (7 dwords pushed).
- **The earlier "arity not settled" was an instrument artifact, not an unknown body.** A `C2 xx xx` scan cannot
  see a plain `RET`, and the two `C2 28 00` bytes at `0x00070b0f`/`0x00070b26` belong to the next function
  (`0x00070aa0`). A strict linear sweep is the second trap: it desyncs in this 5,552-byte body (jump tables) and
  reports only `+0x08`/`+0x18`, so the sweep resyncs on an undecodable byte and stops at the `RET` that
  `INT3` padding follows.
- **The parameters are named by the callee's own asserts.** `0x0006f4dd` tests argument 1 against 0 with the
  literal `InOuter` (`0x004df8dc`, line `0x1346`) and `0x0006f4f9` tests argument 5 with `Filename`
  (`0x004df8d0`, line `0x1347`), both against `UnObj.cpp` (`0x004dd358`); `Core_UnObjBas` is the module in
  `symbols/xbox_functions.csv`. So a rebuilt caller can name two of its seven arguments.
- **The caller's `ADD ESP,0x10` for seven pushes is not a mismatch.** With a `cdecl` callee the caller must pop
  `0x1c`, and `MOV EDI,ESP` at `0x00138d01` immediately re-bases the 12 bytes left over as a 3-dword `TArray`
  header (`[EDI]`/`[EDI+4]`/`[EDI+8]`, grown by `0x00055ed0`): the compiler merged the argument pushes with a
  stack temporary. A rebuilt caller cleans `0x1c` and keeps its temporary elsewhere.
- **Instrument**: `tools/argc-pass.py` (`make argc-pass VA=0x…`) — a 32-bit instruction-length decoder with a
  resyncing sweep, frame-slot and epilogue readers, and a call-site pass anchored on the *caller's* own
  instruction boundaries (a backward "landing" scan invents chains: `01 ff` decodes as `add edi,edi`, which read
  this call as 13 pushes). It also refuses a frame count for a body that re-bases `EBP`, which is what the writer
  `0x00138680` does (`+0x30`, `+0x11c`), and reads that writer as 7 arguments from `RET 0x1c`. `--check` is a
  synthetic self-test, so `make ci` keeps it honest without the dump.
- **Next**: the rebuild side — write a checkpoint package and its `[Saved Games]` row from a level, using the
  writer's seven-argument shape and the index's `SetString(Section, Key, Value, Filename)` row. **Measured in the
  note below**: the row half is done, the package half stops at the save file's export-entry layout.

### Execution notes (2026-11-09, the checkpoint write side: row done, export table unread)

- **The row writer works, and the reader confirms it.** `tools/sgs-write.py` (`make sgs-write`) adds one row under
  `[Saved Games]` in a *copy* of `DefaultProfile.sgp`: 142 keys → 143, the row lands in that section, CRLF kept,
  no other byte changed, and a second add of the same key is refused (the index is a map). `tools/sgs-profile.py`
  re-reads the copy as `2 sections, 154 rows` / `[Saved Games] 143 keys`, so writer and reader agree.
- **Two of the three package tables already re-encode byte-identically in 9 of 9 checkpoints** (names, imports),
  which pins those regions of a rebuild. The export table is the blocker: it desyncs under the `.u` layout and
  runs off the end of the file.
- **The width is not the difference.** 210 entries in 3,401 bytes is 16.2 each; `M00.unr`'s 5,078 entries in
  86,534 bytes is 17.0 — so both are compact entries. It is not a field order either: 256 uniform width
  combinations and 15,120 ordered layouts (three width profiles x every permutation of the seven fields) give zero
  candidates that produce 210 plausible entries. The next measurement is therefore *which* structural difference a
  save package has (a table sorted differently, an entry field that is a reference rather than an index, or a
  table that does not start at `export_offset`), and a `.unr` parses with the same reader in the same run, so this
  is not a reader regression.
- **Instrument**: `tools/sgs-write.py` (`make sgs-write`, dump-side) — reports per file which tables re-encode,
  where a rebuild stops, and the row it inserted; `--check` is a synthetic package plus a synthetic profile, so
  `make ci` keeps it honest without the dump. It exits 1 only when a checkpoint parses and then rebuilds
  *differently*, because the unread table is a known state rather than a failure.

### Execution notes (2026-11-09, the save path's dispatcher is a console command and its writer is vtable slot 59)

- **`SaveGame <letter> <text>` is the whole write interface.** `PlayerController.NewSaveGame`, `QuickSave`,
  `CheckpointSave`, `MapChangeAutoSave` and `MatineeAutoSave` are `exec function`s with no native body; each builds
  that console string (`"SaveGame Manual Save"`, `"SaveGame Q Quicksave"`, `"SaveGame C" @ desc`, `... A`, `... M`).
  So the second token — the letter — is the kind of save, and `NewSaveGame` sends none.
- **The chain is closed without a boot.** `FUN_00139d50` (4,829 bytes, `this` = the `GameEngine` object) walks a fixed
  command list with an `FParse::Command`-style matcher (`FUN_00056dd0`): `SAVEGAME`, `DELETESAVEGAME`,
  `LOADCHECKPOINT`, `LOADQUICK` (+`BACKUP`), `LOADTRAVEL`, `LOADSAVEGAME`, `CANCEL`, `GSPYLITE`. `SAVEGAME` turns the
  letter into four booleans and then does `call DWORD PTR [eax+0xec]` at `0x0013a5bb` — slot 59 of the `GameEngine`
  vtable `0x00509438`, which is `0x00138680`, the `[VL:SAVEGAME]: Saving game to file: %s` writer.
- **The agreement is the evidence, and both halves are structural.** `0x00509438 + 59*4 = 0x00509524` is the only
  dword in the whole image equal to `0x00138680`, and `0x0013a5bb` is the only `call [reg+0xec]` inside the exec body.
  A stored address alone proves nothing (`.rdata` is full of plausible pointers); the call offset matching the table
  index is a boundary no coincidence passes.
- **Instrument: `tools/vtable-reach.py`** (`--va`, repeatable; `--check` is a synthetic self-test with no dump). It
  exists because "zero callers" hides two things a dword scan cannot see: a direct call is `E8 rel32` (`0x00136a40`
  has five, `tools/xbe.py xrefs` answers none) and a virtual call stores an *offset*. It prints vcall sites as
  candidates, not answers — every object's slot 59 answers to `+0xec`; the surrounding literal names the object.
- **The writer's name is now measured, and the correction matters**: `0x00138680` does not *compare* the
  `SAVEGAME`/`CHECKPOINT`/`QUICKSAVE` literals at `0x001386dc`/`0x001386ec`/`0x001386f3` (the previous note said it
  did) — it selects one as a log label (`Map Change` for the first flag, then `QUICKSAVE`, `CHECKPOINT`, `SAVEGAME`).
  The file name comes from five formats: `TRAVEL_%s`, `Q_A_%s`, `C_%s`, `MATINEE_%s` then `%s.sgs`, or
  `%s_%s.sgs` for a manual save; `C_%s`+`%s.sgs` reproduces all nine shipped `C_<desc>.sgs` names exactly.
- **Answered in the note above**: the `Engine`/`Saving` pair is a config read rather than the profile update this
  bullet assumed, the `+0x708` flag's two values are 2 and 0, and the `[Saved Games]` row is written by
  `FUN_001be520` two frames below the writer. What is still unread is `0x001226a0`'s `LoadSaveGameInfo` body.

### Execution notes (2026-11-09, the binder's map is a table in the image, so a name is an address)

- **The names had nowhere to point because the map is data, not code.** The binder's `int%sexec%s` lookup reads a
  table of 8-byte `{pool pointer, .text pointer}` records in `.data`: **1,104 records in 203 contiguous runs**
  (`0x00599e08`..`0x005c6740`), **883 distinct names**, 23 of them registered up to 12 times and always with the same
  body. `tools/native-table.py` (`make native-table`, `inventory/native_targets.csv`) reads it; U1's ledger entry
  now carries it.
- **The cross-check is the deliverable.** 261 of the 274 index-carrying declarations have a record and **261 of 261
  agree with `inventory/gnatives_slots.csv`** (the runtime dump), 0 disagreements; `intUGUIControllerexecGetMapList`
  is `0x002fe5f0`, the address U16 measured independently from a log-string xref and a breakpoint. 769 of 797
  declarations get an address, 508 of them the index-less ones `GNatives` can never name.
- **275 of the bodies are new function entries.** `inventory/function_modules.csv` has no function at 275 of the
  1,104 addresses (and none of the 1,104 is an interior address), so the records seed the map with `{name, address}`
  pairs — the next pass for 4.1 is a seeding pass, not another string or prologue search.
- **U5's two natives are addressable now**: `GameInfo.SavePackage` `0x00298bb0`, `GameInfo.LoadSaveGame`
  `0x00149370`, so the save-path read starts at the two bodies instead of the `0x0012xxxx` neighbourhood.
- **A `0xb9a`-byte bug, corrected here.** `native-binder.py`'s `binder_va` came from a whole-file UTF-16 decode, whose
  surrogate pairs collapse two units into one character and shift every later index: all 769 addresses were wrong
  (752 exactly 2,970 bytes short of their name, 17 outside every file-backed section). The column is regenerated and
  every address now lands on its own name — which is what made the table findable at all.

### Execution notes (2026-11-09, the checkpoint package rebuilds byte-identically: a zero size writes no offset)

- **The save export table was never a different layout, only a different termination rule.** The entry is
  `Class(idx) Super(idx) Outer(int32) Name(idx) Flags(u32) SerialSize(idx) SerialOffset(idx)` — the same seven
  fields as the game's own packages — except that **an export whose serial size is 0 writes no serial offset at
  all**. A checkpoint exports objects with no serialised data (class defaults, `Squad` markers, states) and
  **316 of its 1,776 exports are zero-size**, so assuming the offset loses a byte per such entry: the walk
  desyncs mid-table and leaves the file, which is what made a 16.2-byte average entry look like an unknown
  encoding for a whole session.
- **Why the earlier searches could not have found it.** 256 uniform width combinations and 15,120 ordered field
  layouts are all *present-field* hypotheses; the rule is about a field being **absent**, which no order or width
  search can express. The tell was in the bytes and was read the wrong way round: a run of 13-byte entries whose
  own `Class` resolved (`-7` → `SQUAD_JaffaSerpentL_Single`) while the next entry started on the byte the parse
  called the offset.
- **Three checks, all structural.** The walk ends exactly at EOF in 9 of 9 files (210/208/198/198/212/178/114/213/
  245 entries); all 1,776 class indices name an import whose own class is `Class`/`State`; and the `SerialSize`s
  sum to exactly `export_offset - first_payload_offset` in 9 of 9, so the payloads tile their region with no gap
  and no overlap.
- **The rebuild is now exact.** `tools/sgs-write.py` (`make sgs-write`) rebuilds every checkpoint from the parsed
  model — header, three tables, every payload at its offset — and reports **9 of 9 byte-identical**, with a
  per-table column (names/imports/exports all `yes`) that is what localised this. The `[Saved Games]` row half was
  already exact; both halves of a resumable checkpoint now round-trip.
- **The rule is additive, not a reinterpretation.** The dump's other 407 packages (279,280 exports) encode
  identically with and without it; the one file that answers differently, `M01A_sounds.uax`, reads as 20
  zero-size entries under either rule and resolves 0 of 20 classes, so its 20-entry table is unread on its own
  terms and is neither fixed nor broken here.
- **Instruments**: `ue2pkg.Package.read_exports` now carries the rule (and is what every package-side tool uses);
  `pkg-write.encode_exports` mirrors it and `--selftest` gains a synthetic package with a zero-size export;
  `sgs-write.py --check` does the same with no dump. `docs/formats/sgs-checkpoint.md` has the entry layout, the
  numbers and the checks. What is left on the save path is content, not container: a rebuilt package's payloads
  come from a compiler, and PLAN 6.2's package check still has to accept a rebuilt save.

### Execution notes (2026-11-09, the check file's records pair with files)

- **The "no filename field" conclusion was about the record, not about the data.** A record carries two 32-hex
  values; `0x001219d0` prints a package's first one as `GUID = [%08X%08X%08X%08X]` read straight out of the header at
  `+0x24`, and every record's uppercase field is exactly that text for a file that ships. Pairing all 168:
  499 packages in the dump carry a UE2 header, **168 of 168 records match one**, and the only fan-out is 8 header
  GUIDs shared by copies (`M05ATex.utx`, `M16Tex.utx`, `xx_old_m17tex.utx`, … 17 files under one GUID) whose headers
  were never regenerated. So the record order is the cooker's own GUID order, not an assumption.
- **The digest is not `md5(file)` — and the code says why.** The `MD5` console command (`0x00126860`,
  `MD5 Database Management`) has two hash passes: `-f` (`0x001219d0`) reads the file in `0xFFFF`-byte chunks and
  hashes it, `-q` (`0x00122430` → `0x00121cf0`) reads only the header with `0x00051720`, appends thirteen header
  dwords, then one 8-byte record per name entry, and hashes that with `FUN_00058b40`. The database builder
  (`0x00125a90`, `-a`/`-c`) writes records through the **quick** routine, so zero of the 168 digests equal
  `md5(file)` of the file they are filed against — a measured negative that the format page had read as "the
  digests describe something else entirely".
- **What that costs the old conclusion.** "Regenerate is out of reach" was a statement about hashing the whole
  package. The check covers header and names, so a rebuild that preserves both reproduces it; only a changed name
  set needs `-a` against a patched build. 1,360 framings of the header/name *file bytes* still miss, so the digest
  is over re-serialized fields — the last unknown is the name-entry layout `0x00121cf0` reads at `esp+0xc4`
  (`+0x0C` dword, `+0x1C` word, `+0x26` word).
- **Instruments**: `tools/package-check-pairs.py` (`make package-check-pairs`) writes
  `inventory/package_check_pairs.csv` and `--check` is a synthetic self-test with no dump. `docs/PACKAGE_CHECK.md`
  now carries the switch table with each routine's address and every negative above.

### Execution notes (2026-11-09, the writer rebuilds every container kind, not just the game packages)

- **The stage-2 claim was scoped to `.u` because the writer's file list was.** `pkg-write` filtered
  `endswith(".u")`, so "18 of 18 byte-identical" was a statement about game packages; a checkpoint is a `.sgs`
  and a map a `.unr`, and both go through the same three tables. `--ext` takes the extension set now, so the same
  writer is asked about every container the build ships: **100 of 100 rebuild byte for byte** — 18 `.u`, 9 `.sgs`
  checkpoints, 73 `.unr` maps — 100% of bytes accounted for and zero gaps in every file (`make pkg-rebuild-all`).
- **The checkpoints only join it because of the entry rule** (note above): a zero serial size writes no offset, and
  a save export is where that happens (316 of 1,776). Before it, all nine rebuilt *differently*, which is why this
  row could not be written earlier.
- **Two variables the package targets needed were undefined.** `SYSTEM_DIR` is used by `pkg-rebuild`, `pkg-layout`,
  `pkg-resize`, `package-census` and `squad-rosters` but was never defined, so each of those ran its tool on the
  empty string unless the caller passed it; `docs/REBUILD.md`'s "reproducing each claim" block therefore only
  worked with a variable on the command line. `GAME_DIR`/`SYSTEM_DIR` are `?=`-defined next to `SG1_DUMP` now, and
  every arm reads the same two names.
- **What this does not prove**: payload contents are still copied, so stage 2 says the container is exact, not that
  a build is reproducible (6.3 stays open on the compiler). `docs/REBUILD.md` says so in place rather than banking
  the wider number.

### Execution notes (2026-11-09, the play path picks a track and no name can ever match)

- **Callers answer what the bodies could not.** `Callers.java` over the kept import: `0x000b2cb0` (the `+0x48`
  clip getter) has **one** caller, `0x00220ba0` (96 bytes), which walks the clip array once for the bone link
  `0x000b4930`; `0x000b2ce0` (the `+0x54` track getter) has five, three of them inside `LIPSincPlayAnim`
  (`0x00228730`, sites `0x002287ab`/`0x002287d3`/`0x00228871`), which bails on `[track+0x24] == 0` -- the
  track's `u32 1` flag. So a play call names a **track**, and the clips are the pose side.
- **The chain has no other entry.** `0x000b5f10` has exactly one caller (`0x000b63f0` at `0x000b64f0`, key
  block in `ECX`: `MOV ECX,EDI` at `0x000b64ee`) and `0x000b63f0` has exactly one, the actor update
  `0x0022e090` (14,676 bytes) at `0x0022fe9b`.
- **The lookup cannot resolve on the shipped data.** The blend passes each clip's `+0x34` name to `0x000b4710`,
  which `__wcsicmp`s it against every group name of the track's block (`0x000b45c0`); all **58,006** groups
  are unnamed, so the answer is always `0x4c8848` and pose assembly is never reached from a curve. The `.map`
  files are not the missing source either: 15 near-one-hot speech targets against 26 groups, no index and no
  name in common. This **corrects** the previous note's guess that the `.map` was "the next instrument".
- **Next**: the `+0x24` flag and the rest of the track header's runtime use (which fields the loader's two
  group arrays at `+0x30`/`+0x3c` feed), or a non-LIPSinc task -- U4's runtime half is closed, so the next
  pick should come from `docs/UNKNOWNS.md`'s other entries. Instruments: `~/sg1/work/lip11/out4`,
  `tools/ghidra/ghidra_scripts/Callers.java`, `docs/formats/lipsinc-runtime.md`.

### Execution notes (2026-11-09, the record is a clip, and its name is read rather than stored)

- **The loader pushes all four destinations before its first call, and each callee pops only its own two.**
  `0x000bce40` is `0x00035b60(Ar, +0x18)`, `0x000bc470(Ar, +0x30)`, `0x000bcb60(Ar, +0x48)`,
  `0x000bcd30(Ar, +0x54)` once the stack is followed (`ADD ESP,0x8` after each call leaves the later
  arguments in place) -- which is why Ghidra renders the first call with five arguments and the rest with
  one, and why the previous pass could only call the arrays "two 0x48-byte record arrays".
- **The record whose `+0x34` is a name is the clip.** `0x000bcb60` names each `0x48`-byte element with
  `0x00058850(Ar, elem + 0x34)` -- an inline `FString` read, **not a store**, so the 58-function support
  export saw no writer by construction. `0x000bb2a0(record, out)` copies that `FString` (`+0x34` data,
  `+0x38` count), and both consumers (`0x000b5f10`, `0x000b63f0`) call it over the `+0x48` array: **the
  runtime evaluates clips; `+0x54` is the track array.** A track's `+0x34` is its second group array's base
  (`0x000bc620`, two `0x000bc330` calls at `+0x30` and `+0x3c`), so the two 0x48-byte families are
  separable by that field alone. `0x000b4710` does not take the key block from the record at all -- it is
  in `ECX` from the blend's stack argument (`[ESP + 0x84]` at `0x000b5fe2`), so the caller chooses the track.
- **Corpus:** 17 clip records against 2,231 track records, clips in four of the eight files (carter 4,
  jackson 3, oneill 6, tealc 4) and none in the other four, so a clip-only path leaves `grey.lad` and
  `JAnderson.lad` nothing to evaluate. Instruments: `~/sg1/work/lip11/{out,out2,out3}`,
  `docs/formats/lipsinc-runtime.md`, U4. No boot, no xemu, no artifact change.
- **Next**: the `.map` files, which the writer `0x000b55f0` formats and the dump actually ships -- four of
  them under `System/LIPSincData/Mappings/`, 51 lines each, `[PhonemeToSpeechTargetMapping]`,
  `NumSpeechTargets=15` and 43 `Phoneme=w0..w14` rows. A slot's group name is empty in all 58,006 groups,
  so these text tables are the only remaining candidate for what a slot is called.

### Execution notes (2026-11-09, two record arrays and no name writer)

- **One import of the whole support region answers the name question with a negative.**
  `~/sg1/work/lip10` exports all **58** functions of `0x000b1770`..`0x000b6fc0`, and **no body stores to a
  0x48-byte record's `+0x34`/`+0x38`** -- the only stores to those offsets in the set are stack locals and
  the fade driver's own request field. The records are built outside the region, so the next instrument is
  the loader the factory at `PTR_005bf508` runs (`0x000b9d50`/`0x000bce40`), not another read of these
  bodies.
- **The controller holds two record arrays.** `0x000b2cb0` and `0x000b2ce0` are matched
  bounds-checked getters (`count < 1 || count <= i || i < 0 -> 0`, else `base + i * 0x48`) over
  `+0x48`/`+0x4c` and `+0x54`/`+0x58`: the pose pair `0x000b5f10` blends between, one written by the
  accessor at `+0x40` and one read at `+0x04`/`+0x10`.
- **The key block's write side is in the region too.** `0x000b4200`/`0x000b43e0` are 466-byte twins that
  size a group array for writing, `keys * 0x10 + (namelen - 1) * 2` per 28-byte group -- the 16-byte key
  and the wide name of the same record `0x000bc330` reads -- and `0x000b55f0` writes the `.map` phoneme
  text (`; %s.map`, `LIPSinc Phoneme Mapping File for %s`), a file outside this container. In the exported
  set `0x000b4710` has exactly two callers (`0x000b5f10`, `0x000b63f0`).
- **Next**: the record builder, from the loader side. Instruments: `~/sg1/work/lip10/out`,
  `symbols/xbox_functions.csv`, `docs/formats/lipsinc-runtime.md`, U4. No boot, no xemu, no artifact change.

### Execution notes (2026-11-09, a slot is read by name and a zero is a skip)

- **The consumer addresses a slot by name, not by index.** `0x000b4710` (215 bytes) copies its caller's
  `FString`, resolves it with `0x000b45c0` (329 bytes: a `__wcsicmp` scan of the key block's 28-byte groups,
  count `+0x40`, base `+0x3c`, returning `index*0x1c + base` or 0) and evaluates the match with
  `Engine_LIPSincSupport`, answering `0x4c8848` when nothing matches. Since all 58,006 groups are unnamed,
  the only query this corpus can resolve is the empty name -- a slot number is not an address in the
  runtime, and the name field is the one that would label it.
- **A zero is "no contribution".** `0x000b5f10` (1,244 bytes, the pose-pair blend) enters its sampling path
  only at `alpha == 0` exactly -- the first frame of a fade -- where it copies the destination pose
  indices, calls `ValuesAtTime` once, resets the pose from `pDefBonePose` (`0x000b2700`), then writes each
  record's named curve into `+0x40` and calls pose assembly `0x000b29a0` **only when that value is
  non-zero**; at any other alpha it lerps the three position floats between the pair and writes four
  orientation dwords filled by `0x000b19b0`, with no curve work. `0x000b63f0` (1,411 bytes) is the fade
  state machine: state 1 fade in (duration `[request+0x28]`, alpha = elapsed/duration handed to
  `0x000b5f10`), state 2 play (clock at `+0x20`, `ValuesAtTime` as the bounds check, the same
  accessor-and-assembly loop), state 3 fade out (duration `[request+0x2c]`, then a `+0xec` vtable call and
  every record's `+0x40` zeroed), 0 idle. Both paths treat a negative weight like any other, so the signed
  columns are ordinary weights.
- **The corpus against the gate.** `tools/lad-weights.py` (`make lipsinc-weights`,
  `inventory/lipsinc_weights.csv`, `--check` covering the gate arithmetic and the CSV): **12,080 of 35,031**
  populated slots (34.5%) are non-zero at tick 0 and applied, **22,951 (65.5%)** are exactly zero and
  skipped (median 36.4% per track, p10 9.1, p90 66.7, 3 tracks contributing nothing, per file 6.7% to
  36.2%); at a track's own end only **26** (0.07%) are non-zero. The signed columns are one number and its
  negation: 19, 24 and 25 are populated in 2,230 tracks, **24 == 25 at tick 0 in all 2,230**, **19 == -24
  by value in 2,137** and **exact key for key in the 1,890** whose grids coincide; 19 spans 0..8.378 and
  24/25 -8.378..0, while 20-23 stay within +-2.87.
- **Next**: the binding, which is the last thing between a slot and a name -- the caller that fills a
  record's `+0x34` (the loader that builds the 0x48-byte records). Instruments:
  `tools/lad-weights.py --slots`, `inventory/lipsinc_weights.csv`, `docs/formats/lipsinc-runtime.md`, U4.
  No boot, no xemu.

### Execution notes (2026-11-09, the evaluator holds both ends and a segment is a smoothstep)

- **The per-slot evaluator is `Engine_LIPSincSupport` (`0x000b2230`, 435 bytes)**, called once per slot from
  `ValuesAtTime` at `0x000b2560` with the group record in `ecx`, the one `double` on the stack and the result in
  `st(0)`; `0x000b4710` is the single-slot accessor that calls it directly. **Outside a slot's own extent it
  holds**: before the first key it returns `[keys+8]` and after the last `[keys + count*16 - 8]` with no
  arithmetic in between -- no extrapolation and no clamp to a range -- no keys returns the `0x4c8848` zero and one
  key returns that value everywhere. Between two keys it is a **cubic smoothstep**,
  `v0 + (v1-v0)(3a^2-2a^3)`, built from `3.0` (`0x4ea138`) and `6.0` (`0x4ea130`) with `a = (t-t0)/(t1-t0)`; the
  endpoint identity (`a=0 -> v0`, `a=1 -> v1`) is the check that fixes the reading. A segment is therefore not a
  lerp: at `a = 1/4` the sample is 9.375% of the span inside the lerp, at `3/4` 9.375% outside. `|v| < 2^-51`
  snaps to exactly 0 on the common exit (holds and key hits included), and the segment search is
  `t0 <= time < t1` with a **strict** upper test, so an equal-tick pair is never a segment.
- **The corpus is answered by the hold most of the time.** `tools/lad-sampler.py` (`make lipsinc-sampler`,
  `inventory/lipsinc_sampler.csv`, `--check` covering the sampler and the CSV) puts **57.4%** of the 26 x domain
  rectangle of 2,231 tracks / 35,031 populated slots on the hold path, median track **75.4%** (p10 48.7, p90 82.8,
  max 94.4); **0** tracks have every populated slot covering the domain and **2,111** have no slot starting at
  tick 0; the least-held column is slot 2 at 10% and the most-held is slot 7 at 88%; **0** adjacent pairs
  decrease, **2** share a tick (slot 2, `m07_17_h_carter` @4086 and `m21_09_b_carter` @4022, within 0.002 of each
  other), and no key value is a non-zero magnitude below `2^-51`.
- **Next**: the consumer of the 26 doubles, which is where a signed slot acquires a meaning. Two callers of
  `ValuesAtTime` exist -- `0x000b5f10` (a pose-pair blend: it calls the sampler only for its `alpha == 0` side and
  lerps positions afterwards) and `0x000b63f0` (1,411 bytes) -- and the accessor `0x000b4710` writes one slot into
  an array of `0x48`-byte records through the lookup `0x000b45c0`. Read those four bodies next, then the
  per-column values. Instruments: `tools/lad-sampler.py --track`, `inventory/lipsinc_groups.csv`,
  `docs/formats/lipsinc-runtime.md`, U4. No boot, no xemu.

### Execution notes (2026-11-09, the sampler reads one vector, and no track shares a time axis)

- **`ValuesAtTime` answers "what does the runtime ask", not "what does a slot mean".** `0x000b24f0` (235 bytes,
  `lip7`) gates on one bound — `*(int *)(this + 0x1c)` against its `double time`, logged as
  `m_nEndTime = %i, time = %f`, plus a float64 global floor at `0x4c8848` — and then fills `*(this + 0x34)`
  doubles in a single loop (`out + 8 + i*8`) through one evaluator call per slot, with no slot index and no
  branch. The array it fills is the key-block array: base `+0x30`, count `+0x34`, capacity `+0x38`, stride
  `0x1c`, i.e. the 28-byte group record `FUN_000bc330` reads. So the 7 signed slots are ordinary slots of one
  26-double vector — a code-level negative for "the signed slots are a separate curve type", and a redirection:
  their meaning is in the consumer of the vector.
- **The file's half is a negative with a distribution.** `tools/lad-groups.py --vector` (new mode; `--check`
  still dump-free and now covers `vector_shape()` on hand-built tracks) walks every track: **0 of 2,231** tracks
  share an extent, a start, an end or a key grid across their populated slots; distinct key grids per track
  **3–23, median 10**; inside one track the earliest and latest slot start differ by a median of **696** ticks
  (max 15,133) and the ends by a median of **1,187** (max 17,404), in a corpus spanning −32..8,688. There is no
  shared keyframe table: each curve owns its key list and its extent, and the sampler's one bound is per track.
- **The reader's struct agrees with the file, from the writer's side.** `LIPSincReadAnimFile` (`0x000b8340`, the
  *text* LTF reader) builds exactly the on-disk record while parsing: a 28-byte element (`u32 2`, 12-byte
  `FString`, 12-byte `TArray`) whose keys are `u32 2`, an `i32` at `+4` and a `double` at `+8` — the same 16
  bytes `lad-keys.py` reads — so the container stores the runtime structure and there is no re-encoding on load.
  It also flushes its key array every **26** text pairs (`(i / 2 + 1) % 0x1a == 0`), the same 26 the binary key
  block uses. Recorded, not explained: `160.0`/`220.0` are still untouched by any body read so far.
- **Next**: the evaluator `ValuesAtTime` calls (what it answers for a time outside a curve's own extent — hold,
  clamp or extrapolate), then the code that consumes the 26 doubles, which is where a signed slot acquires a
  meaning. Instruments: `tools/lad-groups.py --vector`, `docs/formats/lipsinc-runtime.md`,
  `docs/formats/lipsinc-lad.md`, U4. No boot, no artifact change.

### Execution notes (2026-11-09, a key group is a named array and the name is empty everywhere)

- **The group's `u8 0` is a name, not a flag.** `FUN_000bc330` -- the reader `FUN_000bc620` calls for a track's
  key block -- walks 26 elements of a **28-byte** record: a `u32` tag, a 12-byte `FString` and a 12-byte
  `TArray` of keys, the latter two through `FUN_00058850` and `FUN_000bb650`. On disk the empty `FString` is the
  single `0` byte the format page called a flag, and the varint after it is the array's count in the same
  encoding every string and count in the file uses, so the reading does not move a byte.
- **The corpus never fills it.** `tools/lad-keys.py` now parses the name and writes a `named` column:
  **58,006 groups over 2,231 tracks, 0 named**, the totals unchanged (263,900 keys, 4,572,880 key bytes, no
  block refused) and `--check` refuses a row with a named group, so the claim is enforced rather than printed.
- **That closes the file-side route to a slot's bone.** The group record is where a curve could be named; it is
  empty in every shipped block, so "which weight slot is which bone" has to come from the runtime or from the
  values, which is where the next read starts.

### Execution notes (2026-11-09, the pose record is a position and a unit quaternion)

- **The three "opaque" dwords are a quaternion's vector part, and the `-1.0f` is its `w`.** `tools/lad-bones.py`
  (`make lipsinc-bones`, `inventory/lipsinc_bones.csv`, `--check` in `make ci`) now reads all seven floats of a
  record: the last four have `|q| = 1` within `5.5e-7` in **all 3,200** records and `w` is never positive, so a
  record is a position plus a rotation in the negated convention. The rest pose's "garbage" words
  `6.3e-30, -0, 3.6e-30` are that quaternion's `x, y, z` -- denormal-small, not zero, which is why four earlier
  readings took them for integers.
- **`FUN_000b1bf0` cannot settle the split, and that is the finding.** It calls `FUN_00037110` once and
  `FUN_00037170` seven times, but the two are **byte-identical** (48 bytes: a 4-byte read through the archive's
  `Serialize` vtable slot plus a running byte counter), so the image says "one tag and seven 4-byte scalars" and
  nothing more; the 3 + 4 grouping is measured from the data, which is the stronger witness here.
- **The angle is twice what the last float alone suggested.** `w = -cos(theta/2)`, so `2*atan2(|v|, -w)` puts
  3,124 of 3,200 records on a **whole-degree** grid (97.6%), 522 turn at all, the maximum is `77.0` degrees and
  the alphabet is 42 values; 506 of the 522 turn about `±X`, the other 16 about `(0.008, 0.239, 0.971)`. The
  earlier half-degree grid was this one read with `acos`, which reports half a rotation and loses small angles
  to float resolution near `w = -1`.
- **The record is closed; the file's tag and constants are not.** `FUN_000bc620` reads a track's `160.0`/`220.0`
  into two consecutive float fields without testing them, and each of the four record readers stores its leading
  `u32 2` into the record's first dword without comparing it -- so `2` is a tag the parser carries, and neither
  question is answered from the serialiser side. **Next**: the 7 signed slots' meaning, and a caller that reads
  those two float fields.

### Execution notes (2026-11-09, the 85 refusals are rows now, and two families are most of them)

- **The bytecode walk's residue is measured per payload, not described.** `tools/uscript-refusals.py`
  (`make opcode-refusals`, `inventory/opcode_refusals.csv`, `--check` in `make ci`) writes one row per refusal:
  package, class, function, script length, the offset the walk stopped at, the opcode there, the case, and the
  walker's sentence. **11,602 walk clean and 85 refuse**, the same counts the prose carried, now with rows —
  46 `bad-token`, 20 `parameter-list`, 16 `label-table`, 2 `operand-bytes`, 1 `string`, over nine packages
  (`StargateMenus.u` 27, `Engine.u` 23, `StargateGame.u` 11, `UWindow.u` 11).
- **The refusals sit late in their scripts, and the tail is not the cause.** The median refusal is at 71% of its
  own script, 40 of 85 stop inside the last 16 bytes and none inside the last 2 — but `--tail-cut` re-walks each
  one with its last 3, 7 and 11 bytes removed and rescues **0, 0 and 4** (the four whose failing token was the cut
  itself). So the offsets are real token boundaries, not a range that runs one token long.
- **Two families are 16 of the 85.** All 13 label-table refusals with exactly **11 bytes left** are
  `StargateMenus.u`'s `InternalOnXControllerEvent` — one function, one token shape, one fix — and that function is
  15 of the 85 on its own. `InternalOnChange` is three 11-byte scripts stopping at offset 5, and the failing byte
  differs between them (`0x42`, `0x49`, `0x54`), which is a bad operand width in the token *before* offset 5 rather
  than three opcode gaps.
- **The rest is a per-function tail**: 45 distinct function names, no offset repeated more than three times, and
  the `bad-token` rows spread over 37 opcodes. Worth individual reads, not another table-wide correction.

### Execution notes (2026-11-09, a `.lad` channel is a bone by name, and the reader was the wrong two functions)

- **The container's other half is the engine's, and it is read.** `LIPSincInitLevel` (`0xba750`) finds `*.lad`
  itself (`FUN_000192a0(L"*.lad")`) and matches each name against the level's controller-prefix list with
  `_wcsstr`; `LIPSincLoadDBFromDisk` (`0xb9e90`) strips the directory and extension to a **stem** (the controller's
  name) and parses the file through an object of the class at `PTR_005bf508` — `FUN_000bce40` is that parse, a
  chain of five stages (`0x35b60`, `0xbc470`, `0xbcb60`, `0xbcd30`, `0x58850`) writing at `+0x14`..`+0x7c`.
- **Channel → bone is a name comparison, and it is the only place the two meet.** `LIPSincLinkBones` (`0x225cd0`)
  finds the controller by `_wcscmp` over `count * 0x94` records (`0xb5250`), then `FUN_000b4930` compares each
  channel name (`FUN_000b48e0`, 0xc-byte name entries) with the mesh's bone names (0x40-byte entries at `+0x1dc`,
  count `+0x1e0`) using **`_wcsicmp`**, and writes the **mesh bone index** into a dword array at controller `+0x10`,
  one entry per channel. A channel that matches nothing goes to a caller list and the controller is
  `NOT VALID CONTROLLER!`; `is valid controller` is logged only when every channel matched. After that the pipeline
  is index/id-based (`FUN_000b3880` searches the mesh's id array and indexes 0x20-byte transforms).
- **The names exist in the data, in exactly five skeletons.** `tools/lad-bone-owners.py` (`make
  lipsinc-bone-owners`, `inventory/lipsinc_bone_owners.csv`, `--check` in `make ci`) scans the name table of every
  package in the dump (482 parse) and finds **all 21 channel names declared by `Grey_anim.ukx`,
  `SGC_soldiers_anim.ukx`, `jaffa_anim.ukx`, `sg1_anim.ukx` and `tokra_anim.ukx`, and by nothing else**; the two
  tongue bones are in all five skeletons but in only four `.lad` packs.
- **The witness the format notes named is the wrong one.** `LIPSincReadAnimFile` (`0x8340`) and
  `LIPSincReadControllerFile` (`0x7260`) tokenize wide strings on spaces and convert with `__wtof`/`__wtol` — they
  read the middleware's **text** LTF/LBP files (`Read Track %s`, `not a valid LTF file!`, `obsolete .LBP file!`),
  not the binary container. The LTF track loop does flush every **26** (time, value) pairs, the same 26 the binary
  key block uses for its group count — recorded, not explained.

### Execution notes (2026-11-09, the channels are an ordered bone list and the pose's last float is an angle)

- **The only ordered bone list in the format is the channel table, and it is stable.** `tools/lad-bones.py`
  (`make lipsinc-bones`, `inventory/lipsinc_bones.csv`, `--check` in `make ci`) reads the table as an index → name
  list over all eight files (160 rows). The first **eighteen** names are identical everywhere; the 21-channel shapes
  then carry `BN-Tongue-back`, `BN-Tongue-front`, `BN-Head` and the 19-channel packs carry `BN-Head` alone — so the
  controller packs are the speech list minus two bones, in the same relative order. `--check` refuses any other tail.
- **A clip's bone list is an explicit order.** Sixteen of seventeen clip lists follow the table; `jackson_frown` in
  `Controllers/sg1_crew_jackson.lad` lists `BN-Head` first (`18, 4, 5, 6, 7`). Its frames carry one record per listed
  bone in that order, so a clip says what a record is by *position in its own list*, and nothing in the file stores a
  slot number beside a name.
- **The pose record's eighth word is a rotation angle.** All 3,200 records have their last float in `[-1, 0]`, and
  `-acos` of it snaps to a **half-degree grid** in 95.0-99.0% of records per file; 2,678 of 3,200 are exactly 0° and
  the corpus maximum is 38.5°. So the `-1.0` that two earlier pages read as a terminator and then as "the pose weight"
  is the zero-angle case; the record's real payload is a position (three floats that move, up to 3.4 units over the
  twenty frames) plus this angle.
- **The negative is the slot-to-bone link.** The 21-channel clips carry the same 26-group key blocks as the 19-channel
  packs, so "19 weight slots, 19 channel names" is a coincidence of the controller packs and not a schema: counting
  cannot name a slot's bone. That leaves the reader (`LIPSincReadAnimFile` `0x000b8340`, `LIPSincReadControllerFile`
  `0x000b7260`) as the only witness, which is the next task rather than another file-side guess.
- **Still unread, and now bounded**: the three words between the position and the angle (`0x0f000004`, `0x9e12217c`
  and denormals in the samples, constant per bone across frames in 8 of 19 bones), which no reading of them as `f32`
  explains.

### Execution notes (2026-11-09, the track table declares its own count in the key counts' varint)

- **The bytes before the first track header are the table's count, not padding.** `tools/lad-tracks.py` now decodes
  them in the *same* 7-bit varint `tools/lad-keys.py` uses (bit `0x40` in the first byte means a second follows and
  contributes `<< 6`): `60 09` is `0x20 + (9 << 6)` = **608** in `Controllers/sg1_crew_carter.lad`, `52 0c` = 786,
  `65 07` = 485, `58 05` = 344, and `00 03` = 3 in `Controllers/grey.lad` and `JAnderson.lad` while `00 02` = 2 in
  `sg1_crew_carter.lad`. **Seven files with a table, seven exact counts** — the third appearance of this varint in the
  format, after a group's key count and the two bytes a 69-key group already needed.
- **It verifies the walk instead of steering it.** The count ends where the first record begins, so it is read
  backwards from a record the marker-and-signature scan already found; `inventory/lipsinc_tracks.csv` gains `gap_hex`
  and `declared`, and `--check` refuses a row whose `declared` disagrees with its `tracks`. A count read forwards
  would have made the walk trust a number before testing it — this way a disagreement is a finding. The leading
  `0x00` in four of the seven is the previous section's last byte, not part of the count.
- **The negative stays a negative.** `sg1_crew_jackson.lad` has no track table and its six bytes
  (`000002000000`) are not a count of zero; they remain recorded as that file's residue. `JAnderson.lad` listing
  `AlkeshBomber.Beware` twice is still the one unexplained item in the table.
- **Next**: read `LIPSincReadAnimFile`'s loop at `0x000b8340` with the count and the group codec in hand — it should
  now be a short loop (a count, then per-record names and a group walk) rather than a parser to guess.

### Execution notes (2026-11-09, the slots are 19 weights and 7 signed curves, and six of them repeat)

- **PLAN 5.5's open half was a question about values, and the values answer it on their own.** `tools/lad-groups.py`
  (`make lipsinc-groups`, `inventory/lipsinc_groups.csv`, `--check` in `make ci`) measures every group against every
  other group in the same track — 208 file × slot rows over the 58,006 groups. **Slots 0–18 never carry a negative
  value in any row**, and their widest is `1.33783`; **slots 19–25 are signed**, −9.45942 to 9.45942. The split is at
  exactly the nineteenth slot, which is also the channel count of the four `Controllers/` packs — recorded, not
  explained. That is the first statement the format has yielded about *what a value is*: a weight for 19 of the 26
  slots, a signed offset for the other 7.
- **Six slots are redundant, so the block's information is at most 20 curves.** The test is exact: two slots are
  compared only when they carry the same time sequence, and then every `f64` must be bitwise equal or the exact
  negation — a near miss is reported as `other` and would disqualify the claim. Result: 24 = 25 in 2,230 of 2,231
  tracks, 17 = 18 in 1,845, 22 = 23 in 883, 15 = 16 in 2, and 19 = −24 = −25 in 1,890 while 21 = −22 = −23 in 193.
  The counts fall short of 2,231 because a pair says nothing where the grids differ (19 and 24 meet in 1,890 of the
  2,230 tracks where both are populated), which is reported as silence rather than as "different". Slots 19, 24 and
  25 are populated in 2,230 tracks, 20 and 21 in 598 and 596.
- **The negative is the header constants.** `--headers` re-scans every `u32 2` marker under a *relaxed* test (a name
  pair, a `u32 1`, two `f32`, any count byte) and finds 2,231 track-like records with exactly one tuple
  `(160.0, 220.0, 26)` and **zero** the strict reader refuses — so the earlier claim that they are invariant is not an
  artefact of the matcher, and no reading of them as per-clip data (a duration, a sample rate, a range) survives.
- `--check` holds the CSV to the split (a slot below 19 may never carry a negative value, a slot from 19 up must leave
  the weight range somewhere), to `lipsinc_keys.csv`'s key totals per file, to the relation symmetry, and to a
  hand-built three-slot case for the relation test itself. **Next**: index the 19 weight slots against an ordered
  bone list — the clip sections' bone subsets are the only one in the file — and read the 7 signed slots' values
  against the pose records' eight floats.

### Execution notes (2026-11-09, the .lad body is three parts and the mass is in the key blocks)

- **PLAN 5.5's "second section" is two parts, and neither is padding.** `tools/lad-tracks.py` (`make lipsinc-tracks`,
  `inventory/lipsinc_tracks.csv`, `--check` in `make ci`) walks on from the twentieth pose frame. The four 19-channel
  packs carry a **clip section** first — a count, then clips of `u32 2` + a bone subset + 20 frames + a **clip name**
  (`carter_happy`, `carter_worried`, `carter_yelling`, `carter_frown`; `oneill_squint`; `tealc_perplexed`) — 17 clips in
  all, every one 20 frames, bone lists of 19, 11, 9, 5 or 3 against a 19-name channel table, so a clip is a partial
  pose, not a copy of the pose frames. Then **2,231 track records** named `<Map>.<Animation>`
  (`AlkeshBomber.ca_firing_ge_s_02`), each with an invariant ten-field header (`u32 2`, both names, `u32 1`, `160.0`,
  `220.0`, key count **26**) and a key block behind it.
- **The number that changes the estimate: 4,572,880 bytes of key data, 4.36 MiB of the 6.5 MiB corpus, all of it inside
  a named track.** The packs are 89-94% key bytes rather than 96-99% unexplained, and the 21-channel clips hold two or
  three tracks each because a speech clip is one line, not a character's animation set. Nothing in the body is left
  unaccounted: `gap_bytes` is 2 everywhere except the 6 bytes of `sg1_crew_jackson.lad`, which has no track table.
- **The negative is the key count.** 26 keys in a 269-byte block and 26 in a 19,568-byte one, with the `u32 2` marker
  recurring 33-1,247 times at 11-15 bytes per marker: the entries are variable-length, so no stride can be assumed and
  the tool stops at each header rather than guessing past it. Also recorded, not explained: the two bytes before the
  first track header (`00 03`, `60 09`), and `JAnderson.lad` listing `AlkeshBomber.Beware` twice — a name is not a
  unique key. **The next measurement is the key block's own encoding**, from `LIPSincReadAnimFile`'s loop at
  `0x000b8340` (already located, 4,977 bytes) or a value check against the `160.0`-`220.0` time range 26 keys imply.

### Execution notes (2026-11-09, the 37 was a frame header, and the .lad body has two sections)

- **PLAN 5.5's `.lad` payload is a frame stream, and the "five bytes past a record" question is closed.** The census
  that produced `37 = 32 + 5` had no instrument that could read the five bytes; `tools/lad-frames.py` walks the body
  and reads them as a **frame header** -- `u32 2` plus a `u8` equal to the channel count -- followed by exactly
  `channel_count` records of 32 bytes, each opening with the same `u32 2`. Measured on all eight files: **20 frames
  each, 3,200 records, records-per-frame exactly the channel count (19 or 21), zero short or empty frames**, so the
  header is a per-frame count and not a record's extra field. Two corrections fall out and are now in
  `docs/formats/lipsinc-lad.md`: the "10-byte prelude (532, 0, 0x21500, 0)" was this header read two bytes late -- the
  body opens with one byte, `0x14` = 20, the frame count in every file -- and the "bind pose block, one record per
  channel" is simply frame 0 of 20.
- **The negative is the largest number in the run: six of the eight files do not close.** `sg1_crew_jackson.lad` leaves
  6 bytes after its twentieth frame, but the 1.3 MB and 1.8 MB `Controllers/` packs leave **96-99% of their bodies
  unread** -- and where the twentieth frame ends the body starts a *second section that repeats the channel table*
  (`04 02 00 00 00 13` then `0b "BN-Blink_L"` at `Controllers/sg1_crew_carter.lad` 12,613) -- which is what
  `LIPSincReadControllerFile` filling a pose array and a per-bone `ValuesAtTime` reader already implied: a table of
  20 pose frames, then per-bone key lists. **The next measurement is that section's
  own record shape**, walked the same way.
- Also measured, because the doc claimed otherwise: `-1.0f` is present in 84% of one file's records (352 of 420), so it
  is the pose weight and not a per-record terminator, and the twelve bytes in front of it take 125-145 distinct values
  per file, so they are data rather than padding. `make lipsinc-frames` regenerates the CSV and `--check` keeps its
  invariants (no short frame, no empty frame, a frame never wider than the channel count, and every body byte either
  consumed or counted as unread).

### Execution notes (2026-11-09, the master's block is seven pointers, and the text is not one of them)

- **The census the last run built over-reported by 20 rows, and the reason was in its own input.** `hudq1`'s
  `MASTER` lines are 27; only **seven** name a class (`+0x08` an `ObjectProperty` `CriticalBreathIn`, `+0x18` a
  `Package`, `+0x24` the `InteractionMaster` class object, `+0x28`..`+0x34` the `XboxClient`/`GUIController`/
  `Console`/`UIXInteraction` defaults). The other 20 are the block's own UTF-16 (`0x007c0020` is `" |"`) and one
  dword inside the `Console` class table (`0x0050cc68`). `master_slots()` now refuses a target that names no class,
  returns the count, and prints `MASTER ...: 7 object pointer(s) ... 20 dword(s) that name no class (the block's own
  text, not slots)`.
- **The outer test does not separate this block's objects.** All seven read a `Package` outer, the block's own
  `+0x18` included, and the test also fires on a class object (a class object's outer is a package too), so
  `outer a Package (the class default)` was describing the block and not the object beside it. The census now prints
  the outer's **class name** with the verdict.
- **`bTyping 3` is narrowed, not explained.** `class-defaults.py $SG1_DUMP/System/Engine.u --class Console` reads
  Console's five defaults and no bool of its own (the single bool, `bRequiresTick True`, is `Interaction`'s), so a
  `Console` default's `bTyping` byte must be 0. The receipt therefore reads the **byte** and prints `bTyping 0x03
  (not a bool: bit(s) 0,1 set)`; the census row `36148,0x02344b00,Console,Console` cannot decide default-vs-live
  because a viewport-created console is named `Console` too.
- **Next**: one boot with `--console-client`, `bTyping` read before `Tab`, after `Tab`, after the first key and after
  `ret` -- `0 -> 2|3 -> 1 -> 0` makes the byte packed (`bIgnoreKeys` is declared next to `bTyping`), `0 -> 1` keeps
  it a bool and moves the contradiction to `+0x18`.
- **Instruments**: `tools/xemu/hud-text.py` (`master_slots` screening and the `screened` count, `typing_text`,
  `outer_text`, two selftest cases), `docs/HUD_TEXT.md`, `docs/UNKNOWNS.md` U15. No boot, no artifact, no new file.

### Execution notes (2026-11-09, the frame gate stops being the verdict, and the census is finally asked)

- **The question had an answer before the run: xemu's QMP has no `screendump`, measured.** `tools/xemu/qmp-caps.py
  --launch` reports **228 commands** and `screendump` refused for a bare `filename`, for QEMU 5's `format: png` and
  for a `device` argument (`CommandNotFound`, three shapes). So "read the framebuffer over QMP" is closed, and the
  X window is the only frame source this build has -- which makes the previous note's next step a negative, not a
  route. The tool is the deliverable: `--sock` for a live run, `--launch` for a 15 s probe, `--has NAME` as an exit
  status, and a `--selftest` that answers like this build in `make ci`.
- **A shot that the desktop will not give is now a reading, not a dead run.** `drive.py --soft-shots` reports
  `UNCAPTURED` (no frame produced) or `UNIFORM` (one colour, `convert -format %k` = 1) and carries on; captured
  frames are compared exactly as before. `hud-say.sh` passes it, `missions.sh` does not -- there the frame is the
  pass criterion. The outcome is decided by `shot_outcome()`, which has six selftest cases.
- **`hudq1` asked the census that `hudsay7` died before.** One boot, `--console-client=0x01fb9bc0`, `M00` at index
  10: the level row and `+0x1008 -> GameHud` resolved, all four commands typed (`041`/`042` shots), the receipt ran
  (365 page reads, 227.0 s, inside the 360 s linger), and the run ended `FAIL: 3 shot step(s) did not repaint`
  instead of exiting 3. The frames behave exactly as the last two boots said: `02-mission` and `03-dismissed` read
  **0 px** (a real, frozen picture -- not one colour, so `UNIFORM` does not catch it), then `05-precommands` repaints
  **94,415 px** and `06-dismissed` **40,250 px** once the level settles, and `042-addnotification` **1,383 px**.
- **The census answers with the class default, three ways.** The client's `InteractionMaster` (`0x02406720`) holds
  `+0x30 -> 0x02344b00`, class `Console`, **outer `Package`**: 1 slot on the `Console` table and **0 live**, so
  `consoles_from_client()` falls through. The level HUD's `+0x398` holds `0x00050971`, not an object address. The
  whole-array walk over 48,646 objects finds that same default and nothing else.
- **The negative is a contradiction, and it is the next measurement.** That object reads `bTyping 3` -- a field the
  class default cannot hold, since a CDO carries the compiled default (`bTyping 0` on every earlier read) -- beside
  `HistoryTop 0` and an empty `TypedStr`. So on `0x02344b00` at least one of `+0x114` (the byte) or `+0x18` (the
  outer) is not the field this build uses, and the receipt's default test is what turned that into a visible
  disagreement instead of a `NOT IN HISTORY` verdict. **Next**: read `0x02344b00`'s `+0x18`/`+0x114` against the
  same object's record in the `ghud1` census (index 36,148, which names it `Console`), then the class's own
  `UStruct::Serialize` if they still disagree -- one dump, no boot.
- **The `MASTER` census over-reports.** It screens every aligned dword in the block, so a UTF-16 fragment
  (`0x007c0020`, `0x00410020`) prints as `class ? name ?` beside the six real slots (`XboxClient`, `GUIController`,
  `Console`, `UIXInteraction`, `ObjectProperty CriticalBreathIn`, the `InteractionMaster` class). A candidate whose
  target names no class is not an object, and the census should say so -- the six it can name are the block's
  slots.

### Execution notes (2026-11-09, the census names the master's slots, and the frames go uniform at the map select)

- **The instrument this run needed is in place and the boot that would have asked it never reached the question.**
  `tools/xemu/hud-text.py`'s receipt now censuses the client's `InteractionMaster` block instead of returning its
  first match: `master_slots()` prints `offset -> target, class, name` per object pointer, marks a target whose
  `Outer` is a `Package` and one that opens with the `Console` class table, and `consoles_from_client()` offers only
  **non-default** targets as a console. So `hudsay6`'s shape -- the default at `+0x30`, a live console somewhere else
  -- answers with the live one, and a block holding only the default prints what it does hold and falls back to the
  array walk. Two selftest cases build both shapes.
- **`hudsay7` loaded `M00` and lost every frame from the map select on.** Name table 40,427 at 281.8 s; row
  `0x08842800` (object 47,885 of 48,494, outer `M00`); `+0x1008 -> GameHud 0x09382100`; six intro turns and
  `New Objective : Open the door for the doctor` at 480.6 s off `DialogueWindow`. Meanwhile `02-mission.png` and
  `03-dismissed.png` are a uniform `(13,13,13)` (black 0.9965, p99 13, 0 differing px between them), and
  `05-precommands.png` could not be captured: `drive.py` exited 3 at 07:22 and killed the guest, so the receipt
  never ran and the boot has no `CONSOLE`/`MASTER`/`RECEIPT` line. The marker question is exactly where `hudsay6`
  left it.
- **The blank frame is not window selection.** `hudsay8` ran a 20 s `xdotool search --onlyvisible --class xemu`
  census beside the boot and recorded **one** window throughout (`67108917`, 1280x960 at `+2560+232`), with the same
  uniform frames (3,824 bytes). So the one X pixmap stops receiving frames at the level's video-mode change; the
  frame source, not the id, is what fails. `drive.py` now takes the largest visible xemu window and tries every
  candidate in `capture()`, which is a hardening rather than this fix.
- **Next**: a frame source that does not go through XWayland -- QMP `screendump` on the socket the run already
  opens -- or a sequence with no post-load shots, so the commands are typed and the census is read. Then
  `IN HISTORY` / `NOT IN HISTORY` is the submit verdict; a block holding only the default means the live console is
  outside `GObjObjects` and the read moves to the master's other slots.
- **Instruments**: `tools/xemu/hud-text.py` (`master_slots`, `consoles_from_client`, the `MASTER` lines, two selftest
  cases), `tools/xemu/drive.py` (`windows`, `find_window`, `capture`), `docs/HUD_TEXT.md`, `docs/UNKNOWNS.md` U15. No
  artifact and no new file. Two boots were spent; no xemu, HDD copy or pad was left behind.

### Execution notes (2026-11-09, the client route reaches a `Console` -- the class default)

- **`hudsay6` answered half the receipt's question: the route works, and the object it reaches is the class
  default.** `M00` loaded (name table 40,427 at 281.8 s), the level's row `0x087c2800` (object 47,885 of 48,494,
  outer `M00`) held `+0x1008 -> GameHud 0x09322100`, six box changes came off it -- the intro's turns at counts 14,
  35, 33, 50, 56 and 53 through 465.7 s, then the game's own `New Objective : Open the door for the doctor` at
  480.5 s -- and the post-resolve `pad=a` repainted **42,083 px (3.42%)**, so the panel was dismissed and the boot
  was live in every part the markers needed.
- **The client route works mechanically and costs 7 page reads**: `--console-client=0x01fb9bc0` -> the client's
  `InteractionMaster` `0x02406720` -> `+0x30` -> a `Console` at **`0x02344b00`**, `HistoryTop 0`, `bTyping 0`,
  `TypedStr` empty, 0 populated slots, in 4.3 s with **0** array walks. Both markers printed `NOT IN HISTORY`.
- **`0x02344b00` is the address `docs/XEMU.md` records as the `Console` class default** (measured twice with the
  prompt open), and a default's fields are the shipped `defaultproperties` -- so `NOT IN HISTORY` is true of the
  default and says nothing about the keys. The route's class screen cannot separate them, because the default is of
  that class.
- **The frames cannot settle it either**, which is why the receipt exists: `06-dismissed -> 041-say-sg1txt1`
  differs by **314 px** (0.03%) and `console-line.py` reads **0** cyan px on both marker shots -- but each marker
  shot is 0.7 s *after* `ret`, and a submit clears `TypedStr` and closes the console, so the frame after a successful
  submit is blank too.
- **The receipt now refuses a class default**: `is_class_default()` reads `UObject::Outer` (`+0x18`) and tests the
  outer's own first dword against the `Package` class table from the census (`0x004de088`). The client route's
  default prints that its outer is a `Package` and falls through to the array walk instead of answering, the
  first-sight `CONSOLE` line prints the outer, and a verdict whose every object is a default prints `UNMEASURED`.
  The selftest builds the shape (an object whose outer is `Package`-classed) and asserts the refusal beside the
  live shape it already asserts.
- **Instruments**: `tools/xemu/hud-text.py` (`class_table`, `is_class_default`, the client-route fallback, the outer
  on the first-sight line, the default-object verdict, one selftest case), `docs/HUD_TEXT.md`, `docs/UNKNOWNS.md`
  U15. No artifact and no new file.
- **Next**: one boot with the fixed instrument. A second `Console` object with a non-`Package` outer makes
  `RECEIPT ... IN HISTORY` / `NOT IN HISTORY` the submit verdict at last; only the default means the live console is
  outside `GObjObjects` and the read has to move to the master's other slots -- the one that answered names the
  default, so the field may hold a class reference rather than the instance.

### Execution notes (2026-11-09, U15's guard hypothesis is retired by the scripts' own call sites)

- **The question U15 carried into this run -- "which guard refuses the message" -- is answerable from the disc, and
  the answer is "none of them".** `tools/hud-messages.py --sink-callers` walks all **2,317** classes of the extracted
  `ScriptText` and counts the bodies that call each message sink. The chain is closed and script-driven:
  `GameHud.SetNotifications` (`GameHud:1034`) has exactly **one** call site -- `NotificationManager.DisplayMessages`
  (`NotificationManager:40`) -- and that has exactly **one**, `StargateHUD.DisplayMessages` (`StargateHUD:1316`), which
  is the override the engine dispatches because `Hud.PostRender` (`Hud:299`) calls `DisplayMessages(Canvas)`.
  `GameHud.DisplayTextBox` (`GameHud:900`) writes `DialogueWindow`, `SetNotifications` writes `NotificationWindow`,
  `SetInteractionText` writes `InteractionWindow`, one call site each.
- **The guards that do exist are three and none of them drops a non-empty string**: `GH == None` (the HUD link),
  `DeltaTime <= 0.0` (the manager's own clock), and the head-of-queue `MessageLife <= 0.0` test; an entry is evicted
  after `NotificationLifeTime`, which the disc sets to **4.0 s** (`System/tweak/Misc/StargateHUD.ini:216`).
  `SetNotifications` substitutes `NO NOTIFICATION TEXT SET!!!!` for an *empty* text, and draws with duration **0** --
  so a delivered notification is a short queue entry drawn once, and a frame taken later can miss it while the receipt
  would not.
- **Both windows were live on the level the markers were typed into.** `hudsay3`'s `NotificationWindow` held the game's
  own `New Objective : Complete fitness Examination` (count 45) and `New Objective : Open the door for the doctor` at
  479.3 s; its `DialogueWindow` held the intro's turns (14, 35, 33, 50, 56, 53). So `NotificationManager` ->
  `SetNotifications` -> `NotificationWindow` and `SetSubtitle` -> `DisplayTextBox` -> `DialogueWindow` both ran on that
  HUD during that boot, and the two sinks stop being candidates for the silent markers.
- **The console edge is the one left, and it differs by command**: `addnotification` needs `StargateHUD`'s
  `exec function AddNotification` (`StargateHUD:277`) -> `DisplayNotification` -> `StargatePlayer.AddNotification` ->
  `NotificationManager.AddNotification` (9 call sites in the tree); `say` is engine-side,
  `PlayerController.TeamMessage` -> `myHUD.Message` -> `SetSubtitle` (10 call sites).
- **Instruments**: `tools/hud-messages.py` (`--sink-callers`; the selftest now also asserts that a declaration is not a
  call site, that a qualified call is one, and that a commented-out call is not), `docs/HUD_TEXT.md`,
  `docs/UNKNOWNS.md` U15. No artifact and no new file.
- **Next**: the receipt, unchanged -- one `hud-say.sh` run with `--console-client=0x01fb9bc0` or the fixed walk. Its
  `IN HISTORY` / `NOT IN HISTORY` decides between "the exec edge never ran" and "the string was queued and the box was
  read after the 4 s queue moved on".

### Execution notes (2026-11-09, the walk screened a class pointer with a class table)

- **`hudsay3` finished the boot the last run could not wait for, and every console route missed on it.** `M00`
  loaded, the level's row `0x08822800` resolved (object 47,885 of 48,494, outer `M00`) with
  `+0x1008 -> GameHud 0x092e2100`, and seven box changes came off it -- the intro's turns (counts 14, 35, 33, 50,
  56, 53) and then the game's own `New Objective : Open the door for the doctor` at 479.3 s -- so the sink read is
  live. `05-precommands`/`06-dismissed` repainted 42,959 / 32,704 px, so this boot's dismissal landed (`hudsay2`:
  0 / 0). Then: the row's `InteractionMaster` (`+0xc0`) read **null**, the level HUD's `PlayerConsole` (`+0x398`)
  read `0x00050967` (a number, not an object address), and a whole-array walk of **48,646** objects read **zero**
  `Console` objects -- 1 walk, 347 page reads, 215.1 s -- leaving both markers `UNMEASURED`.
- **The walk's zero is the instrument's.** It screened `UObject::Class` (`+0x24`) against `0x0050cb80`, the
  `Console` **class table** in `.rdata` (`inventory/xbe_class_table_names.csv`, 1088 bytes). A class table is the
  object's **first** dword -- `hud-link.py` measured the typed-into `Console` opening `00 50 cb 80 ...`, which is
  the screen `inventory/hud_console_link.csv` rests on -- while `+0x24` holds the `UClass` *object*
  (`ghud1` census: `275,0x00d48a00,Class,Console`, instance `36148,0x02344b00,Console,Console`; `names.py --verify`
  measured `+0x24` as a `UClass` pointer). A table address therefore cannot sit at `+0x24`, and the walk could
  match nothing: the selftest had written the table to `+0x24` as well, so the test agreed with the bug.
  `sink.rows_holding` compares both dwords in one pass (pages are cached, so the second compare is free), the
  selftest now writes the measured shape (table at `+0x00`, `UClass` at `+0x24`) and asserts the old route's `[]`
  beside the new route's hit.
- **`+0xc0` is `Client`'s slot, not the level row's.** It was measured on the `XboxClient` object `0x01fb9bc0`
  (`plr1`), and `ghud1`'s census names the same address on a second level boot -- two boots, same object address --
  but a `StargatePlayer` row holds null there (`hudsay3`), which is a fact about the offset on that class. The route
  is now `console_from_client` behind `--console-client ADDR` (`hud-say.sh --console-client=ADDR`), and a level row
  is not screened at all: the receipt prints that the slot is `Client`'s rather than reporting a null master as a
  console that never opened.
- **Instruments**: `tools/xemu/sink-slots.py` (`rows_holding`; `walk` takes an offset tuple; both routes asserted in
  `--selftest`), `tools/xemu/hud-text.py` (`console_from_client`, `--console-client`, the row caveat,
  `rows_holding` in the receipt, two new selftest cases), `tools/xemu/hud-say.sh` (pass-through), `docs/HUD_TEXT.md`,
  `docs/UNKNOWNS.md` U15. No artifact and no new file.
- **Next**: one `hud-say.sh` run with `--console-client=0x01fb9bc0` or the fixed walk. Its
  `RECEIPT ... IN HISTORY` / `NOT IN HISTORY` is the submit verdict four boots have been circling; if the submits
  ran, the remaining question is the `SetSubtitle`/`SetNotifications` guard.

### Execution notes (2026-11-09, the HUD's field is not the console on a live level)

- **The one-read route the last note expected does not hold on a level's own HUD** (measured 2026-11-09, `hudsay2`,
  stock disc, `M00`, two markers). The boot resolved the level's row `0x08842800` (object 47,885 of 48,494, outer
  `M00`) at 360 s and `+0x1008 -> GameHud 0x09382100`, and the boxes read the intro's turns and the game's own
  objective banner (counts 14/35/33/50/56/53, then 45) -- the sink read works. The receipt then read the HUD's
  `PlayerConsole` (`+0x398`) as **`0x00050975`**, which is not an object address, so the shortcut fell back:
  `hud-link.py`'s `+0x398 -> console` was measured on `findtext4`'s HUD, and this level HUD carries a number there.
  The field is therefore a *candidate* route that has to be screened by a fallback, not an assumption.
- **The fallback walk did not fit the linger either**: `the stub stopped answering at page 0x012e0000: 0x07730000,
  which read at the start, no longer does -- the guest is gone`, i.e. the 347-page walk was still reading when
  `drive.py`'s 180 s linger expired, and both markers printed `UNREAD` with no `History` read at all. `hud-say.sh`
  now lingers `300 + 30/command` s for it, which is the walk plus one more typed command.
- **The marker frames repeated the panel negative, in the other direction**: `05-precommands`/`06-dismissed` both
  read 0 px on this boot against 40,532/34,096 on `hudm1`, and both command shots read 0 px -- so "the post-resolve
  `pad=a` lands" is not deterministic, and a frame cannot say whether the keys arrived. The receipt is still the
  only instrument for that question, which is why the next boot is worth its four minutes.
- **The route that should work is one hop from the row, not from the HUD**: a viewport-created console is
  registered as an interaction, so the row never names it -- but `PlayerController.InteractionMaster` at `+0xc0`
  was measured at `0x02406720` on `plr3`, whose own fields name `XboxClient`, `GUIController`, `Console` and
  `UIXInteraction`. `console_from_player` now screens the master's first `0x100` bytes for a pointer that opens
  with the `Console` class table: at most 66 reads, no dependence on the console's array index, tried before the
  HUD field, and both fall back to the walk.
- **Instruments**: `tools/xemu/hud-text.py` (`console_from_player`, `receipt(..., row=)`, three new selftest cases:
  one hop out, a master with no console, a null master), `tools/xemu/hud-say.sh` (linger), `docs/HUD_TEXT.md`.
  No artifact and no new file.

### Execution notes (2026-11-09, the console is at index 36,148, and the HUD's own field names it)

- **The window was never going to contain it** (measured 2026-11-09, no boot): a saved census of a loaded level
  (`xemu/ghud1/cls.csv`, 47,742 named objects) holds exactly one `Console` instance, at **index 36,148 of 48,646**,
  and it is the object (`0x02344b00`) that `inventory/hud_console_link.csv` read out of the live HUD's own field in
  `findtext4`. A 2,000-entry tail window and a 2,000-entry head window are both outside it, so the first receipt's
  `NOT IN HISTORY` was a fact about its search, which is what its `UNMEASURED` wording already said.
- **The cheap route is the HUD's `PlayerConsole`** (`Engine/HUD.uc:27`, at `+0x398`): one read against 347 page
  reads for the whole array, and the pointer is screened by class (`+0x24`, or the object's first dword) so a null
  or a stale value prints a reason instead of reading the wrong object. `--receipt-tail` (default 0, the whole
  array) keeps the walk as the fallback and `--receipt-walk` forces it -- the control for the next boot, which is
  now one `hud-say.sh` run whose `RECEIPT ... IN HISTORY` (or `NOT IN HISTORY` with a `Console` actually read) is
  the measurement the last three boots have been circling.
- **The receipt polls the located object, not the array**: once a console is found each poll re-reads its slots, a
  base whose class word moved is reported as destroyed, and the report carries its own cost line (`RECEIPT read N
  console object(s) by <route>: X array walk(s), Y page read(s), Z s`).
- **Instruments**: `tools/xemu/hud-text.py` (`console_from_hud`, `holds_class`, `--receipt-tail`, `--receipt-walk`,
  three selftest cases), `docs/HUD_TEXT.md` (the receipt section). No artifact and no new file.

### Execution notes (2026-11-09, the receipt ran -- and found no console in its window)

- **The receipt's first live run answered a different question than it was built to**: `hudr2` loaded `M00`, the
  read resolved the live `GameHud` (`0x09362100`) and recorded seven text changes (the intro's dialogue, then
  `New Objective : Open the door for the doctor`), the tutorial panel was dismissed (`05-precommands` 88,237 px,
  `06-dismissed` 0 px -- a frozen intro frame, as measured before), and `drive.py` then lingered its derived
  180 s so the receipt could run. It ran 18 walks over the newest 2,000 of 48,646 objects and found **no
  `Console` object at all**, so both markers printed `NOT IN HISTORY` -- a verdict about the search window,
  not about the keys.
- **The fix is the verdict, and it is in both halves of the instrument**: `hud-text.py` prints
  `RECEIPT <mark> UNMEASURED` when no walk read a `Console` (an empty `seen`, which is what the tail shortcut
  leaves), and `hud-say.sh` reports `UNREAD` instead of `NOT DELIVERED` for it. A `Console` is created with the
  viewport, before the level's actors, so "not among the newest 2,000" is expected rather than remarkable; the
  shortcut needs a wider window or an index, not another boot.
- **Boots cost what they cost**: this run's own frames also repeat two negatives -- `03-dismissed` reads 0 px
  because the intro card is a frozen picture (a pad press into it changes nothing), and the marker shots are
  whole-frame deltas in a live 3D view (53,752 and 1,502 px), so a shot can only say "the screen moved", never
  "the message painted".
- **Instruments**: `tools/xemu/hud-text.py` (`UNMEASURED` verdict), `tools/xemu/hud-say.sh` (`UNREAD` for it),
  `docs/HUD_TEXT.md` (the receipt section). No artifact and no new file.

### Execution notes (2026-11-09, the box verdict gets a second half: did the submit run at all?)

- **A box that never held the marker had two causes and no way to tell them apart**: the keys never reached the
  console (the `M00` tutorial modal, a press into an unfocused window, a dropped character -- each measured here) or
  the command ran and the sink drew nothing (the message path is the other half of this unknown). `hud-text.py
  --receipt MARKS` reads the console's own `History[16]` in the same boot, after the typing, and answers per
  marker: `RECEIPT <mark> IN HISTORY` (a submit ran) or `NOT IN HISTORY` (the keys never arrived). It is reached
  from the census's class table (`0x0050cb80`, `inventory/xbe_class_table_names.csv`) instead of a name lookup, so
  the read is one `--tail` walk rather than a walk of every object; the slots are `FString`s at `+0x50`, stride 12
  through `+0x110`, and `HistoryTop`/`bTyping`/`TypedStr` print beside them because a populated history plus a line
  still being typed is not a submit (`console-state.py`, 2026-11-06).
- **`hud-say.sh` runs the receipt on every boot** and prints `DELIVERED`/`NOT DELIVERED`/`UNREAD` beside each
  marker's box verdict. Its `FOUND` test now reads box lines only: the receipt's own lines carry the marker, so a
  `FOUND` that was really a `RECEIPT` line would report the message path as painting when it painted nothing. The
  receipt lands *after* the watch, so `drive.py --linger` derives as `120 s + 30 s per command` -- the reader is
  killed two seconds after `drive.py` exits, and a cut-off receipt reads as an unmeasured one.
- **Still unread on this run**: the boot it was built for reached `M00` (name table 40,427 at 281.8 s, row
  `0x08812800` outered `M00`, `+0x1008` -> live `GameHud` `0x09342100`, three boxes reading `default` at 366.8 s)
  and was still typing its commands when this run's budget ran out, so the receipt's own verdict is the next run's
  first read -- one boot, no new instrument.
- **Instruments**: `tools/xemu/hud-text.py` (`--receipt`, `--receipt-wait`, a selftest case), `tools/xemu/hud-say.sh`
  (receipt on every run, derived linger, box-only `FOUND`), `docs/HUD_TEXT.md` (the receipt section). No artifact and
  no new file.

### Execution notes (2026-11-09, the tutorial panel is dismissed before the commands, and the boxes stay empty)

- **The panel needed its own press and the instrument now measures it**: `hud-say.sh` presses the pad's `A` once
  `--post` has passed the intro's hand-over, immediately before the first command, and brackets it with two shots.
  `hudm1/try1` read 40,532 differing pixels on `05-precommands` and 34,096 on `06-dismissed`, and the second frame
  is gameplay with the "Press the X button to open the door" panel gone. The mission sequence's own `pad=a` lands
  on the intro card ("10 days later / Press A") and is not that press (its shot reads 0 px).
- **The watch was still being cut short by the reader's page cache, and the report hid it**: a flat 300 s deadline
  killed `hudf2`'s read at 479 s with two of its four commands still untyped, and every marker printed ABSENT.
  `hud-text.py`'s `--deadline` now derives from `--wait + --watch + 60` (>= 300 s), `hud-say.sh` passes the run's own
  budget, and a log that hits it reports UNREAD with the reason.
- **The four markers are ABSENT with the panel gone, on a boot whose boxes read live**: intro turns at counts
  14/35/33/50/56/53 and the game's own `New Objective : Open the door for the doctor` at count 45 (480.4 s), then
  all three window slots read 0 at 677.7-678.5 s -- the level's `GameHud` was destroyed while the boot ran.
  **Next**: a `Console.History[16]` read after the last command in the same boot, which separates "the keys never
  arrived" from "the command ran and drew nothing".
- **Instruments**: `tools/xemu/hud-say.sh` (post-resolve dismissal, `PANEL` verdict, `--deadline`),
  `tools/xemu/hud-text.py` (deadline derived from the sequence), `docs/HUD_TEXT.md`, `docs/UNKNOWNS.md` (U15).

### Execution notes (2026-11-09, the "hand-over" was the menu's row dying, and the level's HUD reads live)

- **The 40,000-name floor is a pre-gate, and the row's own `Outer` is the level gate.** A `hud-say.sh` boot read
  40,427 names at 281.7 s and resolved `row 0x06b30000` (object 41,474 of 42,401) **outered to `MainMenu`** -- the
  menu's `StargatePlayer`, which the map load destroys. Its `GameHud` `0x0682ab00` was gone one second into the
  watch (three slots `0x00000000` at 317.9 s), `+0x1008` then held a `ColorModifier`, an `Other` and finally no
  object, and a walk at 343.2 s found no `StargatePlayer` among the last 2,000 of 46,169 objects. So the previous
  note's "the intro's `GameHud` was destroyed as the intro handed over" is the map load tearing down the *menu's*
  row -- the level's row does not exist yet, and the marker was being read against a HUD nobody would ever paint.
- **`resolve` now skips a row outered `MainMenu`, and a `--wait` run keeps walking until its budget** (10 s
  between attempts) instead of stopping after `--tries` 60 s after the first row appeared. The same boot then
  resolved the level's row `0x08832800` (object 47,885 of 48,494, outer `M00`) with `+0x1008 -> GameHud
  0x09362100`, and read the level's own dialogue live (counts 14 then 35, 33, 50, 56, 53) and the game's own
  objective banner in `NotificationWindow` at 479.2 s -- the field `addnotification` would write.
- **`--follow SECS` (`hud-text.py`) and `--post SECS` / `--gap SECS` (`hud-say.sh`)**: the watch re-reads the row's
  `+0x1008` on that cadence and rebinds when it holds a different `GameHud`, and the harness's commands can be
  scheduled past the level's own hand-over instead of into the intro. `--watch` now derives from `--post`, and the
  reader's deadline includes it. `--selftest` covers the rebind and the menu-row skip, in `make ci`.
- **The four markers are still ABSENT, and this boot names the next blocker**: the command steps ran after the
  level's HUD resolved, and `drive.py` recorded `4 shot step(s) did not repaint` with a noise floor of `0 differing
  pixels` -- the `M00` tutorial modal freezes the picture and holds input. **Next**: dismiss the modal *after* the
  resolve (a `pad=a` between resolve and the first command), then the four markers; the reader is no longer the
  open question.
- **Instruments**: `tools/xemu/hud-text.py` (outer gate, budget-bounded resolve retries, `--follow`),
  `tools/xemu/hud-say.sh` (`--post`, `--gap`, `--follow`, derived `--watch`, `SLOT` lines in the report),
  `docs/HUD_TEXT.md`. No artifact and no new file.

### Execution notes (2026-11-09, `missions.sh` resolves its own display, so a cron shell can boot a sweep)

- **The sweep the bisect wanted could not start from a cron shell**: `missions.sh` passed `XAUTHORITY="$XAUTHORITY"`
  to `drive.py` under `set -eu`, so an unset variable aborted the first index *after* its HDD copy had been taken,
  with `XAUTHORITY: parameter not set` -- which reads like a harness fault and not like an environment one. It now
  resolves `DISPLAY` and `XAUTHORITY` itself, the way `hud-say.sh` and `console-commands.sh` do, and both call
  sites take `${XAUTHORITY:-}` as a second guard.
- **The receipt needs no boot**: `env -u XAUTHORITY -u DISPLAY sh tools/xemu/missions.sh OUT 10 --dry-run` prints
  `display :0, xauthority /run/user/1000/xauth_jpxZPb`, so the resolution is visible in the dry run.
- **Instruments**: `tools/xemu/missions.sh` (`--dry-run` prints the pair; `sh -n` in `make ci`), `docs/XEMU.md`.
  No artifact and no new file.

### Execution notes (2026-11-09, the watcher was freezing the boot it watched, and the boxes read live on a level)

- **A stub reader that never resumes froze six boots** (`tools/xemu/hud-text.py`): `objects.Guest` interrupts the
  CPU on connect (`_connect` -> `mem.Gdb.interrupt`) and nothing ever sends `c`, so a watcher started one second
  before the boot halted the guest at 4.5 s -- before video init. That is every blank-frame symptom in one cause:
  one identical frame per run, `GNames` answering all 84 polls with a boot-time value, no level, and pad presses
  landing on a stopped CPU. It was written up twice as "a property of the capture path"; the capture path was
  never at fault. `LiveGuest` (`--live`) makes each read a stop/read/resume triple and `hud-say.sh` passes it.
- **The first live boot reached the level, and the boxes hold the level's own dialogue**: `M00` at index 10, stub
  up at 4.5 s, level-sized name table at 281.7 s (40,427 names), row `0x08842800` (object 47,885 of 48,494, outer
  `M00`), `+0x1008 -> GameHud 0x09312100`, and `DialogueWindow` (`0x09737300`, `+0x394`) went `count 14` (the
  intro card's line) then four speaker turns at counts 35, 33, 50 and 56 while the watch polled; the other two
  boxes held the `default` placeholder (count 8). U15's field is read; the counts are in `docs/HUD_TEXT.md` and
  the strings are not, because they are game content.
- **A `--tail=2000` walk at the name-table floor is a menu read**: the first two walks (42,438 and 47,042 objects)
  found no `StargatePlayer` because the level had not spawned the row yet; the third, 1,452 objects later, found
  it. `--tries` is load-bearing, and the row's index moves by ~1,400 objects between the list and the level.
- **Still ABSENT: `say sg1txt1` and `addnotification sg1txt2`**, and this boot says why it is not the reader: all
  three window slots read `0` at 437.5 s (the `GameHud` the read had resolved was destroyed as the intro handed
  over) and the two commands' shots did not repaint (521 px against a 2,254 threshold). **Next**: type them after
  that hand-over instead of into the cutscene, and read the two markers in the window that follows.
- **Instruments**: `tools/xemu/hud-text.py` (`LiveGuest`, `--live`, a selftest case that fails if a read stops
  resuming), `tools/xemu/hud-say.sh` (`--live`, corrected header), `docs/HUD_TEXT.md`. No artifact and no new
  file, so `inventory/artifact_hashes.csv` is untouched.

### Execution notes (2026-11-08, the HUD-text watcher's window is its own clock, and a blank capture is one frozen frame)

- **The watcher could not see what it was built for** (`tools/xemu/hud-text.py`): `--watch` was bounded by the
  *process* clock (`started`), and a boot resolves its `GameHud` 300+ s in, so the early-exit guard ended the
  watch before its first poll on every real run -- the log showed a baseline and nothing after it. The loop is
  `watch()` now, measured from its own start, and `--selftest` fails if the early exit comes back.
- **The focus is not the blank frame**: `--step=focus=30` reports `the emulator owns the focus after 0s` on both
  boots of this session (`hud7`, `hud8`) and the frames are still blank, so "a HUD-text boot wants the screen to
  itself" is retired. **The blank frame is one frozen picture**: all eleven frames `hud8` saved are the same
  8,021-byte PNG (md5 `1860677834e7a3c214fd295018442c91`) -- a black 1,280x960 field with one green dot, no
  menu and no error dialog -- across four minutes and twenty-six input steps.
- **Those boots' guest never initialized `GNames`**: 84 polls over 420 s, 0 that could not read it at all, and
  never a plausible table (a menu boot reads 34,800, a level 41,265), so the frames and the guest agree that the
  boot, not the message, failed. `--wait` now prints how many polls could not read the table, and
  `hud-say.sh`'s deadline is the sequence's whole length (`boot + settle + 90 + 15 s per command`) because
  300 s expired while the boot was still loading.
- **Still unread**: what `say sg1txt1` leaves in `DialogueWindow`. The next measurement needs a boot whose game
  starts, and the launch that did produce real frames today (`missions.sh` at 20:34) differs from this one in its
  step list -- that difference is the next thing to bisect.

### Execution notes (2026-11-08, a HUD-text boot now says why it failed: one boot at a time, and a verdict inside the run)

- **The blank frame is not the screen** (measured 2026-11-08, `docs/HUD_TEXT.md`): with the Steam client the
  display's only window and the desktop holding `_NET_ACTIVE_WINDOW`, a `--boot=240` hud-say boot still
  captured blank frames -- 7,556-byte PNGs, black with one green pixel, five shot steps at `0 differing pixels`
  -- while the stub answered and the vpad was bound. So `0 differing pixels` says nothing about the pad, and
  the previous note's "wants the screen to itself" was half the story.
- **The watcher outlived its own deadline, so a failed boot had no cause**: `--wait` was
  `boot + settle + 150` (450 s) against a sequence that finishes in about `boot - 75 + settle + 40` s, so the
  watcher was killed mid-wait and `hud-say.sh` reported `UNREAD` for a question nobody had asked. The deadline
  is now derived from the sequence (`boot - 75 + settle + 30 + 12 s per command`) and a log holding neither a
  resolved `GameHud` nor "no level within" prints `NO VERDICT` instead of a cause.
- **Two boots on one desktop read as a broken emulator**: a second boot on a GDB port this script's own retry
  held answered `-gdb tcp::N: Address already in use`, and `drive.py` reported it as `xemu exited immediately`
  (exit 3). `hud-say.sh` now refuses to start while `pgrep -x AppRun` or a leftover `SG1 virtual pad` is
  present, naming the collision instead of the emulator.
- **Still unread**: what `say sg1txt1` leaves in `DialogueWindow`, and whether `addnotification sg1txt2` reaches
  `NotificationWindow`. The read needs one boot whose name table reaches 41,265 *and* whose watcher is still
  alive to report it; that is the next run's measurement.

### Execution notes (2026-11-08, the map list is the disc's 73 in sorted order, and two pad positions pin the order)

- **Nothing truncates the list, and the "41 names" was a scrolled page table** (`docs/MAP_LIST.md`,
  `tools/map-list-order.py`): U16's breakpoint read (73 names returned at `0x2fe809`, `count=73 max=76` at
  `0x2fe8e0`) had already retired the cap; what was missing was the *order*, and the shipped `Maps/` directory
  answers it without a boot -- sorted 0-based index 10 is `M00` and index 40 is `M15B`, exactly the two
  highlight positions a frame measured after 10 and 40 `pad=down` presses. So the list is the disc's own sort,
  and the 30 campaign maps past `M15B` sit at indices 41-70.
- **Census**: 73 `.unr` -- 61 campaign `M*`, 7 `DM-`, 3 CTF (two `CTF-`, one `SGCTF-`), 2 shell maps
  (`Entry`, `MainMenu`).
- **A prediction, not a census**: the sort routine (`0x000f8ab0`) is not read, so the two anchors are the whole
  evidence for plain byte order, and case sensitivity is unread (`DM-Ha'tak` is the only name outside
  `[A-Za-z0-9]`). `--check` fails if either anchor moves or the count stops being 73.
- **Instrument**: `tools/map-list-order.py` (`--selftest` in `make ci`); it reads file names only and writes
  nothing, so no artifact and no boot.

### Execution notes (2026-11-08, the HUD's text boxes have a live reader, and three boots never reached a level)

- **The sink `say` needs is four offsets, and each is gated** (`docs/HUD_TEXT.md`, `tools/xemu/hud-text.py`):
  `StargatePlayer+0x1008` `GameHudPtr` (the target's own `Name` must read `GameHud`, which is what keeps a heap
  neighbour from being read as the HUD), then `GameHud+0x050`/`+0x054`/`+0x058` for `DialogueWindow`/
  `NotificationWindow`/`InteractionWindow`, then `UIWidget+0x394` `DisplayText` as an `FString` whose `count`
  includes the NUL. The route's texts are `docs/HUD_MESSAGES.md`'s; this is the object they end in.
- **The field is cleared, not just faded, so the read has a two-second deadline**: `UITextBox.DisplayTextBox` sets
  `StartFadeTime = TimeSeconds + FadeOffsetTime + Duration` and `Update` calls `Hide(t, true)` past it, which zeroes
  `DisplayText` -- with `Duration` 2.0 s. That is why `msg1`'s 128 MiB dump ten seconds late held no copy of the
  string, and why the per-command frame one second after the submit is inside the window and still empty.
- **No live read yet, and the gate is what kept that honest**: three boots (two at `--boot=180`, one at 240 with
  `raise`/`activate`) captured blank frames -- 2,331-2,688-byte PNGs, a black window with one green pixel, against
  tens of KB for a real frame -- while the guest and the stub were alive, and none reached a level-sized `GNames`
  (34,800 at the menu, 41,265 on a level). `hud-text.py` refused all three with the measured count instead of
  printing an empty `DisplayText`, and `hud-say.sh` reports `UNREAD`, not `ABSENT`, for that case.
- **Two input facts carried forward**: 180 s is too early for the first `A` (`sink14`'s failure, reproduced twice
  here; 240 s passes first time on the sink script), and an upper-case marker letter is not a QEMU key name
  (`guest key T -> invalid parameter: T`) -- the character is dropped and the run reads exactly like a message that
  never arrived, so markers are lower case and digits and the script refuses anything else.
- **Instruments**: `tools/xemu/hud-text.py` (`--selftest` in `make ci`, `make hud-text`), `tools/xemu/hud-say.sh`
  (`sh -n` in `make ci`), `docs/HUD_TEXT.md`. Neither writes a file.

### Execution notes (2026-11-08, three HUDs, three answers to `say`, and nothing paints the buffer)

- **The message route is per HUD class, and the disc ships three of them** (measured 2026-11-08 from the shipped
  `ScriptText`, `tools/hud-messages.py`, no boot; 2,317 named classes, 5 under `Engine.HUD`): `HUD.Message` ->
  `AddTextMessage`; `GoauldConsoleHud`/`ReplicatorHUD` -> `LocalizedMessage` -> `AddTextMessage`; `StargateHUD` ->
  `LocalizedMessage` -> **`SetSubtitle(Msg, 2.0s)`** for `Say`/`TeamSay`, `AddNotification` for everything else;
  `StargateTeamHUD` declares none of them and inherits `StargateHUD`'s. **One feed in the whole tree**:
  `PlayerController.TeamMessage` (line 1543) calls `myHUD.Message(...)`; `ClientMessage` hands to it at 1505.
- **`AddTextMessage` is not "commented out" in `StargateHUD` -- it is absent** (0 occurrences of the identifier in
  its shipped text), which corrects the sentence `docs/XEMU.md` carried; the two other HUDs do call it.
- **`TextMessages[]` is filled by three HUDs and painted by none.** `HUD.DisplayMessages` is its only painter and all
  four `DisplayMessages` overrides (HUD, StargateHUD, GoauldConsoleHud, ReplicatorHUD) replace it without `Super`, so
  the two console HUDs fill the engine's buffer and then override the painter: it is write-only there.
- **`StargateHUD.SetSubtitle` hands the text on to `GameHud.DisplayTextBox(SubTitleText, Duration)`** (line 309), the
  live `GameHud` the sink read holds at `+0x1008` -- so `say <marker>` draws, or does not, through an object the sink
  report already names, and the 1 s frame `console-commands.sh` already takes is inside the 2 s window.
- **Instrument**: `tools/hud-messages.py` (`--selftest` in `make ci`, `make hud-messages`); `docs/HUD_MESSAGES.md`
  carries the class-by-class table. It prints class, function and call names with line numbers, never source text,
  and writes no file.

### Execution notes (2026-11-08, the outer column names its object, and the level says so)

- **`sink16` names the row's `Outer`** (measured 2026-11-08, `sink15`'s script unchanged, gdb 12653): `row 0x08842800`
  (index 47,885) **class `StargatePlayer` outer `0x06b40000` `M00`** name `None`, slots unchanged --
  `NotificationManager` `0x0968c340`, `GameHud` `0x09322100`, `CommandInterface` `0x096ff230`, Pawn `0x089e0000`
  class `Oneill`. `M00` is the level object, so the report now says "a level, not the menu" in the row itself.
- **The bug was `class_name`'s one column over**: `Outer` is an object pointer and the report resolved it as
  `nm[outer]`, i.e. asked the name table for entry `0x06b40000`. `object_name` reads the outer object's own `Name`
  -- one read -- and `--selftest` pins both sides (the pointer read as an index answers nothing, the object's own
  name answers `M00`).
- **Cost: 89 pages / 368 requests / 41.6 s in reads / 46 s total** against `sink15`'s 87 / 360 / 39.7 s / 44 s --
  the two extra pages are the outer objects' names, inside the noise.
- **The pawn's own `Name` stays `?`**: it is empty on this build, a fact about the pawn, not the printer. The
  class column is what names it.
- **Boot**: `--boot=240 --retries=3` reached `M00` on attempt 1 again (two runs in a row), so the 240 s window is
  the setting `sink14` was missing rather than a lucky boot.

### Execution notes (2026-11-08, the class column names the row, the slots and the pawn's class)

- **`sink15` reached `M00` on attempt 1 and printed the classes** (measured 2026-11-08): `--boot=240 --retries=3`
  and the fixed `class_name` -- row `0x08802800` (index 47,885) **class `StargatePlayer`**, `+0x1004`
  `NotificationManager` `0x096ec340`, `+0x1008` `GameHud` `0x09342100`, `+0x100c` `CommandInterface` `0x0975f230`,
  `+0x038c` Pawn `0x089a0000` **class `Oneill`** -- every slot live, every class named. `sink14`'s two failed attempts
  were the 180 s boot window, not the tool: the same script with 240 s passed first time.
- **Class names cost nothing measurable**: 87 pages / 360 requests / 39.7 s in reads / 44 s total against `sink13`'s
  85 / 352 / 43 s, so the four extra reads are inside the noise and the class column is free.
- **`Outer` is still `?`** (`0x06b40000`): the report prints that pointer through the name table, the same bug
  `Class` had, so the same one-read fix applies and is the next instrument change.
- **Next**: the HUD's message route, read statically from the shipped `ScriptText` (no boot): `Engine/PlayerController.uc`
  TeamMessage calls `myHUD.Message`, `StargateHUD.Message` comments out `AddTextMessage` and sends `Say` to
  `SetSubtitle` (2 s) and everything else to `AddNotification`, while `StargateHUD.DisplayMessages` overrides
  `Engine.HUD.DisplayMessages` without `Super` -- so `say <marker>` is the on-screen assertion a console probe can
  frame-diff, and `TextMessages[]` is never drawn.

### Execution notes (2026-11-08, a class pointer is not a name index, and the confirming boot did not arrive)

- **`sink13`'s report called every class `?`, the row's own included** (`row 0x08842800 class ?`, and a live object it
  had just named `GameHud` printed `class ?` beside it): `UObject::Class` at `+0x24` is a class *object*, and the report
  read that pointer as an `FName` index into a 41,265-entry table. `class_name` now reads the class object's own
  `Name` (the rule `class_named` already used), so the row and each slot name their class for one extra read.
- **The negative costs a boot, and this run did not get one**: `sink14` (same sequence, fixed tool) booted twice on
  index 10 and neither attempt reached the level -- `missions.tsv` never got a row and the run was stopped inside
  attempt 2 -- so the printed `class StargatePlayer` is the next run's measurement, not this note's claim.
  `--selftest` pins the regression without a guest: `class_name` on the synthetic row and a live `GameHud`, which the
  old `nm[cls]` answered `?` for.
- **Instruments**: `tools/xemu/sink-slots.py` (`class_name`, used by the row line and by `describe`), `--selftest` and
  `make ci` green. `docs/XEMU.md` carries the output this note reads.

### Execution notes (2026-11-08, the sink is live on a level, and a page with a hole is not a page that is gone)

- **The four sink slots are live on a loaded `M00`** (measured 2026-11-08, `sink13`: disc `$WORK/xemu/sg1.iso`, gdb
  `12650`, `02-mission` changed from `00-title` by 80,489 px / 6.55%, names `41,265`): one row of class
  `StargatePlayer` in the last 5,000 of 48,646 objects, at index **47,885** (`0x08842800` -- the same index `sink1`
  held), and `+0x1004` `NotificationManager` `0x0968c340`, `+0x1008` `GameHudPtr` `0x09322100` (`GameHud`), `+0x100c`
  `commandInterfacePtr` `0x096ff230` (`CommandInterface`), `+0x038c` `Pawn` `0x089e0000` are **all live objects**. So
  the console's silent `say`/`addnotification` is not a null sink; what is left is the HUD's own guards (U15).
- **`sink12`'s 9.7 s a page was the retry path, not the stub.** The stub answers `E` for a request that touches one
  unmapped byte, `Guest.read` reconnected for each of four attempts and `Pages.page` repeated that three times, so a
  page with a hole cost ~12 attempts; and the whole page was cached `None`, which also hid the objects in its mapped
  half. `sink13`'s read cost **85 pages / 352 requests / 38.7 s in reads / 43 s total**, 3 pages with a hole, slowest
  page 0.6 s -- the same tail bound that did not finish in 900 s.
- **Instruments**: `sink-slots.py` reads a page in 16 KiB chunks, re-reads a failed chunk in 4 KiB sub-pages and marks
  only those missing (`u32` returns None per address), reports requests/unanswered/split/holes and the slowest page,
  and probes the guest only after a page with a hole; `objects.py`'s `Guest.read` takes `attempts=1` so an `E` costs
  one round trip instead of a reconnect and a sleep. `--selftest` gains the hole case (7 assertions) and `make ci`
  passes. `docs/XEMU.md` carries the two-run table and the slot values.

### Execution notes (2026-11-08, the typed line is in the console's `TypedStr`, and the key never draws)

- **The keyboard attach is a race with machine init, and it is what plr2 lost** (measured 2026-11-08):
  `device_add usb-kbd,port=1.3.2,id=kbd` as the run's first step answers `usb port 1.3.2 (bus usb-bus.1) not found
  (in use?)`; with `wait=10` first the same command answers nothing and `info usb` then lists
  `QEMU USB Keyboard, ID: kbd` at `1.3.2` beside `gamepad_0` at `1.3.1` (`plr3`, stub `12370`). The message is the
  one a busy port gives, so "in use" is not evidence of another device -- the hub does not exist yet at t=0.
- **The console object holds the typed line** (measured 2026-11-08, same boot: stock disc md5 `109fff2a…`,
  `System/Default.ini` inside the image says `Console=Engine.Console`, `M00` loaded, `48,646` objects, names `41,265`):
  one `Tab`, one `spc`, `h i s t` leaves `' hist'` at `0x02344b00 +0x044` (`count=6`, `objects.py --fstrings`), and
  the frame is **byte-identical** to the one before the letters (`05-typed` vs `04-tab`, PIL `getbbox()` `None`). So
  the band `console-line.py` reads as `prompt` on a loaded `M00` (56 cyan px, 64x32 at `+34+209`) is a HUD element,
  not the console line: it is present with an empty `TypedStr` and unchanged with text in it.
- **`--compare` has no default to diff on this class** (measured 2026-11-08): `--object 0x01fb9bc0 --compare` prints
  `class default not found`, and `--instances` on the class object `0x00dcea00` finds exactly one object of class
  `XboxClient` -- the live row `0x01fb9bc0` (index `35,581`). The route the previous note proposed ("the transient
  slot is null in the class default and set in the live instance") has no second row here; the console is reached
  through `InteractionMaster` `0x02406720` at player `+0x0c0` (its `+0x030` block), and `--find` over the player's
  first `0x1400` bytes puts the console pointer itself nowhere.
- **Next**: read the master's `+0x030` and the `TArray<UViewport*>` element (`+0x02c data 0x009e7454 num=1 max=5`)
  on one boot, then `--bytes 0x040,0x200` on `0x02344b00` after a `ret` submit for the `History[16]` receipt.
  The array element is a *bare address*, which no mode could read (`--object` needs an object, `--bytes` is an
  offset inside one), so `fields.py --read ADDR[:LEN]` now reads one and names any dword that resolves -- and the
  `mem.py --dump` reads this run took after the linger expired (`0x41bfbf5a…`, zeros) are a reset guest, not a
  reader defect: the name table read garbage at the same moment.

### Execution notes (2026-11-08, the live player is `XboxClient : Client` and declares no `Console` property)

- **`--prop-name Console` on the live player answers "no property named Console in the chain", and the chain is
  printed** (measured 2026-11-08, `plr1`: `M00` loaded, keyboard at 75 s, one `Tab` and `h i s t`, prompt frame
  `95x32` at `+34+209` = 67 cyan px, object array `0x041e0000` 48,646 objects, names 41,265). `0x01fb9bc0` is class
  `XboxClient` (class object `0x00dcea00`) and the super chain is `XboxClient` -> `Client` -> `Object`, so U15's
  "read it from a `Player` object" is closed as well as the `PlayerController` arm.
- **`Client`'s 32 declared objects contain no `Console`** (`--children` on `0x00d40600`: array indices 308--339,
  `ETextureDetail` ... `DrawDistanceLOD`, viewport and renderer settings). The CSV row U15 relied on
  (`inventory/class_properties.csv:2907`) is a script class whose `Console`/`GUIController`/`InteractionMaster`/
  `LocalInteractions` fields are all `0x2000:Transient`, so no offset is serialized; the native constructor wires
  them and the live window holds `InteractionMaster` `0x02406720` at `+0x0c0` -- the same master the console default
  names at `+0x030`.
- **Budget, measured:** `--children` on a class object is one page-cached array pass, **3 m 5 s** for 48,646
  objects; `--prop-name` is seconds. This run's typing was lost to `bIgnoreKeys` (four `gkey` presses straight after
  `Tab` leave an *empty* prompt), so a text read needs the spent key the earlier sequences used. **Next**:
  `fields.py --object 0x01fb9bc0 --compare` -- the transient slot is null in the class default and set in the live
  instance, so the diff names the console without a property chain -- then `--fstrings` on that pointer.
  `docs/XEMU.md` "The player object is `XboxClient : Client`" carries the numbers.

### Execution notes (2026-11-08, `UProperty::Offset` is `+0x48`, and the HUD's windows are named by property)

- **`Offset` is the dword at property `+0x48`, from two independent witnesses on one boot** (measured 2026-11-08,
  `prp3`: gdb `12,573`, `M00` loaded at 89,499 px, object array `0x041e0000` 48,646 objects, names 41,265).
  `SkipMatineeTextString` (`StrProperty` `0x019c5400`, index 26,928) holds `0x258` there -- the offset its `FString`
  occupies on both `GameHud` rows -- and the three config scales hold the offsets they were measured at on the class
  default: `DialogueFontScale` `0x168`, `NotificationFontScale` `0x19c`, `InteractionFontScale` `0x1d0`. Two value
  sets that could not both be coincidence is the calibration. `fields.py` now defaults `--prop-at` to
  `OFFSET_AT = 0x48`, so `--prop-name`/`--prop-value` name a value slot with no calibration read, and the selftest
  runs on the default so the position cannot drift.
- **A window is named by its property's `Offset`, not by its place in the declaration list.** `GameHud`'s 158
  declared objects list `DialogueWindow` (26,717), `InteractionWindow` (26,718), `NotificationWindow` (26,719), but
  the offsets are `0x50`, `0x58`, `0x54` -- so index order would have read `InteractionWindow` as the notification.
  The live row `0x09362100` resolves all three to `UITextBox` objects (`0x097c7300`, `0x097c6900`, `0x097c6e00`),
  chain `UITextBox` `0x0197a800` -> `UIWidget` `0x0197b400` -> `Actor` -> `Object`; the class default `0x0682ab00`
  holds `0` in all three slots because `GameHud.uc:493/:511/:522` spawn them.
- **U15's window question is answered: the text arrives and it is drawn.** `UIWidget` declares 65 objects and
  `DisplayText` (`StrProperty` `0x019c2450`) is at `Offset 0x394`; the live `NotificationWindow` holds
  `'New Objective : Open the door for the doctor'` (Count 45) there, not the 27-character
  `NO NOTIFICATION TEXT SET!!!!` placeholder, and `prp3/02-mission.png` from the same boot draws that identical
  line at the bottom of the instruction panel. `DialogueWindow` holds `'General, would you mind getting the door for
  me sir?'` (53) and `InteractionWindow` `'default'` (8) at the same offset.
- **One `--children` pass over the array is about 200 s, and its names compare exactly.** The `StrProperty` reads
  back as `'SkipMatineeTextString'` with `Outer` `0x01886c00` on both a `repr` read and the list, yet one pass over
  that same boot missed the entry and the next found it -- so a name-keyed filter that finds nothing is worth one
  re-read before the object is called absent. The windows' `DisplayText` read is the whole of U15's remaining
  measurement; `docs/NATIVE_PROPERTIES.md` §10 is the record.

### Execution notes (2026-11-08, a class's declared fields are the objects outered to it, and `Outer` is one array pass)

- **A class declares its fields as objects outered to its class object, so `Outer` is the field index** (measured
  2026-11-08, `census19` on the `prp2` boot: gdb `12571`, `M00` loaded at 116,782 px, `GObjObjects`
  `data 0x041e0000` num 48,646, `GNames` `data 0x07730000` num 41,265; `GameHud`'s class object `0x01886c00`, its
  two rows `0x0682ab00` index 41,505 and `0x09322100` index 47,914). Every object in the array whose `Outer`
  (`+0x18`) is the class object is something that class declares: `GameHud` has **158**, and the list holds its
  whole declared surface -- `DialogueWindowMesh`, `NotificationWindowMesh`, the three `FloatProperty` scales
  (`DialogueFontScale` index 26,915, `NotificationFontScale` 26,918, `InteractionFontScale` 26,920),
  `SkipMatineeIcon`/`SkipMatineeIconSize`/`SkipMatineeTextString` (26,928, a `StrProperty` at `0x019c5400`)/
  `SkipMatineeTextOffset`, `numWaypoints`, the `crosshair*` fields, and ~40 `Function`s. `fields.py --object
  <class object> --children` prints it.
- **The name-first walk is the trap that route replaces.** `Reader.object()` resolves the *class* name with an
  uncached `u32(cls + 0x20)` before it reads the object's own name, so filtering the whole array by name costs one
  stub round trip per object: `census18` had not reached its first `DisplayText` after five minutes and was
  killed. The `Outer` test is `try_u32` -- page-cached -- for all 48,646 entries except the matches, so the same
  question answered in about **80 s** on the same stub. A whole-array question asks a cached dword first.
- **`InitProperties` copies the declared `FString` into the instance.** Both `GameHud` rows hold
  `'Press A to Skip'` at `+0x258` (`Count 16`), with *different* `Data` pointers -- `0x059254c0` on the default,
  `0x091b63e0` on the live row -- so the calibration reference is valid on either object, and the string is not
  shared state.
- **`UProperty::Offset` is still uncalibrated, and this is the one read left.** The window dump inside
  `SkipMatineeTextString`'s `UProperty` was still in flight at the run's cap, so `--prop-at` stays a guess and
  U15's live `NotificationWindow` `DisplayText` slot is unread. What changed is the cost: the property objects
  are now listed with their addresses, so the calibration is one `--bytes 0x28,0x180` on the `StrProperty`
  address this list names (address per boot, never carried across), with no walk behind it.

### Execution notes (2026-11-08, a class object holds `Super` at `+0x28`, so its fields are in `GObjObjects`, not in it)

- **The class object is not a field list.** `GameHud` `0x01886c00` (identity re-read this boot) holds, in its first
  `0x1400` bytes: `Super` at `+0x28` — the chain reads `GameHud` -> `Interaction` -> `Interactions` -> `Object` —
  the meta-class `Class` at `+0x624`, a `Landed` override at `+0xd74`, and a `Function` (`PlayHUDSound`) at
  `+0x3c`, the `Children` offset the native reader uses, whose `Next` chain stops after that one link. A scan that
  walks every dword as a candidate `Children` head and follows `+0x28` as `Next` finds no chain of field objects:
  the longest candidates are the `Super` chain itself (3 links) and two `Function` tails. So census15's UITextBox
  result ("field chain head at class+0x63c: 1 fields", a `Function`) is not a truncated chain, it is the shape of
  this build's class object — and `+0x28` is `Super` on a class object even though it is `Next` on a `UProperty`.
- **A `UProperty` is found in `GObjObjects` by name, and `Outer` names the declaring class.** The array walk
  (`--prop-index`, `--prop-up`) answers in the same read what the class-object route could not: the first
  `DisplayText` hit is index 1647, a `StrProperty` at `0x00fe6f90` whose `Outer` is `SGInteraction` — not the
  `UIWidget` field U15 wants, which is why the ancestor test is in the tool. The walk costs minutes over 48,646
  objects (a header page read each), so it runs as a desktop script, not inside a foreground call.
- **`Offset` is calibrated, not serialized.** `SkipMatineeTextString`'s `FString` sits at `GameHud`'s class default
  `+0x258`, so the dword position inside that `UProperty` object holding `0x258` is `Offset`; `--prop-at` takes it
  and `--prop-in` the instance. That read is what remains: this run shipped the instrument and the negative (the
  class object holds no fields, and the `Children` chain has one link), and the next run's first read is the
  calibration window plus `DisplayText`'s slot in the live `NotificationWindow` — the U15 answer.
- **The instrument is `--prop-index`, `--prop-outer`, `--prop-up` and `is_ancestor`, selftested without a boot.** The
  selftest builds one object array holding a `StrProperty Text` outered to a child class, a `StrProperty
  DisplayText` outered to its `Super`, and a `Function DisplayText` outered to the child: the name match must take
  the property and reject the function, `--prop-outer` must separate the two declarations, and `--prop-up` must
  reach the parent's field.

### Execution notes (2026-11-08, the config path fills the scales, and the three declaration-order slots are not them)

- **The three scales are `var config float`, the shipped ini sets them, and the class default carries the ini's
  values — `0.85`, `0.8`, `0.85`, not `1.0`** (measured 2026-11-08, `census16` = `~/sg1/work/xemu/tfs1b.sh`, gdb
  `12563`, `M00` loaded at 42,103 px, noise 3,175). `System/tweak/Misc/StargateHUD.ini`'s `[StargateGame.GameHUD]`
  sets `DialogueFontScale=0.85`, `NotificationFontScale=0.8`, `InteractionFontScale=0.85`, and `GameHud.uc:2` is
  `config(Tweak_Misc_StargateHUD)`, so the config load is what writes those slots. `fields.py --find
  0x3f59999a,0x3f4ccccd,0x3f800000,0x3dcccccd` over the default's `0x4000` prints `0.85` at `+0x168`, `+0x1c4`,
  `+0x1d0` and `0.8` at `+0x19c`, `+0x1e0`; the live row `0x09372100` (index 47,914; the default is `0x0682ab00`,
  index 41,505) holds the same six dwords. The three declaration-order slots at `+0x24c..+0x254` are `0.1`, `0`,
  `0` on **both** rows, so they are not the scales: the declared `1.0`s are nowhere in the object's own field data
  (`0x3f800000` appears once, at `+0x180c`, in the deflate stream the previous note identified).
- **Nothing but the config path can put a scale in a `UITextBox`**: `GameHud.uc:493`, `:511`, `:522` assign
  `DialogueWindow/NotificationWindow/InteractionWindow.TextFontScale = Dialogue/Notification/InteractionFontScale`
  after each `Spawn(class'UITextBox')`, and `UITextBox` (`StargateGame.u`) declares `var config float
  TextFontScale`. Its `DisplayTextBox` writes the body to `DisplayText`, a `UIWidget` field -- so U15's
  "`NotificationWindow.Text`" is `DisplayText`, and the read it asks for is a name plus that property's offset.
- **The property-name-to-`Offset` instrument is in, but the field chain is not found by shape on `UITextBox`'s
  class object.** `fields.py --properties --prop-bytes 0x28,0x20` on `0x0197a800` (`UITextBox`; its class object is
  `0x00bcea00`) answers `field chain head at class+0x63c: 1 fields`, and that one link is a `Function` (`EatStr`) --
  `find_field_chain` accepted a `Next` tail, because its rule ("every link ends in `Property`") holds vacuously for a
  one-link chain. The calibration the next read needs is the `GameHud` chain: its `SkipMatineeTextString`
  `StrProperty` should carry the already-measured `+0x258` in the dword position that holds `Offset`. The flags
  (`--prop-bytes OFFSET,LEN`, `--prop-value NAME`, `--prop-at`, `--prop-in`) and their selftest are in this commit;
  the missing piece is the `Children` offset, measured off a class object, not a new dataset.

### Execution notes (2026-11-08, the class default's field block ends before `+0x1800`, and the three scales are not in it)

- **All three `1.0` hits are inside an allocation the object array does not hold, so `--near` overstates an object's
  extent** (measured 2026-11-08, `census15` = `~/sg1/work/xemu/ghud7.sh`, gdb `12561`, `M00` loaded at 42,644 px).
  `--find 0x3f800000,0x00000001 --size 0x5500` on the class default `0x0682ab00` (identity re-checked this boot,
  `class \`GameHud\` name \`GameHud\``) prints three `1.0`s -- `+0x180c`, `+0x420c`, `+0x4288` -- and the bytes say
  what they are: `+0x17e0..+0x17fc` are **zero** and `+0x1800` starts `0x6dd00119 0x000800fe 0x1e006200 0x0119e516
  0x00fe64a0 0x1b0c0006`, a deflate-shaped stream with no object pointer and no field-sized value in it. So
  census14's "one `1.0` at `+0x180c`, no run of three, therefore either non-adjacent offsets or an unwritten
  `Float`" was reading a **neighbour's bytes**: `--near` bounds an object by the next *object*, and a plain heap
  allocation between the two reads as part of the object (this is why `+0x5500` looked like the object's extent
  while its own field data stops before `+0x1800`).
- **The declared order is exactly adjacent, and the three slots it names hold `0.1`, `0`, `0`.** At `+0x230` the
  field block holds `0.9` (`+0x234`), `0.97` (`+0x238`), `0.05` (`+0x248`), `0.1` (`+0x24c`), `0` (`+0x250`), `0`
  (`+0x254`), then the `SkipMatineeTextString` `FString` at `+0x258` (`Data 0x059254c0`, `Count 16`, `Max 16`) --
  the 16 characters `class-defaults.py` reports, and immediately after it `3.0f` (`+0x264`) and `2.2f` (`+0x274`).
  `class-defaults.py --class GameHud --walk` reads the block as three consecutive `Float` records (`info=0x24`,
  5 bytes each) then the `Str` (`info=0x5d`), so the declaration order puts the three scales at
  `+0x24c..+0x254` whatever the linker does with direction -- and those three dwords are `0.1`, `0`, `0`, not
  `1.0`. **There is no `0x3f800000` anywhere in the object's own field data.**
- **The three are `var config float`, which is the standing explanation and not yet a settled one.** `GameHud.uc:90`,
  `:96` and `:102` declare all three `var config float` (and `:493`, `:511`, `:522` copy them into the windows'
  `TextFontScale` at run time), and no shipped `System/*.ini` sets a `FontScale` key -- so the serialised `1.0` need
  never reach the field slots, the config path being what fills a `config` property. The competing explanation is
  that these three slots are not the scales at all (a property the block does not serialise sitting between them and
  the string), which the same read cannot rule out. **Next**: `DialogueWindow.TextFontScale` (the live HUD's
  `+0x050` `UITextBox`) over one boot -- `0.1` there makes the `config` route measured instead of argued, and the
  same read answers U15's `NotificationWindow` `Text` question.
- **The instrument is `--bytes` and `--flat` on `fields.py`, both selftested without a boot.** `--bytes OFFSET,LEN`
  hexdumps a window of the object with the float reading of every dword (`1.0f` for `0x3f800000`, nothing for the
  integer `1`, whose float is `1.4e-45`) and the ASCII of a printable dword; `--find` says a dword is there but
  never what sits beside it, and that is the whole of this measurement. `--flat OFFSET` counts zeros, flats
  (small integers and floats), object pointers and *other* dwords per `0x100` window, because a field block's end
  is where the other dwords start -- the boundary `--near` cannot see. `--flat -1` over `0x2000` on one object
  costs minutes against a live stub (every dword is tried as an object pointer), so read it over a bounded
  `--size` and expect a slow call rather than a hung one.

### Execution notes (2026-11-07, a class object holds no pointer to its own instance, so the default-object scan is closed)

- **`DefaultObject` is not in the class object's first `0x1400` bytes, and that ends the search rather than another
  window** (measured 2026-11-07, `census12` = `~/sg1/work/xemu/ghud1.sh`, gdb `12549`, census10's pad sequence).
  `fields.py --object 0x01886c00 --instances --locate --size 0x1400` finds the same **two** `GameHud` rows
  (`index 41505 0x0682ab00`, `index 47914 0x09322100`, both outered `0x0097f928` `Package`) and then **zero**
  class-object dwords in `+0x28..+0x1400` whose value is either address. `--locate` is address membership over the
  instance walk's own output, not `Class == this class`, so unlike the `--members` label that earlier notes read as a
  candidate it cannot be a superclass's class object. Consequence: the census5 + census10 scans could not have
  succeeded, `Outer` does not split the rows, and the default has to be identified by *contents* -- the six declared
  defaults `tools/class-defaults.py StargateGame.u --class GameHud` reports (three font scales `1.0`,
  `SkipMatineeTextString` `Press A to Skip`, `bShowWeapon`/`bVisible` `True`) against each row's live field block,
  with `--near` deciding whether a `0x600` window's tail is a neighbour's. **Next**: read `+0x600..+0x600` of both rows
  field by field with `--show-text` and pick the row that holds the declared defaults with no live text -- the class
  object will not name it.
- Two smaller readings from the same boot, both of which change how a run is written: **the class-object address did
  not move** (`GameHud` `0x01886c00` on census4, 5, 10, 11 and now 12), so a boot that "loses" it was read badly, not
  moved -- this run first reported otherwise because its identity check piped `fields.py` through `head -2` and threw
  away the object line; and **`02-mission` can measure 0 px on a boot that loaded fine**, because M00's tutorial modal
  freezes the guest (`03-dismissed` is the live frame, 102,774 px). Judge a load from the last frame and
  `GObjObjects.num`, never from the shot taken while the modal is up.

### Execution notes (2026-11-07, a class default extends past `0x1400`, and the three `1.0` scales are not adjacent)

- **The `GameHud` class default's own bytes run to at least `+0x5500`, and the live row's stop at `+0x600`**
  (measured 2026-11-07, `census14` = `~/sg1/work/xemu/ghud3.sh`, gdb `12557`, then `ghud5.sh`/`ghud6.sh` on the same
  stub). `--near 0x0682ab00 --size 0x8000` puts the first foreign object at `+0x5500` (`Sound`
  `te_enmyvehkilld_dg_n_01`, `0x06830000`), so every `--find` hit up to there is the default's own dword;
  `--near 0x09352100 --size 0x1400` finds a `ParticleMaterial` at `+0x600` and four more at `+0x900`, `+0xc00`,
  `+0xf00`, `+0x1200`, so the live row's extent is **at most `0x600`** and a wider window on it reads a neighbour.
  Two consequences for earlier notes: census11's `--diff` over `0x600` compared the live row's own bytes after all,
  and any `--show-text` past `0x600` on the live row was reading widgets it does not own.
- **The three `1.0` font scales are not three adjacent dwords, and only one `0x3f800000` exists in the default's
  first `0x4000`** (same boot). `--find 0x3f800000,0x00000001 --size 0x4000` on `0x0682ab00` prints **one** `1.0` at
  `+0x180c` and `0x00000001` at 25 offsets (`+0x084`, `+0x088`, `+0x240`, `+0xf30`, `+0xff0`, `+0x1224` … `+0x2b30`),
  with **no run of three** anywhere; the live row holds neither value past `+0x240` inside its own `0x600`. So the
  scalar half of the six declared defaults is still open, and the naive signature ("three `1.0`s in a row") is
  refuted: either the scales land at non-adjacent offsets (the class declares many fields between them) or a
  `Float` default is not written into the object's block at load. **Next**: compare the declared property order —
  `tools/class-defaults.py StargateGame.u --class GameHud` — against the offsets the block actually holds, and read
  `+0x180c`'s neighbours; that needs one boot and no new data.
- **The instrument is `--find` without the walk, and it is what made the measurement affordable.** `--find` used to
  fall through to `walk_fields` unconditionally, which resolves every dword as an object and again as an array head:
  the same query over `0x1c00` on a window that crossed into a neighbour's bytes ran **past ten minutes** and printed
  nothing (killed by hand). `--find` alone now returns before the walk — `0x1400` in **2.5 s** — while
  `--show-text`/`--diff`/`--compare` keep it. `--find` also groups its hits into **runs of one value at consecutive
  offsets**, because that is the only shape in which a dword is evidence of declaration order rather than a
  coincidence. `--selftest` covers both.
- **An instance address carried across boots is not an address.** `census13`'s live row `0x09322100` read as
  "does not read as an object (`Class +0x24`)" this boot, while the class object `0x01886c00` was `GameHud` for the
  seventh time; `--instances` then named `0x0682ab00` (index 41,505) and `0x09352100` (index 47,914). Resolve the
  rows with `--instances` every boot — this is the third distinct address the live row has taken (`0x09322100`,
  `0x09352100`, `0x09332100`). The live row's own `+0x050`/`+0x054`/`+0x058` are again the three `UITextBox`
  windows, on a `--show-text` read of `0x600` (`docs/UNKNOWNS.md` U15's read is reachable from these).

### Execution notes (2026-11-07, the class default is the `GameHud` row that holds the declared text)

- **`GameHud`'s class default is `0x0682ab00` (index 41,505), and the row the census called live is the live one**
  (measured 2026-11-07, `census13` = `~/sg1/work/xemu/ghud2.sh`, gdb `12553`). The run loaded `M00` (`03-dismissed`
  44,522 px against a 4,076 threshold, noise 1,778), `GNames` `0x006be0d8` -> `data 0x07740000 num 41,265`,
  `GObjObjects` `0x006c21b0` -> `data 0x041e0000 num 48,646`. `fields.py --near` puts **no other object inside
  either row's `0x600` bytes** (previous neighbours `0x06829600` `ParticleMaterial0` at `-0x1500` and `0x092e0000`
  `SquadMenu` at `-0x42100`), so census11's `--diff` compared two objects' own bytes after all, and the "property
  records at `+0x324`" are inside the second row rather than a neighbour's. By contents `0x0682ab00` carries **37**
  pointer/array rows, every one a *package* asset (`StaticMesh`/`Texture`/`Shader` at `0x01xxxxxx`), no runtime
  widget, and the declared `SkipMatineeTextString` at `+0x258` -- `FString of 16 characters: 'Press A to Skip'`,
  which is the value `class-defaults.py` reports. `0x09322100` (index 47,914) carries **65**, including a widget
  tree allocated during this load at `+0x034..+0x07c` (`UIWidget`/`UIProgressBar`/`UITextBox` at `0x0974xxxx`, one
  `0x500` apart), a `Waypoint`, a `StaticMeshActor333` and a live `HitEffectManager` at `0x0974af00` -- state a
  class default cannot hold. The default-object question is therefore settled by contents, and census4/census5's
  "the live `GameHud` at `0x0682ab00`" was the pointer scan's label rather than the object's.
- **The three `1.0` font scales are not in either row's first `0x600`.** `--find 0x3f800000` finds no such dword in
  either row (both hold `0x00000001` at `+0x084`, `+0x088` and `+0x240`), so the scalar half of the six declared
  defaults still needs a wider window. **Next**: `--object 0x0682ab00 --size 0x1400 --near` and
  `--find 0x3f800000` over that bound, then the same on `0x09322100` -- one boot, four reads.
- **The instrument is `--find` plus a text read that is not truncated.** `fields.py --find 0x3f800000,0x00000001`
  prints where requested dwords sit, because `walk_fields` follows pointers, arrays and text and cannot see a
  scalar at all. And `--show-text` printed `'Press A '` for a 16-character string: `array_at` read `min(num, 64)`
  *bytes* while `num` counts characters, so a 16-character UTF-16 string showed its first eight. Both are in this
  commit and `--selftest` covers each without a boot.
- **Two reads cost this run its time: the identity check and `--properties`.** `ident()` piped `fields.py` through
  `head -2`, which drops the object line (census12's first attempt "lost" the class object the same way), so the
  check failed and the script walked all 48,622 object headers with `findcls` -- 13 minutes -- to re-confirm
  `0x01886c00`, which five previous boots already agreed on (sixth now). `--properties --scan 0x1400` then ran past
  ten minutes on one row and was killed: a chain candidate is tried per dword. Neither is needed for a contents
  question -- `--object <class>` plus `grep` on its whole output answers identity, and `--find`/`--show-text`
  answer contents.

### Execution notes (2026-11-07, `GNames` is a `.data` triple that does not move, and a field window needs its extent)

- **`names.py --find` costs more than it saves on a loaded level**: it scanned `.data` for **over fifteen minutes**
  without printing a candidate (killed by hand), while `tools/xemu/gnames-find.py` reads the section in 64 KiB
  chunks and finds the triple in seconds. Measured on `near12` (`census11`'s script, a mission): `GNames` triple
  `0x006be0d8` -> `Data 0x07740000` / 41,265 / cap 51,886; `GObjObjects` triple `0x006c21b0` -> `Data 0x041e0000` /
  48,646. The **triples are static and the arrays are not** (`census11` had `GNames` at `0x07730000`), so a stored
  `--at` from a previous boot fails whole -- `read 0x7730000+0xc failed after 4 attempts` was this run's first
  error, not a dead stub.
- **`fields.py --near` is the instrument the two-`GameHud` question needed, and the question is still open.** A
  `--diff` over `0x600` compares dwords past the object's own extent -- nothing readable states a size -- so
  `--near` sorts the object array by address and names the object that starts at each boundary; `next - ADDR` is an
  upper bound on the extent, and a `--selftest` covers it without a boot. **The measurement did not land**:
  `near12` reached a mission (the `02-mission` frame differs from the title by 100,594 px) and the array read, but
  the name walk that locates the instances was still running at the end of the budget. Next run:
  `findcls.py --grep GameHud --at 0x006be0d8`, then `fields.py --near <both addresses> --size 0x600`.

      **The frame draws it, and one boot holds both sides of the submit (2026-11-08)**: `vp1` (stub `12390`, the
      stock disc -- md5 `109fff2a…`, `Console=Engine.Console` read inside the image) typed ` hist` on a loaded
      `M00` and the band `console-line.py` watches read **0 cyan px at `04-tab`, 52 px in a `105x32` box at
      `+34+209` once the text was in (`(> hist_` at 6x), 0 again one second after `ret`** -- so the drawn line
      tracks the console, and the previous note's "the frame never draws it" is withdrawn (`plr3`'s pair fits an
      open-and-empty console in both frames). In the same boot both dumps hold one `Console` object, `0x02641b00`:
      pre `TypedStr ' hist'` with `History` empty and `bTyping 1`; post 3 s after `ret`, `History[0] ' hist'`,
      `TypedStr` empty, `Top 0 -> 1`, `Bot -1 -> 0`, `bTyping 0`, which `console-state.py --receipt hist` asserts
      -- control and receipt are the same object, 20 s apart. The address is per-boot heap (`plr3`'s `0x02344b00`
      is unrelated bytes here), and `Engine.Console` has no `PostRender` row in the binary either, so the "no
      `PostRender` therefore no picture" inference is retired: the engine draws it and the swap's tell is the
      pre-filled `Say `. **Measured 2026-11-08: the draw is `Engine.HUD.PostRender`, and no `Console` virtual is
      involved** (script text on the disc; no boot). `Engine/HUD.uc:299` is `simulated event PostRender( canvas
      Canvas )`, lines 424-425 call `DrawTypingPrompt(Canvas, PlayerConsole.TypedStr, PlayerConsole.TypedStrPos)` while
      `PlayerConsole.bTyping`, and line 629 composes the prompt from the two-byte literal `"(>"`, a `chr(4)` caret and a
      trailing `_` only at the end of the line -- so the earlier scan for the byte pair `(> ` had nothing to find.
      `PlayerConsole` is `HUD`'s own `var() Console`, linked from `PlayerOwner.Player.Console`; `StargateHUD`,
      `GoauldConsoleHud` and `ReplicatorHUD` all extend `HUD` and none overrides `PostRender`, so every HUD class
      reaches one draw. The route the previous note named is measured and closed: `inventory/class_virtual_overrides.csv`
      (`tools/class-overrides.py`) diffs all 492 tables against their parent's, and `Console` (`0x0050cb80`) vs
      `Interaction` (`0x0050c880`) differ on slot 3 alone -- the per-class slot that differs in all 492 -- while
      `Interaction`'s `PreRender`/`PostRender` slots are `c2 04 00` stubs; a script override never reaches the table.
      **Next**: the same script-text route names what draws the one readable output (`stat fps`'s `60 cur FPS`), which
      would give the console a second proven output channel; the receiver is a HUD function, one grep away.
      Instrument: `tools/xemu/console-receipt.sh OUTDIR [COMMAND]`.
      **Measured 2026-11-08, no boot:** there is no receiver in script. `%u cur FPS` (`0x0051f358`) and `%u avg FPS`
      (`0x0051f374`) are UTF-16 literals in the code section's own `.rdata`, pushed at `0x0025635a`, `0x00256405` and
      `0x002564e5`, all inside one function `0x00256280` whose only caller is `Engine_UnRender` (`0x001daed0`) --
      `inventory/stat_fps_render.csv` (`tools/image-strings.py --topic stat-fps`). All 2,319 classes were searched:
      neither literal appears, and the 129 case-insensitive `fps` hits are `#ifdefPS2`. So `stat fps` is native end to
      end (command, counter, draw) and cannot be a script output channel. **Next**: the guest's own `bTyping` -- read
      `PlayerController.Player.Console` and `Console.bTyping` after a `tab` in one boot -- decides whether the HUD's
      prompt should have drawn at all; instrument `tools/xemu/console-receipt.sh OUTDIR [COMMAND]`.
      **Measured 2026-11-08, no boot, from the dumps already on disk:** that read did not need a boot, and the answer
      flips the suspect. In `findtext4` -- the dump where the console holds `' hist'` with `TypedStrPos 5`/`bTyping 1`
      and no frame drew a prompt -- the live `StargateHUD` (`0x05c5ca00`) holds **that same console** at `+0x398`
      (`0x02344b00`), so `(PlayerConsole != None) && PlayerConsole.bTyping` is already true and the silence is
      *downstream* of the link (`inventory/hud_console_link.csv`, `make hud-link DUMP=<ram.bin>`). The two closed dumps
      give the same field and target with `bTyping 0`, and the values `DrawTypingPrompt` consumes are ordinary
      (`ConsoleColor (253,216,153,255)`, `HudCanvasScale 0.95`, `ConsoleMessagePosY 1.0`, no `.ini` override).
      **Measured 2026-11-08, no boot: neither variable needed the breakpoint.** `GetConsoleFont` is not native --
      `Engine/HUD.uc:847` is `static function font GetConsoleFont(Canvas C)`, and `StargateHUD` re-declares it to return
      `GetSmallFontFor(C)`; `tools/hud-console-font.py` (`make hud-console-font`) follows both branches to a font this
      disc ships (`UWindowFontsTex.TahomaB30` on the override branch, `Engine.DefaultFont` on the base one) and puts
      the prompt at X 16, Y 468 - YL of a 640x480 canvas -- so the draw path has no null font and no clip rectangle
      left to find. What remains open in 7.3 is the **output** sink, not the input line (`docs/XEMU.md`).

### Execution notes (2026-11-08, the two failed sink boots had no disc in the drive)

- **`sink10` and `sink11` never ran the game.** Both left `--iso` at `missions.sh`'s default, `$WORK/sg1.iso`, which
  does not exist in this WORK (the disc is `$WORK/xemu/sg1.iso`); xemu logs `warning: Could not open ...: No such file
  or directory` and boots the HDD alone. So `sink10` attempt 1's 657 px highlight and `sink11`'s 8,321 px post-select
  frame were **xemu's own "Failed to open DVD image file" dialog** (`00-title.png` reads "Please insert an Xbox disc…"
  behind it) and, after the select, the empty-disc screen (`guest-halt.py`: `black=0.9974 text=1548 BIOS SCREEN`). The
  previous note's "a level the gate threw away" and "a swallowed select press" both describe that dialog.
- **The same sequence with the disc mounted differs by an order of magnitude.** `sink12` (2026-11-08, `--iso` set):
  `01-highlight` **23,533 px** against a 1,582 threshold and `02-mission` **22,076 px** -- against 657 px and 8,321 px
  on the disc-less boots. Nothing else about the run changed.
- **Instruments**: `drive.py` refuses a `--iso` that is not there (exit 3, named) and, after the emulator starts, reads
  xemu's log for `Could not open '…'` and fails on the image it names -- the second check covers a path that exists but
  cannot be opened, which xemu also only warns about. `missions.sh` fails before it copies the HDD and names the disc
  that does exist. `--selftest` gains three cases (21 total, `make ci`). `docs/XEMU.md` carries the frames, the two
  xemu.log lines and the three-run table.
- **What the next boot is**: unchanged in its goal -- the four `StargatePlayer` slot values off a live level -- but it
  is now gated on the disc as well as on the frame, so a boot that cannot be the game costs nothing.

### Execution notes (2026-11-08, a gate that cannot run is not a verdict, and the sink values are still unread)

- **The frame gate in front of the sink read never ran: `tools/xemu/frame-diff.py` is tracked 644.** `sink10`'s
  attempt 1 reached the list (`01-highlight` 657 px) and was discarded because `tools/xemu/frame-diff.py …` answered
  `Permission denied` (126), which `if ! …` read as "the frame is still the main menu". Attempt 2 then booted fresh,
  never left the menu (0 px against `00-title`, `guest-halt` `running`), and the four slot values are still unread.
- **Instruments**: `missions.sh` now calls `frame-diff.py` and `guest-halt.py` through `python3`, keeps
  `frame-diff`'s 0/2/1 apart (only 2 means "still the menu"), ends an index with a `FAIL … frame gate` row when the
  gate itself fails rather than retrying into a second boot, and passes `guest-halt`'s exit 3 through as a printed
  warning instead of a silent "not the BIOS screen". Both tools are 755 in git now. `docs/XEMU.md` carries the
  measurement.
- **What the next boot is**: unchanged -- a boot whose `02-mission` frame passes, then the tail read. What changed
  is that a passing frame can no longer be mistaken for a menu frame by a broken gate.

### Execution notes (2026-11-08, the sink read is 64 pages, and a level-sized name table is not a level)

- **Reading the array from its end is 64 page reads instead of 347.** `GObjObjects` appends, so the row a level
  spawns is the newest one (`sink1`: index 47,885 of 48,646); `sink-slots.py --tail N` scans the last N from the end
  backwards, and `sink9` read the last 3,000 of 48,646 in **64 page reads / 77 guest reads**. It reports itself as a
  shortcut: a zero says the row is not among the newest N, never that it is absent.
- **The slot gate cost four array walks per row and now costs none.** The declaring class is matched by name along
  the row's own super chain (`chain_names`), which is one page per link, instead of being looked up in `GObjObjects`
  once per slot. `--selftest` covers the tail bound, the newest-first order and the chain names.
- **A menu boot holds 41,265 names, so `--require-level` is not a level test.** `sink9` read `num=41265` on a boot
  whose `02-mission` frame is the main menu (`frame-diff.py` under the 2.5% floor; `01-highlight` 5 px against a
  200 threshold, so the select press never opened the list) -- #249's "the gate agrees with its frame" was that
  boot, not a rule. The count still separates the profile screen's 34,800 from a booted menu, and nothing more.
- **Instruments**: `missions.sh --sink-class ADDR --sink-tail N` choose the class table and the scan bound, and
  `--sink-read` now runs the frame diff **before** it touches the stub and retries a boot whose frame is the main
  menu, instead of reading a menu row and calling it gameplay. The four slot values remain unread: `sink9` found 0
  rows of `StargatePlayer` in the newest 3,000, the menu shape `sink3` had. The next boot is a level whose frame
  passes, not a new instrument.

### Execution notes (2026-11-08, the boot that proves a level also answers what its player row holds)

- **`--pad-hold=0.35` with a 180 s window reaches a level on the sink path, and the boot says so twice.** `sink8`:
  `02-mission` **10,235** differing pixels against a noise-set threshold of **1,620**, `cpu=2` samples both in
  `.text` (`0x00062ff0`, `0x00047000`, **0 in kernel** -- `sink7`'s were `0x0003fb80`/`0x80024a97`), and the sink
  gate's own read of `GNames` is **num=41,265**, the level reference exactly (34,800 is the menu). So the level was
  loaded, not merely repainted: this is the first sink-path boot whose gate agrees with its frame. `01-highlight`
  still failed (441 px) while the mission shot ten seconds later passed -- the list's repaint is late, not absent,
  which is the reason the frame checks are on named steps rather than on one screenshot.
- **The read is the cost, and the guest has to outlive it.** `sink-slots.py` ran **9.5 minutes** without reaching a
  slot (the walk is one 64 KiB stub read per page over 48,646 array entries) and then found the stub dead: xemu's own
  log ends `AppRun: GLib: g_source_destroy: assertion ... failed`, the crash signature this project has seen when two
  boots collide, here with no second boot. A reader that reports nothing when its guest dies at minute nine is the
  same defect the harness just fixed for its steps, so it now does: `--progress` prints a page count per 64 pages,
  `--deadline` (default 900 s) stops a walk that will not finish, and a probe of the name table's own data every 128
  pages names a dead stub (`exit 4`) instead of returning an empty result. `--row ADDR` and `--census` remain the
  cheap routes.
- **Instrument** `tools/xemu/missions.sh --sink-read --gdb PORT [--expect Pawn=live] [--default ADDR]`: the boot that
  proves the level reads the four slots off its own player object while the guest is held, because `--linger` makes
  `drive.py` sleep -- so the sequence runs in the background, the read happens when the `lingering` line appears, and
  the emulator is killed after it. `--pad-hold` is exposed for the value above. Both paths keep their old behaviour
  when `--sink-read` is absent, and `--sink-read` without `--gdb` is refused rather than silently unread.

### Execution notes (2026-11-08, the harness could lose its subject and say nothing)

- **Six sink-slot boots failed at three defects in `tools/xemu/drive.py`, not in the game.** An external call had **no
  deadline** (`xdotool`, `import`, `compare`, `convert` were all `subprocess.run` without a timeout): two attempts end
  with the log's last line `window: <id>`, whose next statement is `xdotool getwindowfocus`, so a stuck X call blocked
  the harness inside itself with no log, no exit and no artifact. A guest that **exited mid-sequence** was found one
  step too late (`sink5` attempt 2 saw `apprun=0` for 332 s while the harness slept through its remaining steps, and the
  next capture's failure was recorded as "still the menu"). And **`--eeprom` naming a missing file fell back silently**
  to the profile-less `~/.local/share/xemu/xemu/eeprom.bin` (md5 `d0f50547…` against the profile's `aaa061bd…`), so
  `Continue` opened `SELECT PROFILE` and the ten d-pad presses stepped nothing -- `sink5`/`sink6` passed
  `$W/smoke1/eeprom.bin`, and the profile image is at `$W/xemu/smoke1/`.
- **All three are instruments now.** Every call carries `--deadline` (default 10 s for the X tools, x3 for `import`,
  x12 for the filters) and a missed deadline is reported **by name** with the step, the emulator's liveness and its log
  tail, whether the X server still answers and where its focus is, and the guest's VM state and EIP -- exit 4, and the
  same text in `--out/stall.txt`; the probes inside that report are themselves bounded (a thread, 3-4 s), because the
  reason for the report may be that X stopped answering. The emulator is polled before **every** step, so a lost guest
  is named at the step it was lost at. A missing `--eeprom` is a `FAIL`, and every run logs the image's basename and
  md5, so two boots that differ only here cannot look identical from outside.
- **The control is one boot with the right EEPROM path** (`sink7`, 2026-11-08): the first press after the title moved
  **22,272** px in the list crop (noise floor 311, threshold 622) -- the map list opens -- ten d-pad presses and a
  select moved **20,748** px into a level, `03-dismissed` 6,540. So the earlier "still the menu" was the run's EEPROM
  and not the map, the pad or the select press; the next sink read is the one `--expect Pawn=live` gates, not another
  attempt at the same input.
- **`--selftest` (18 cases, `make ci`)** proves the deadline path, the table, the report's text and `stall.txt`, the
  dead-emulator report and the abandonment of a blocking probe -- none of it needs X, an emulator or the dump.
  `docs/XEMU.md` carries the three defects, the two EEPROMs and the control boot's numbers.

### Execution notes (2026-11-08, the 22 native residual rows get a verdict from the image's own binder pool)

- **"Which side is right needs the C++ side" was already answerable, and the answer splits 15 / 7.** The C++ side is
  `inventory/native_binder_names.csv` — the build's own `int<prefix><Class>exec<Function>` literals, which is exactly
  the name the binder at `0x3a030` looks up. `native-reconcile.py` now joins the residual against it and writes
  `inventory/native_residual.csv` (22 rows, `verdict` column): **13 `text-missing`** (record right, the class's
  shipped text just lacks the declaration), **7 `declaration-unbound`** (declared, no record, no binder name —
  `Weapon.GetErrorVec`/`GetFireStart`, `ScoreBoard.SortPRIArrayFast`, `StargateWeapon.DrawReticule`/`updateLeadLag`,
  `HitEffectManager.Update`/`SetupFade`), and **2 `binder-only`** — `Core.Object.Divide` and `Round` are declared
  *and* bound in the image, so the record table is the half that is missing them.
- **The truncation hypothesis is only partly right.** 3 of the 13 owning texts end unbalanced (`Actor`, `GUIFont`,
  `UPreviewMapListClient`), but `PlayerController`'s 245 KB text is balanced and still declares none of its four
  `Client*`/`Server*` records, so "text truncated" explains a third of the record-only rows and not the rest.
  `--selftest` covers both verdicts and the no-binder case (11 cases, `make ci`), so a future build whose binder pool
  moves cannot silently turn every row into `record-unbound`.

### Execution notes (2026-11-08, every token has a basis, and `Core.Object`'s 17 are operator records)

- **U3's guess is falsified corpus-wide, and the question it left open is now a per-record column.** `native-reconcile.py`
  gained `--tokens` / `inventory/native_tokens.csv` (274 rows: package, class, function, token, basis) and `--selftest`
  (in `make ci`): **166** records spell their index on a declaration in the class's own text, **107** are `operator`
  signatures no declaration names, **0** belong to a declaration that named a function and spelled no index, **0**
  disagree, and **1** is unresolved (`UPreview.UPreviewMapListClient.Created`). So the compiler never invents an index
  for an index-less `native` on this build — the rule "the record's token is authoritative, the text's index is not
  exhaustive" survives only in its narrow form: the *text* of the runtime packages is incomplete, indices are not.
- **`Core.Object`'s 17 close arithmetically.** 160 token records = 57 function records that spell their index + 103
  operator records, against 143 spelled indices = 57 functions + 86 `operator` statements; the surplus is 103 − 86 =
  **17**, all of it per-signature operator records (`Add_IntInt` 146, `Concat_StrStr` 112,
  `EqualEqual_ObjectObject` 114). U1's own wording ("mostly … operator records") was one record short of "all".
- **The one unresolved row is an artifact of an editor package, not an index.** `UPreview` ships `Created` as a script
  function **with a body**; the package's two `Created` `Function` exports have outers that resolve to a `FloatProperty`
  and a `TextBuffer` — impossible for a function — every function export in the file shares the flag word `0x70004`, and
  both payloads end in the same eight bytes `47 00 00 00 02 00 02 00`. So index 2 is the second dword of a script
  function's tail, and `UPreview` is excluded from naming in any case.
- **Numbers, docs and the note ship together**: `docs/NATIVE_CLASSES.md` gains *Every token has a basis* (the table and
  both cases), `docs/UNKNOWNS.md` U1 and U3 are corrected in place, and `make native-reconcile` writes both CSVs.

### Execution notes (2026-11-08, the player object is a stock `PlayerController`, and a slot is read only where its class declares it)

- **#244's caveat is answered by two censuses, no boot: the guest's own player object is the stock `PlayerController`.**
  `findcls.py --csv`'s class column is each object's own `Class` pointer through `GNames`, so `ghud1`/`ghud2` -- each
  41,265 names, 48,646 array entries, 48,637 pointers walked over 347 pages, i.e. the same level-shaped boot as
  #244's -- put `PlayerController0` at index 35,783 `0x01c31000` with class `PlayerController` (`0x00d9ae00`) and hold
  **0** rows of `StargatePlayer` and **0** of `Pawn`. So `--class 0x00e47800` was not a wrong name: there is no row of
  that class on this boot, and the three slots declared on `StargatePlayer` have no object to attach to.
- **That is a fact about the offsets, and the instrument now refuses to paper over it.** `+0x1004`/`+0x1008`/`+0x100c`
  are fields only on a class whose chain reaches `StargatePlayer`; on a stock `PlayerController` those dwords belong to
  a neighbouring allocation and often read back as plausible object pointers -- the shape a garbage read takes when it
  is reported as a live HUD. `sink-slots.py` resolves each slot's declaring class by name in the boot, checks it
  against the row's `UStruct::Super` chain (`+0x28`) and prints `n/a` where the chain does not reach it; `+0x38c Pawn`
  is on `Controller`, so it stays valid on every controller. `--class-from NAME` takes the row class off the guest's
  own object, and `--census FILE --class-from NAME --want-class C` asks the same two questions of a saved census, so
  the paragraph above is one command with no boot. All of it is in `--selftest` (`make ci`).
- **What the sink read waits on is possession, not the level.** A `StargatePlayer` exists only once the game spawns the
  mission's player controller; these boots stop at the mission-load screen with no `Pawn` row at all, so
  `Controller+0x38c` has nothing to point at. The gate for the next read is `--expect Pawn=live` on `PlayerController0`
  (`num=` stays the cheap pre-gate, but this boot passed it and still had no player of the slots' class). Six boot
  attempts in this run did not reach that state: two were cut by the 75 s title window (`wait-stub 76s`, a cold
  `hdd-val.qcow2` copy booting past it), and the two retries stalled after `window:` appeared with no further log line
  (one before `step 1`, one at the 70 s wait after the mission-list presses) while `drive.py` noted X input focus was
  on another window. The cause of the stalls was not measured, and a run may not take the owner's focus to find out.

### Execution notes (2026-11-08, a level boot held no `StargatePlayer` row, and the gate that says so)

- **The `num=` gate is measured on both sides and costs one dword.** `names.py --at 0x006be0d8` read **41,265** on a
  boot that had loaded `M00` (array 48,646) and **34,800** on `sink3`'s menu boot (42,152) -- so a field read can be
  refused before it happens, instead of after a six-minute array pass. `tools/xemu/sink-slots.py` refuses below
  40,000 names unless `--any-state` is passed.
- **That boot holds no object whose own `Class` is `StargatePlayer` `0x00e47800`.** The walk (48,637 plausible array
  pointers, **347 page reads**) answers 0 rows, while `0x00e47800` reads back in the same boot as the class object
  named `StargatePlayer` (name index 1,773) -- so the class resolved and the array was complete; the instance is what
  is missing, against `sink1`/`sink2`'s one unnamed row outered to `M00`. The four sink slots therefore have no
  gameplay value yet; #243's menu row (`+0x1004 0x05a28c00`, `+0x1008 0x0682ab00` = the `GameHud` class default,
  `+0x100c 0x05c3c1c0`, `+0x38c 0`) remains the only measured state.
- **Caveat, stated rather than removed:** a *subclass* instance carries its own class pointer, so this is "no
  `StargatePlayer` instance", not "no player controller". The next read is the class of the boot's own player object
  (`PlayerController0`'s address from a census row, one `fields.py` read); that class is the `--class` the slots are
  then read through, with `--default` naming the class default so the mark is explicit.
- **Instrument** `tools/xemu/sink-slots.py` (`--selftest`, `make ci`): the gate, a page-cached class walk, and the
  two windows the four offsets allow (`+0x1000..+0x1010`, `+0x380..+0x390`), with `--expect SLOT=live|null|default`
  as the assertion a future boot can make. `docs/XEMU.md` carries the numbers.

### Execution notes (2026-11-08, the four player slots have offsets, and a row's `Outer` names its state)

- **`sink3` did not reach `M00`, and one field says so: the player row's `Outer`.** The one object whose own `Class` is
  `StargatePlayer` `0x00e47800` is `StargatePlayer0`, index 41,474 at `0x06b30000`, outered to the `MainMenu` object
  `0x017f0188` -- where `sink1`/`sink2` held an **unnamed** row outered to `M00` (`docs/XEMU.md`). Names 34,800 and
  array 42,152 against 41,265 / 48,646, and `03-dismissed` -> `04-gameplay` moved 1,901 px (0.15%, threshold 2.5%).
  So a `StargatePlayer` row's presence is not evidence of gameplay; its `Outer` is, and "one row, unnamed, outer `M00`"
  is the gameplay signature the previous three notes recorded without a control. The boot, not the read, is what
  failed: this is `missions.sh`'s documented swallowed-press failure.
- **The four declarations now answer with their offsets**, `UProperty::Offset` at `+0x48`:
  `NotificationManager` `+0x1004`, `GameHudPtr` `+0x1008`, `commandInterfacePtr` `+0x100c` (all `StargatePlayer`,
  objects `0x01748b90` / `0x01748c70` / `0x0192c2b0`, indices 22,180 / 22,178 / 23,782), and `Pawn` `+0x38c`
  (`Controller` `0x00d9a800`, object `0x00fe6200`, index 1,689). **The three sink fields are adjacent in declaration
  order**, so one 12-byte window covers them; `Pawn` had to come off the super chain.
- **The menu row's values**: `+0x1004` `0x05a28c00`, `+0x1008` `0x0682ab00`, `+0x100c` `0x05c3c1c0`, `+0x38c` `0`.
  `0x0682ab00` is address-identical to the `GameHud` **class default** (index 41,505) two earlier boots read, so on a
  menu row `GameHudPtr` is the class default the block was initialised from, not a live HUD; `Pawn` `0` is a
  controller with no possession. **Next**: the same four slots on an `M00`-outered row, read as
  `--bytes 0x1004,0x10` plus `--bytes 0x38c,0x8` off the row (one page each) instead of an array pass -- the run above
  cost ~6 minutes in `--instances --prop-name` over 42,152 objects -- and gate the read on
  `names.py --at 0x006be0d8`'s `num=` (34,800 menu vs 41,265 level) so a menu boot is never read as gameplay again.

### Execution notes (2026-11-08, a class object is its own struct, and the declaration is in the array)

- **`GameHudPtr` is a `UProperty` object outered to the class, not a dword of the class object** (measured 2026-11-08,
  `sink2`; the value read is the next call, not this one). `fields.py --object <class object>` resolved `+0x24`, which
  on a class object is the **metaclass**, so every `--prop-name` answered along `Class -> State -> Struct -> Field ->
  Object` and the last note's "0 declared `GameHudPtr`" was a statement about `Class`. A class object is now read as
  its own struct, and the chain it prints is the real one (`StargatePlayer` `0x00e47800` -> `UnrealPlayer` ->
  `PlayerController` -> `Controller` -> `Actor` -> `Object`, on a boot that again holds exactly one row, index 47,885
  at `0x08842800`, outer `M00`). That chain still holds **0** `GameHudPtr` dwords in the class object's 1,536 bytes
  while `inventory/class_properties.csv` says the class declares one (`UObjectProperty`, flags `0x2000:Transient;`),
  so the declaration is an object outered to the class -- exactly what `declared_objects`' docstring says. `--prop-name`
  now answers from one page-cached `Outer` pass over `GObjObjects` for the whole super chain, comma-separated names
  sharing the pass, and `--instances --prop-name A,B` reads the slots for every row in one call. **Next**: one gameplay
  boot with `--object 0x00e47800 --instances --prop-name GameHudPtr,commandInterfacePtr,NotificationManager,Pawn`,
  which states whether `SetupInteractionsFor`'s write reached the sink and gives `Pawn` as the possession control
  (`docs/XEMU.md`).

### Execution notes (2026-11-08, the sink's owner is in the guest, and a census CSV cannot say so)

- **The two loaded-`M00` object censuses hold no `StargatePlayer` instance and no `Pawn`, and a boot holds one of the
  first** (measured 2026-11-08; `tools/xemu/census-query.py`, new, `--selftest`; no boot for the census half). The
  query separates the two readings a `grep` conflates -- an object *of* class C (its own `Class` pointer is C) from
  the class *object* C. Over `~/sg1/work/xemu/ghud1/cls.csv` and `ghud2/cls.csv` (47,742 rows each,
  `findcls.py --csv`): instances of `StargatePlayer` **0**, of `Pawn` **0**, of `PlayerController` 1
  (`PlayerController0`), `StoryModeGame` 1, `InteractionMaster` 1, `GameHud` 2 -- while the class objects are all
  present (`StargatePlayer` `0x00e47800`, `Pawn` `0x00caa800`), so the names resolve and the walk had them available.
- **That zero is the file's shape, not the guest's content**: `findcls.py --csv` writes a row only when the object's
  own name resolves (43,681 of 43,741 reachable headers in `ghud2`). The `sink1` boot (`03-dismissed` 43,074 px,
  `04-gameplay` +1,949 px, array `0x041e0000` 48,646 objects, names 41,265 -- the same shape as the censuses) holds
  **one** object whose own `Class` pointer is `0x00e47800`: index 47,885 at `0x08842800`, **name unresolved (`?`)**,
  `Outer` `0x06b40000` (an object named `M00`). It is exactly the row a name-keyed CSV cannot carry.
- **The read is a property read on the instance, not on the class object**: `fields.py --object <class object>
  --prop-name X` resolves the object's own class -- the metaclass -- so it printed the chain
  `Class -> State -> Struct -> Field -> Object` and answered "no property named `GameHudPtr`", then fell through to
  the class object's 271-entry member table. `--object 0x08842800 --prop-name GameHudPtr` is the same call against
  `StargatePlayer`'s own chain, which is where `StargatePlayer.uc:26` declares it. **Next**: that read, plus
  `commandInterfacePtr` and `NotificationManager`, on a `--instances` row resolved in the same boot (the address is
  per-boot and the row is unnamed, so the class object is the only handle).

### Execution notes (2026-11-08, the binder's names are in the image, and 769 of 797 declarations have one)

- **The index-less `native` binding is measured end to end now** (2026-11-08, no boot):
  `tools/native-binder.py` (`make native-binder`, `inventory/native_binder_names.csv`) extracts every
  `int<prefix><Class>exec<Function>` wide literal from `default.xbe` -- **883 distinct names in one 440.8 KB span**
  (median gap 30 characters, so a pool the build ships) -- and joins them with `inventory/natives.csv`: **769 of 797**
  declarations have a name to bind to, including **508 of the 523** that serialise no index. The one-letter prefix is
  `A` exactly when the class descends from `Actor` in **769 of 769** rows, so the join is a measurement rather than a
  convention, and `exec` is a literal in the format, not a mark of an `exec` function (`Actor.KWake` is index-less and
  not `exec`).
- **The misses are a fact about the image, not the packages**: all 15 index-less declarations with no name are
  `Editor.BrushBuilder` (13), `Editor.MaterialFactory` (1) and `UPreview.UPreviewSkinnedButton` (1) -- modules that ship
  as script in the dump but whose C++ side is not in the Xbox image. Every declaration the game's own modules make has
  one (`Engine` 444/444, `GUI` 81/81, `IpDrv` 32/32, `StargateGame` 17/17, `StargateVehicles` 4/4), which answers U1's
  last question: the lookup succeeds in a shipped build with no map or PDB.
- **The twelve misses that do carry an index are spelling, not absence**: every `Core.Object` string operator the join
  misses is `_StrStr` in script and has a `_StringString` twin in the pool (`Concat_StrStr` ->
  `intUObjectexecConcat_StringString`), and all 114 unmatched pool names are `UObject` -- those twelve twins,
  `HighNative0`..`HighNative15` and `Iterator`.

### Execution notes (2026-11-08, the prompt's font is script, and it ends on a shipped object)

- **`GetConsoleFont` was recorded as native twice; it is script, and the engine's own override chain picks the font**
  (measured 2026-11-08, no boot): `Engine/HUD.uc:847` is `static function font GetConsoleFont(Canvas C)` -- it reads
  `OverrideConsoleFontName`, then `ConsoleFontSize` plus one per `ClipX` threshold, and returns
  `LoadFontStatic(Clamp(8-FontSize, 0, 8))` -- while `StargateHUD.uc` **re-declares it**, returning `GetSmallFontFor(C)`
  instead. `tools/hud-console-font.py` (`make hud-console-font`, every input read through `tools/class-defaults.py`)
  resolves both branches at the console's own 640-wide output: the override branch gives `LoadFontStatic(8)` ->
  `UWindowFontsTex.TahomaB30`, the base branch `LoadFontStatic(4)` -> `Engine.DefaultFont`, and **both objects are in
  the dump** (6 and 1 `Font` exports). `StargateHUD`'s `FontArrayNames` is TahomaB30 in 8 of its 9 slots and
  `FontScreenWidthSmall` runs 4096..640, so the font is the same whichever way the array order reads.
  `inventory/hud_console_font.csv`.
- **The position is inside the frame, and there is no clip rectangle to find**: `HudCanvasScale 0.95`,
  `ConsoleMessagePosX` unset (0 -- `Engine.HUD` does not set it) and `ConsoleMessagePosY 1.0` put the line at X 16,
  Y 468 of 640x480 minus `TextSize("A")`'s glyph height; only a glyph taller than 468 px would push it off, and the
  drawn band measures 32 px tall at 1280 wide. The body passes no rect -- `DrawTextClipped` clips to the canvas' own
  `ClipX/ClipY`, which the engine sets to the viewport every frame.
- **The colour cross-checks the counter**: `ConsoleColor` is `(253, 216, 153)` in the `color` struct's **B,G,R** order
  -- RGB 153,216,253, a pale cyan -- which is the colour `tools/xemu/console-line.py` counts, so the class default and
  the 52-px `(> hist_` band on `vp1` are the same line drawn twice over.

### Execution notes (2026-11-08, the HUD holds the open console, so the prompt's silence is downstream of the link)

- **The link is not broken; the dump says so twice over** (measured 2026-11-08, no boot): `tools/xemu/hud-link.py`
  (`make hud-link DUMP=...`) reads the live `StargateHUD` out of a whole-RAM dump and screens its dwords for pointers
  to an object whose class table is `Console`. In `findtext4` the HUD's `+0x398` is `0x02344b00`, exactly the console
  `console-state.py` reads as typed-into, with `bTyping 1`; `xemu/findtext3` and `msg1/try1` are the control (same
  field, same target, `bTyping 0`). `inventory/hud_console_link.csv`.
- **Two measured details, each of which produced a wrong first answer**: an object's class-table word is its *first*
  dword in this build (the typed-into `Console` opens `00 50 cb 80`), so a bare pointer can be screened by class
  without resolving a property chain -- and `HUD.uc:27` declares exactly one `Console`-typed field, which is what
  makes the screen the field; and `pmemsave` writes physical memory while the pointers in it are linear, so comparing
  the two spaces finds zero references to a console that nine dwords name. `vamap.physical_for` is the match.
- **`DrawTypingPrompt`'s inputs are already ordinary** (`tools/class-defaults.py --class HUD` on the disc's
  `Engine.u`): `ConsoleColor (253,216,153,255)`, `HudCanvasScale 0.95`, `ConsoleMessagePosY 1.0`, and none of those
  names is set in any shipped `.ini`. What is left is `GetConsoleFont(C)` (native, result not in the object) and the
  clip rect `DrawTextClipped` uses -- which is why the next instrument is a breakpoint, not a screenshot.

### Execution notes (2026-11-08, `stat fps` is native end to end, and the scanner was reading the wrong address)

- **The one readable output has a native owner, and script never names it** (measured 2026-11-08, no boot):
  `tools/image-strings.py --topic stat-fps` (`make stat-fps`) finds `%u cur FPS` / `%u avg FPS` and attributes both to
  **one** function, `0x00256280`, whose only caller in the image is `Engine_UnRender` (`0x001daed0`, 4,081 bytes).
  All three `push` sites (`0x0025635a`, `0x00256405`, `0x002564e5`) are inside that function's SEH frame body. The
  counter is a float truncated by `cvttss2si` and formatted by `FUN_00059180` (82 bytes), then drawn through virtual
  slots `+0x90`/`+0x98` of the object at `(%esi)` with X/Y at `+0x4c`/`+0x50`, a colour at `+0x60`, and the colour
  switched on the counter's own value at 20/30/40 (`cmp $0x14/$0x1e/$0x28` at `0x0025645d`/`0x0025646e`/`0x00256490`).
  Script text: 0 of 2,319 classes carry either literal (the 129 `fps` hits are `#ifdefPS2`), and `stat` is not among
  the 336 `exec` declarations -- so the console's single readable output is closed to script, which is why a second
  output channel has to come from script (`Canvas.DrawText` is available: `Engine/AIController.uc`) or from the guest.
- **The scanner's needle was matching a substring, so it read zero for a string the code pushes three times**: `cur FPS`
  is found at `0x0051f35e`, three wide characters into the literal at `0x0051f358`, while `push imm32` names the first
  character. `Image.string_start()` now walks a hit back to its string's first character (bounded at 256 units,
  printable-only), and the topic's evidence is the whole format. The three older topics are unchanged (`--check`:
  karma 8, lipsinc 19, package-check 6) and `inventory/stat_fps_render.csv` is the new artifact.

### Execution notes (2026-11-08, the console line is drawn by the HUD, and a class table carries native dispatch only)

- **The draw is `Engine.HUD.PostRender` and the console is not in the path** (measured 2026-11-08 off the disc's own
  script text, no boot): `HUD.uc:299` declares `simulated event PostRender( canvas Canvas )`, line 424 reads
  `if ((PlayerConsole != None) && PlayerConsole.bTyping)`, line 425 calls
  `DrawTypingPrompt(Canvas, PlayerConsole.TypedStr, PlayerConsole.TypedStrPos)`, and `DrawTypingPrompt` (629) draws
  `"(>"@Left(Text, Pos)$chr(4)$Eval(Pos < Len(Text), Mid(Text, Pos), "_")` in `ConsoleColor` at `ConsoleMessagePosX/Y`.
  Three earlier readings fall out of that one body: the prompt literal is `"(>"` joined by `@`, so a scan for the byte
  pair `(> ` had nothing to find; the caret is `chr(4)` and the `_` appears only at the end of the line; and the colour
  is a HUD `var() Color`, which is what `console-line.py` counts as cyan pixels. `PlayerConsole` is the HUD's own
  `var() Console` (27), linked in `LinkActors` from `PlayerOwner.Player.Console` (683); `StargateHUD`,
  `GoauldConsoleHud` and `ReplicatorHUD` extend `HUD` and none of them overrides `PostRender`.
- **`tools/class-overrides.py` is the census the question wanted, and its answer was a negative** (`make
  class-overrides`, artifact `inventory/class_virtual_overrides.csv`): 302 parent/child pairs resolve out of 492
  tables, 200 classes override 658 slots, 102 override nothing but slot 3, and 39 add 185 slots. `Console` and
  `Interaction` are both 36 slots and identical except slot 3 -- the per-class slot -- although `Console.uc` overrides
  five of Interaction's functions. **A script override never reaches the table**: the six slots Interaction adds are
  one real body (`0x0014f790`) and five two-instruction stubs whose `ret` immediates match `KeyType`/`KeyEvent`/
  `PreRender`/`PostRender`/`Tick` (`33 c0 c2 08 00`, `33 c0 c2 0c 00`, three `c2 04 00`), and subclasses inherit
  those same addresses. So no vtable diff can name a script draw, which is why the `vamap.py` route the previous note
  proposed was the wrong instrument rather than an unfinished one.
- **`vamap.py` was never needed for this**: the tables are image `.rdata` at their virtual addresses, so the read is
  the image file, not a physical dump. The earlier attempt read a 128 MiB RAM dump at `0x0050cb80` and got bytecode
  because that is a linear address the page tables have to translate; `tools/class-overrides.py --class Console` reads
  the same table out of `default.xbe` in one pass.
      **The receipt is the next read, not a new instrument (2026-11-09)**: `hud-text.py --receipt MARKS` reads
      `Console.History[16]` in the same boot and prints `RECEIPT <mark> IN HISTORY` or `NOT IN HISTORY`, which is
      the difference between "the keys never arrived" and "the command ran and drew nothing" -- the two failures
      every box verdict so far conflated.

      **The route reaches a `Console` and it is the class default (measured 2026-11-09, `hudsay6`)**: with
      `--console-client=0x01fb9bc0` the client's `InteractionMaster` `0x02406720` names a `Console` at `+0x30`
      -- **`0x02344b00`**, the class default `docs/XEMU.md` records -- in 4.3 s and 0 array walks, so both markers
      read `NOT IN HISTORY` about `defaultproperties` rather than about the keys. `hud-text.py` now tests
      `UObject::Outer` (`+0x18`) against the `Package` class table and refuses a default object, walking instead.
      The next boot's verdict is the submit: `IN HISTORY` if the walk finds a live console, otherwise the console
      instance is outside `GObjObjects` and the read moves to the master's other slots.
      **A title screen has no console to type into (measured 2026-11-09, `hudq3`, `hudq4`)**: the four-state read
      this item asked for cannot be taken at the title. `--console-at 0x02344b00` reads one object in **3 page reads
      / 1.3 s** (the walk a miss falls back to is 347 pages / ~227 s), `--no-walk` keeps a census from turning into
      that walk, and `--trace 400` polled the object and the client's `+0x30` slot while `drive.py` pressed `Tab`, `a`
      and `ret` -- all three `-> ok` -- and printed **one change, the opening line**: slot `0x02344b00`, `bTyping 0`,
      `TypedStr` empty, `HistoryTop 0` throughout. So the title screen's block is the same seven pointers as the
      level's, the console in it is the class default, and no keypress creates one; `hudq1`'s `bTyping 3` reads 0 on
      both title boots, so it is a level-boot reading. **Next**: the same trace on a loaded level, started before the
      boot and bound to the HUD's `+0x398` console once it is non-null (`--trace` reads the client slot, so a live
      console appearing there is a change it already reports).
      **Instruments**: `tools/xemu/hud-text.py` (`--console-at`, `--no-walk`, `--trace`, `trace_types`),
      `docs/HUD_TEXT.md`, `docs/UNKNOWNS.md` U15. No artifact.

- [~] 7.4 - [~] 7.4 CI job per commit: manifest verify → package parse → decompile coverage → compile status →
      bytecode match % → asset count. Publish the scoreboard as the repo's README badge/table.
- [~] 7.5 Nightly regression: rebuild + xemu boot test; fail on any new assert dialog or missing class.
      **The harness has now actually run** (`tools/nightly.sh`, `docs/REGRESSION.md`): the first run against the full
      dump failed five ways, all of them its own plumbing (scripts committed non-executable, the manifest counting
      BIOS images as dump files, a stage that seeded nothing, `set -u` on an optional variable, and a `rebuild` stage
      that ran `VAR=value` as a command). Fixed, the same run is **19 of 19 PASS**. The harness exists and runs
      (`tools/nightly.sh`, `docs/REGRESSION.md`): `make ci`, then `verify-dump`,
      `class-tables`, `libraries` when `SG1_DUMP` is set, then optionally the xemu boot, one summary and one log per
      stage under `build/nightly/<UTC>/`, exit non-zero on any failure, last 30 runs kept. It reports `git status`
      drift instead of repairing it. **Not yet**: the rebuild stage (blocked on 3.6) and assert-dialog detection (the
      evidence channel is `EIP` sampling, `docs/XEMU.md`).

**Acceptance:** CI is green on a stock machine, the scoreboard moves in the right direction on every PR,
and a deliberate one-line script break is caught by the boot test.

### Execution notes (2026-11-06, six console commands all delivered, and the message channel is the HUD's)

- **The first run of `console-commands.sh` that loaded a level** (the two lost boots were input, on a desktop that was
  rendering another game): `M00` on the first attempt with `--pad-hold=0.35` and a 180 s boot, `01-list` 57,224 px and
  `02-mission` 101,648 px against the title's 18,343 px floor. Six commands, all six in `History[16]`
  (`console-state.py`, `Console` at `0x02681b00`), `TypedStr` empty and `bTyping` 0 -- and **only `stat fps` drew**
  (`64 cur FPS`/`60 avg FPS`, 2x crop read by vision). `god`, `say hello`, `enablecheats`, a second `god` and
  `addnotification hello` left no text anywhere in the frame, and a 128 MiB `pmemsave` holds no UTF-16 copy of
  `Cheats are NOT enabled`, `God Mode On` or `You feel much lighter`.
- **Why the two message commands are silent is in the HUD, not the console**: `StargateHUD.Message` comments out
  `AddTextMessage`, sends `Say`/`TeamSay` to `SetSubtitle(Msg, 2.0, ...)` and everything else to
  `StargatePlayer.NotificationManager` -> `GameHud.SetNotifications` (both steps return early when the manager or the
  GameHUD is `None`). The engine's `HUD.TextMessages[8]` is never filled, so the buffer U15 hoped to read does not
  exist in a mission on this disc.
- **Two instrument faults, both fixed here.** `--pad-hold` was named in the PLAN note and exists in `drive.py` but was
  not accepted by `console-commands.sh`, so the run that was meant to try the knob could not have set it; and a
  `noise=` step set the pass threshold to the **largest** floor the run had seen, so the title's 18,343 px made every
  gameplay shot (682-754 px of real animation) FAIL -- 18 of them, over six commands that all executed. The script now
  also takes the per-command frame **one second** after the submit (inside the 2 s subtitle), retries a boot that
  never leaves the main menu (`--retries`, gated on `frame-diff.py` against the title frame), and forwards `--watch`.
- **A burst of `gkey` steps can repeat the last character**: `addnotification hello` arrived as
  `addnotification helloooooooo...` (65 chars) in `History`. The receipt is still exact for the command word, but a
  command with no argument would be corrupted by the same burst.

### Execution notes (2026-11-06, the console's command surface is 336 declarations, and a zero is three facts)

- **The console's command list is measured from two sources that agree**: `tools/console-commands.py` (`make
  console-commands`) reads every `exec function` out of the shipped `ScriptText` and joins it to `Exec` in
  `inventory/function_flags.csv` -- **336 declarations over 37 class/state scopes, 292 distinct names, 336 of 336
  carrying the binary's `Exec` flag, 0 disagreements** (`inventory/console_commands.csv`, 461 rows). The 125
  `Exec`-flagged functions with no declaration are **122 state-scoped** (the binary's `class` column names the
  *state* there -- `PlayerBeingElectrocuted`, `Dead`, `GameEnded`, ...) plus three stock natives declared
  `native function` without the `exec` keyword (`SetProgressMessage`, `SetProgressTime`, `GetCurrentStream`).
- **`stat` is not in either source**, and neither are `open` or `cheats`: those are engine handlers, so `stat fps`
  drawing and `stat game` not drawing is a fact about the engine's subcommands, not about console delivery. That
  withdraws the sentence this page carried ("behindview's zero means the command never entered the console") --
  `BehindView(bool)`, `ShowMenu()`, `ShowDebug()` and `ShowStats()` are all measured `exec` functions, so a zero on
  them is "ran, drew nothing".
- **The cheats exist and are gated, not missing.** `fly`/`ghost`/`walk`/`slomo` are 72 `Engine.CheatManager`
  commands, the manager is created unconditionally by `PlayerController.AddCheats()` on the way into a level
  (`PlayerController.uc:591`), and `areCheatsEnabled()` refuses them until `enablecheats` -- with *drawn* messages.
  So `walk`'s measured 0 px was a movement mode with no movement input, and 7.3's missing output has a real
  candidate: `ClientMessage`/`Say` reach `HUD.Message`, which draws a line.
- **The instrument is `tools/xemu/console-commands.sh`** (one boot, a list of `--cmd`s, a frame per command, a
  whole-RAM dump at the end). **Its measurement is the next run's**: the desktop was rendering another game and both
  boots were lost to input and timing -- the first `pad=a` on the main menu moved only the highlight (527 px, and
  4,930 px at a 165 s boot) and no later press left the menu; the second reached a submit (`god`, 27,546 px against a
  158 px idle floor) and then died on a QMP `sendkey` timeout, and its frames were pruned with the run directory
  before they were read. Run it on a quiet desktop; `--pad-hold` is the knob if the 0.15 s press is the cause.

### Execution notes (2026-11-06, `pad=start` reaches no console branch, and a submit moves the counters)

- **`submit8` is one boot with a positive control and the last untested key**: round 1 `ret` from the console the boot
  opens, round 2 `pad=start` from the console one `Tab` re-opens, with the `wait=3` before the dump that the earlier
  attempt timed out on. `Console` at `0x02682b00`, the third base address in three boots.
- **The counters increment where `Console.uc`'s `IK_Enter` branch puts them**: `HistoryTop` 0 -> 1, `HistoryBot` -1 ->
  0, `HistoryCur` 0 -> 1, `TypedStrPos`/`bTyping` 55/1 -> 0/0, and `History[0]` copies the whole 56-character line (a
  repeated `gkey` grew it), so the receipt cannot be a coincidence with the five-character control.
- **Start reaches no branch of `Console`.** Round 2's pre-dump holds the fresh ` hist` at `TypedStrPos` 5 with
  `bTyping` 1 and round 1's history in slot 0; after the tap every one of those words is identical while the frame
  moved 813 px -- not the submit, not the `Escape` clear. With `pad=a` measured the same way, no pad button drives the
  console.
- **`--state`'s phrase had the precedence wrong, and round 2 found it**: an open console with a populated `History`
  read as "submitted". `bTyping` decides first now, and `--selftest` asserts open-with-history and
  closed-with-history separately.
- **The submit is invisible**: the shot three seconds after `ret` moved 0 px, because `Engine.Console` has no
  `PostRender`. That is why the receipt is a dump and not a frame.

### Execution notes (2026-11-06, `kp_enter` is not a submit, and the console's counters are readable)

- **`submit7` is `submit6`'s round-1 shape with one key changed**: `Tab`, `spc`, `hist`, a control `pmemsave`,
  `gkey=kp_enter`, `wait=3`, a receipt `pmemsave`, one boot, `Console` at `0x02661b00`. Both dumps are identical
  (— `TypedStr` `0x07ffdb78 6/6 ' hist'`, `History` empty, `+0x38` 0, `+0x3C` -1, `+0x40` 0, `+0x110` 5,
  `+0x114` 1) so the keypad Enter delivers nothing to the console: not a character, not the `Escape` clear, not a
  submit. The frame after the press moved 1,506 px, the gameplay floor, so the picture was never the evidence.
- **`HistoryBot`'s own `-1` pins the counter offsets**: `+0x38`/`+0x3C`/`+0x40` (0, -1, 0) sit between the object's
  `InteractionMaster` at `+0x30` and `TypedStr` at `+0x44`, the declaration order of `Console.uc:8-16`, and
  `+0x110`/`+0x114` read 5/1 for a five-character line in a console that is typing.
- **`tools/xemu/console-state.py --state`** prints those five fields plus one phrase for what the key left behind,
  because `Escape` clears the line too: an empty `TypedStr` with an empty `History` is a cleared line when `bTyping`
  is 1 and a submit when it is 0. `--selftest` was corrected to the measured layout (it put `HistoryTop` past
  `History[15]`, which is `TypedStrPos`' offset) and proves typing, submitted and Escape-cleared apart in `make ci`,
  which has no dump.
- **A quoting trap cost a step, not a boot**: `"--step=monitor=info\ registers"` inside double quotes keeps the
  backslash and the monitor answers `unknown command: 'info\'`; the dumps were read with the measured `--cr3 0xf000`.

### Execution notes (2026-11-06, the receipt: `ret` submits and leaves `History[0]`)

- **The object answers what the register file could not.** `submit5` is `submit2`'s boot with the watch removed and
  a `pmemsave` after every candidate key. `d0-typed` holds `TypedStr` `+0x44` = `0x07fddb78 6/6 ' hist'` and an
  empty `History`; `d1-ret`, three seconds after `ret`, holds `TypedStr` `0 0 0` and `History[0]` `+0x50` =
  `0x07fde61c 6/6 ' hist'`. The submit ran, its copy landed in the object, and the fresh `EDI=0x0802e61c` the
  previous boot saw at the `Z3` stop is that `History[0]` buffer.
- **`History` is a static inline array, not a `TArray`.** `Console.uc:10` declares `var string TypedStr,
  History[16]`, so the entries are 12-byte `FString` structs inside the object: `History[i]` at `0x50 + 12i`,
  ending at `+0x110`, where the declaration order puts `TypedStrPos` (5 while ` hist` is typed, 0 after) and
  `bTyping` (1 -> 0). Earlier notes' "no `History` field in the first 0x200 bytes" was true because the array was
  empty, and the reader now looks at the slots by offset instead of scanning for them.
- **A submit closes the console, so the three keys sent after it are untested, not negative.** `bTyping` is 0 in
  `d1`, and `d3`/`d4` are the same receipt byte for byte: `kp_enter` and `pad=a` arrived with an empty line, and
  `if (TypedStr != "")` guards the copy.
- **The run's last step timed out.** The `pmemsave` immediately after a `pad=start` tap never got a QMP answer
  (`TimeoutError` in `smoke.py`), which left `d5` at 54 MiB and nothing known about Start. That is not a verdict
  on the key; a re-run should put a `wait=` between the tap and the dump.
- **`tools/xemu/console-state.py` gained the named layout**: `--history` prints `TypedStr` plus the populated
  slots, `--receipt TEXT` exits 0 only when the text is in a slot *and* the line is empty, and `--selftest` proves
  both on a synthetic object whose `HistoryTop` sits past slot 15.

### Execution notes (2026-11-06, a submit reads the line, and the dump taken at the watch is one step too early)

- **`ret` reads `Console.TypedStr`, and the append and the submit differ in where the copy goes.** `submit2` took
  four `Z3` stops at `EIP=0x1b300` with `ESI=0x0802db78` (`line+0xC`) and `EDI=0x0802e61c`; `findtext8`
  (`--watch-key=x`) stopped at the same `EIP` and `ESI` with `EDI=the line`. A `Z3` over two bytes at `line` cannot
  fire for a `rep movsl` that reads `[line+0xC, line+0x18)` and writes the line, so the reported register file is
  the post-execution one: the source starts at the line in both boots, and the submit's destination is a new heap
  buffer `0xAB0` above it. The previous note's "the trap is the write" is the pre-execution reading of the same
  numbers, and it is wrong.
- **A dump taken immediately after a watch is a pre-submit snapshot.** The step leaves the guest stopped at the
  instruction it trapped; the resume and the `pmemsave` that follows are milliseconds apart, so
  `submit2/after.bin` still holds `TypedStr` `Count/Max 6/6` and **no** `History` field in `0x200` decoded bytes.
  A receipt needs a `wait=` before the dump, not a longer watch.
- **A repeating key makes the terminated needle match nothing.** `submit3` reported 0 copies of `hist\0`, never
  armed the watchpoint and never sent `ret`; its own two dumps hold ` hist` + 50 `t` (`Count/Max 55/55`) and then
  104/104 four seconds later, in the same boot. `drive.py`'s watch step now falls back to the bare text and prints
  which form armed, and `tools/xemu/console-state.py` reads such a dump without guessing (`--cr3`, FString decode,
  `--selftest`).

### Execution notes (2026-11-06, the watchpoint chain runs end to end, and it names a write rather than a reader)

- **The stack works in one boot.** `drive.py --step=watch=hist --watch-key=x` dumps 128 MiB, finds the UTF-16 text,
  resolves *which* copy an object holds, translates it with `vamap.py`, arms `Z3` on the linear address and sends one
  guest key. `findtext8` stopped three times, all at `EIP=0x0001b300` with `EDI=0x07ffdb78` (the line) and
  `ESI=0x07ffdb84` (`line+0xC`). `findtext5` had armed on the first of eight copies and `findtext6` on a physical
  address; both were arming faults, and the stub was fine.
- **The copy is resolved, not guessed.** `ramscan.string_copies` accepts a copy when a four-aligned dword names its
  first character and the next two words are `Count = chars + 1 <= Max`, then names the owner from its vtable with the
  existing `owner_of` walk. On `findtext8` that answered `Console at 0x02661b00 +0x44, Count/Max 6/6` — the same
  object and offset two earlier sessions found by hand, on a third boot with a different heap.
- **A `Z3` on the line stops on the store, so the reader is still unnamed.** `EIP 0x1b300` is `FUN_0001b2c0+0x40`,
  and the bytes two before it are `f3 a5` (`rep movsl`): the stub reports the PC after the repeated instruction while
  the register file is its pre-execution state. The function is an `FString` assignment (`Count`/`Max` from `[esi+4]`,
  `call 0x45ed0`, `rep movsl` + `rep movsb`, `ret 4`) and the 12 bytes it moves land on the line while its source
  starts at `line+0xC` — outside the eight watched bytes. So the trap is the write, and this boot cannot say whether
  the stub distinguishes a read watchpoint at all.
- **7.3's next measurement is one boot, not a new instrument**: the same run with `--watch-key=ret`, because a submit
  copies `TypedStr` into `History[HistoryTop]` before clearing the line, so *something* must read it. If that also
  stops only on stores, the read channel is the interpreter's dispatch (`tools/xemu/mem.py --watch` on a `GNative`
  slot) rather than a data watchpoint.
- **The frames this run called FAIL are the noise floor.** `noise=3` measured 2,509 pixels, the threshold went to
  5,018, and `04-tab`/`05-typed`/`06-after` measured 711/1,263/762 while the dump holds the typed text. The console
  line moves ~1,000 px: assert on a crop or on RAM, never on the whole frame in gameplay.

### Execution notes (2026-11-06, the dump is physical memory — translate it and the typed line has an owner)

- **`pmemsave` writes physical memory; every address this project uses is linear.** The two differ on this build, and
  the difference is what `U15`'s negative was measuring. `tools/xemu/vamap.py` walks the guest's i386 page tables out
  of the dump itself (`CR3` from `info registers`, `0x0000f000`; PAE off, PSE on per `CR4=0x000610`), both directions,
  with `--stats`. It is validated by content rather than by itself: `--va 0x11000` → physical `0x613000` is `.text`'s
  prologue (file offset 0x1000 of `default.xbe`), and `--va 0x4d1cc2` → physical `0x67dcc2` is an `.rdata` assert path.
  The map is 137 present PDEs (63 of them 4 MiB), 93,495 mapped pages over 93,399 distinct physical pages — the image
  is scattered, so no constant offset relates the two spaces.
- **With the translation the buffer's owner is one dword away.** The typed line is physical `0x06bfcb78` in
  `findtext5`, linear `0x0802db78`; that dword occurs exactly once in 131,072 KiB, at physical `0x02661b44` — `+0x44`
  of the object at `0x02661b00` whose first word is the `Console` class table `0x0050cb80`. `+0x44` is
  `FString.Data`, `+0x48`/`+0x4c` are `Count=6`/`Max=6` (`" hist"`), and `+0x50`..`+0x74` are zero, so `History[16]` is
  empty because nothing was submitted. `findtext4` reproduces it at the same object offset with its heap `0x3d9000`
  away. The old "no pointer in 128 MiB" was a physical value searched for in a linear space.
- **The register file at the dump step answers its own question and not this one.** `findtext5` step 47, console
  drawn: `EIP=0x00020669`, `CPL=0`, `ESP=0xd0169d38`, `EBP=0x0979d700`, `EAX=0xd016a140` — a ring-0 frame on a kernel
  stack. The plan it was meant to serve ("the pointer can only be in a register or a stack frame") was wrong anyway:
  the stack is inside the dump. The address space was the missing piece.
- **xemu's stub implements read watchpoints (`Z3` → `OK`), and they must be armed on the linear address.**
  `tools/xemu/mem.py --watch ADDR[:LEN]` reports each stop's register file, the stack words that name the address and
  `EIP`, with a fake-stub `--selftest` in `make ci`. Armed on the physical `0x06a57b78` (`findtext6`) it never stopped
  in 25 s. `drive.py`'s new `watch=TEXT` step does the chain in one boot — dump, find, back up to the string's first
  character, `CR3`, translate, `Z3` — and the pair is unproven end to end until a boot finishes with it, which is the
  next run's first job.

### Execution notes (2026-11-06, the whole guest dumps inside the boot, and the typed line is in it but unowned)

- **`pmemsave` inside the boot is the instrument the stub could not be.** `--step="monitor=pmemsave 0 0x8000000
  \"<path>\""` writes all 128 MiB (134,217,728 bytes in `findtext3` and `findtext4`) and prints nothing. The
  command answers `help pmemsave`; a bare path does not -- the monitor parses it as an expression and refuses it
  (`invalid char 'h' in expression`) -- so the quotes travel inside the step value. This closes the "untested"
  line in the note above.
- **`tools/xemu/ramscan.py` is the other half**: one pattern over the whole image in 0.1 s (0.19 s with the
  interpreter), against ~17 min for the same bytes over the stub at the recorded 7.9 s/MiB. `--text` searches the
  UE2 UTF-16 form with its NUL terminator, `--ascii` the 8-bit one, `--dword` a value, and `--refs` walks every
  pointer to a hit back to the nearest class table to name the owning object. `--ref-window` (0x40) exists because
  a text pattern matches *inside* a string: the FString's `Data` names the first character, not the match.
- **Two boots, one variable.** `findtext3` and `findtext4` differ only in `$L`. `findtext3`: `01-list` 0 px (the
  pad press never took, the game stayed on the title), `console-line.py` reads `absent`, and the dump has 0 hits.
  `findtext4`: `01-list` 37,878 px PASS, `05-typed` reads `typed` (95 cyan pixels, 128x33 at +34+208), and the dump
  one step later has exactly one hit -- `0x06823b7a`, two bytes into `" hist"` at `0x06823b78`. The leading space
  is the `spc` step, so in this boot it was not the swallowed keystroke.
- **The negative that matters is about the image, not the walk.** No 4-byte-aligned dword in 128 MiB names the
  match or any even address in the 0x40 before it, and no known class table is within 0x8000 before the bytes --
  so the buffer is not a field of any object the vtable walk can name, and nothing in RAM holds a pointer to it.
  A `Data` pointer absent from the whole image can only be live in a register or in a stack frame; the next step
  is `monitor=info registers` in the same boot, which is one step and no new data.
- **The 8-bit copy is excluded for this state**: `--ascii hist` is 0 hits in the image that holds the UTF-16 copy,
  which retires the caveat the stub scan left open.
- **`drive.py`'s whole-frame threshold is the wrong witness here, again**: `findtext4` failed `02-mission`,
  `03-dismissed`, `04-tab` and `06-dumped` at 0 px while `05-typed` passed with a whole-frame change -- the mission
  loaded somewhere inside the 55 s wait and the shots before it were all the same list frame. The frame counter is
  the witness for the line, and `console-line.py` read it right on the frame the dump was taken.

### Execution notes (2026-11-06, the prompt draws on a stock disc, and the interaction list names the console default)

- **The disc was identified before the frame was interpreted**, because a drawn prompt is supposed to mean the boot
  is the `Console=GUI.ExtendedConsole` swap. It is not: `$WORK/xemu/sg1.iso` (md5 `109fff2a5a0091932f4262eefda41855`)
  carries `Console=Engine.Console` in its own `System/Default.ini`, while the swap image
  `$WORK/console-iso/game.iso` (md5 `5c47159b3e4ec558d2532a177b4e6992`) carries `GUI.ExtendedConsole`. Both are
  2,258,370,560 bytes, so size does not separate them.
- **`grep` on the image is not an identity check.** The *stock* ISO answers 1 for `Console=GUI.ExtendedConsole`,
  because a stray copy of that line sits at offset 1,881,354,812. Read the file: `tools/xemu/xiso-cat.py --iso
  <image> System/Default.ini` walks the XDVDFS tree and prints it in seconds (extract-xiso is gone from the desktop;
  `-c` would unpack 2.2 GB).
- **The stock disc draws `(> hist_`.** Sequence: `Tab`, `spc`, `stat fps`, `ret`, then `Tab`, `spc`, `hist` and stop.
  `console-line.py` reads `absent` (0 cyan px) on the post-submit frame and `typed` (82 cyan px, 128x32) on the frame
  with `hist` typed, and the crop reads `(> hist_` with the `stat fps` counter (`59 cur FPS` / `58 avg FPS`) above it.
  So a drawn line does not mean the swap, and a submit ran a command on a stock disc.
- **The census is unchanged with the prompt open**: `data=0x41e0000 num=48646 max=51886`, 45,802 objects over 295
  pages, one `Console` (`0x02344b00`, `defaultproperties` text inside it, no FString carrying text), zero
  `ExtendedConsole` (the filter is a substring match, so a subclass would have been caught), and no `Console` field in
  the controller's 0xc00 bytes.
- **The console default's own pointers are the next target**: `0x02344b00` holds `XboxViewport` at `+0x02c` and
  `InteractionMaster` `0x02406720` at `+0x030`; that object's `+0x028`..`+0x034` name `XboxClient` `0x01fb9bc0`,
  `GUIController` `0x02345500`, `Console` `0x02344b00` (the class default again) and `UIXInteraction` `0x05b10a80`.
  Whatever draws the line is in that list, not in a `Player` field.

### Execution notes (2026-11-06, the typed line is not in the pages the objects live in)

- **`objects.py` now answers "which address holds these bytes"**: `--find-text` (UTF-16LE plus the FString's NUL
  terminator, so `hist` does not match inside `History`), `--find-ascii`, `--find-dword`, and `--array-pages`,
  which derives the 64 KiB page set from the object array (295 pages / 150 runs / 18,880 KiB) instead of scanning
  the 123 MB span. Every pattern is searched in the same reads, because the stub's packet rate is the limit and a
  second pass would double the cost.
- **The scan came back empty and the hole is named**: 0 UTF-16 hits over 18,624 KiB read in 145 s, and four pages
  unreadable after four attempts each (`0x22b0000`, `0x70c0000`, `0x7230000`, `0x7570000`).
- **Read rate, measured in two regions**: 64 KiB per request costs 0.49 s (130 and 129 KiB/s); a `0x20000`
  request is refused every time, so one page per request is the ceiling and the span is ~16 min.
- **The RAM-dump route is closed after the fact**: a second client on the run's `qmp.sock` gets no greeting
  (30 s for `info status`, 300 s for `pmemsave`, no file), so a dump has to be
  `--step='monitor=pmemsave 0 0x8000000 <file>'` inside the boot -- untested, and the next measurement worth one.
- **A 70-pixel line does not clear a whole-frame threshold**: `drive.py` marked the `05-typed` step FAIL
  (1,570 px against 1,720) on the frame `console-line.py` reads `typed`. For this state, read the line counter.

### Execution notes (2026-11-06, the interaction block is four pointers and none of them holds the typed text)

- **The state was reproduced and proved from the frame first.** `intm1`: `M00` (index 10, ten d-pad presses),
  tutorial modal dismissed with `a`, one `Tab`, one spent `spc` for `bIgnoreKeys`, then `hist` and no submit.
  `console-line.py` reads `04-tab` `absent` (0 cyan px) and `05-typed` `typed` (60 cyan px, 110x32) -- the recorded
  three-state shape, where the prompt draws only once it holds text.
- **The console default's own fields, read live in seconds.** `--object 0x2344b00 --refs --classes-only` names six
  pointers in its first 0x800: `BoolProperty` at `+0x008`, `Package` `+0x018`, `Class` `+0x024`, `XboxViewport`
  `0x022fe200` at `+0x02c`, `InteractionMaster` `0x02406720` at `+0x030`.
- **The master's block is four children, named in one read** (`--object 0x2406720 --refs --object-size 0x200`):
  `XboxClient` `0x01fb9bc0` at `+0x028`, `GUIController` `0x02345500` at `+0x02c`, `Console` `0x02344b00` at
  `+0x030`, `UIXInteraction` `0x05b10a80` at `+0x034`. The `Console` slot is the *same* class default
  (`defaultproperties` window) that `--class Console` has returned in every state, so the master is not holding a
  live console for a walk to find.
- **None of the four holds the typed text.** `--fstrings` over each window reads Console 1 pair / 0 with text,
  `XboxClient` 0 of 4, `GUIController` 0 of 8, `UIXInteraction` 0 of 11. So the previous note's "whatever draws the
  line is in that list" turns into a negative: two of the four resolve to live instances (`XboxClient`,
  `UIXInteraction`), two to `defaultproperties` windows (`Console`, `GUIController`), and the typed string is in
  neither kind. Reading `History` back through this block is closed.
- **The viewport pointer is a default as well**: `--object 0x022fe200 --refs` finds two pointers in 0x400
  (`KarmaParamsCollision` at `+0x07c`, a `Function` at `+0x274`) and no console field, so the `+0x02c` the console
  default holds does not open a second route.
- **A linger goes bad silently.** Fifteen minutes and ~10 connections into the stub, `0x02344b00` (six pointers a
  few minutes earlier) and then `0x01fb9bc0` (four) both answered "no field in the first 0x800 bytes holds a guest
  pointer" while the emulator still ran: a failed read padded to zeros, which is exactly the shape that makes a
  null look measured. Every number above was taken in the first two minutes after the frame; `--object` now prints
  a `WARNING: all-zero window` line instead of letting that read pass as an empty object.

### Execution notes (2026-11-06, the console is not in the object array once it closes)

- **The array is one address, not one boot.** At a loaded `M00` with a command submitted, `objects.py --walk
  0x6c21b0` reads `data=0x041e0000 num=48646 max=51886` -- the same `data` and `max` the title screen read
  (`num=42152`), so the triple names the array and only `num` moves with level state.
- **45,802 live objects over 295 pages** (`0x970000..0x7eb0000`): 45,794 resolved to a class table, 2,844 slots null
  or implausible, four pages unreadable (262,144 bytes). Exactly one `PlayerController` (`0x01c31000`) -- so the
  class filter does find the controller, which is what makes its missing `Console` field a measurement rather than a
  filter artefact.
- **`Engine.Player` declares `Console` and the controller's first 0x800 bytes hold no pointer to one.**
  `inventory/class_properties.csv` lists `Engine,Player,Console,UObjectProperty,0x2000:Transient;,Console`, so the
  field is in the layout; the eleven object-address fields the walk reads instead are levels and volumes
  (`+0x0a0`, `+0x0ac`), `DefaultPhysicsVolume`, `ScriptedController`, `PlayerReplicationInfo`, three `Class`
  objects, a `Package`, an `ObjectProperty` and the controller itself at `+0x5e8`. `--class Console` in the same
  state returns exactly one object, the class default at `0x02344b00`, whose bytes are its `defaultproperties`
  block (`FontNames(0)="SmallFont"`, `KeyName="SGMenuButton"`).
- **So the console exists only while the prompt is up, and U15's read is a question of *when*.** Every walk so far
  -- this one included -- was taken after a submit, when `TypingClose()` has run: `Player.Console` reads null and
  there is no instance to hold a `History`. The next read has to be taken with `bTyping` set.
- **Two modes make that read cheap.** `--object ADDR [--refs] [--fstrings]` reads one object by address in seconds,
  naming each guest pointer from the target's own vtable with no array walk (which costs 3 min 29 s here); `--follow
  CLASS` with `--class`/`--refs` follows an owner's field into the instance and prints its `(Data, Count)` pairs.
  Both were exercised against a synthetic object graph and against the guest.
- **The same boot re-confirmed the submit a third time**: `04-tab` absent (0 cyan px), `05-typed` `typed`
  (109 px, 176x30), `06-ret` **absent** (0 px) -- the line gone after one `ret`, which is `TypingClose()`.
- **Keys cannot be injected after `drive.py` exits**, so the sequence has to end with the console open: a QMP client
  opened on the run's `qmp.sock` once the driver was gone connected and then timed out waiting for the greeting
  (6 s and 60 s deadlines, same result). Do not plan a post-run `sendkey`.

### Execution notes (2026-11-06, the input line is a three-state read, and `ret` submits)

- **`console-line.py` answers with a shape, not a count.** Its 20-pixel threshold called the title screen `present`
  (**22 px over 554x246**) and a menu button `present` (**87 px in an 18x8 glyph**); it now requires the drawn band to
  be line-shaped (<= 5% of the frame's height, >= 3% of its width) and separates `absent` / `prompt` (50--64 px wide,
  the empty `(>_`) / `typed` (176 px stock, 210 px `GUI.ExtendedConsole`), with `--require=typed`, `--min-text`,
  `--min-line-width` and `--max-line-height` for other resolutions. Ran over 17 frames in four sessions: every console
  shot classifies, and no menu or title frame does.
- **An empty prompt is a positive read.** The frame the last note called a cyan HUD glyph (`submit1/05-typed`, 56 px in
  64x32) is `(>_` read at 4x -- the console was open and empty, not absent. Crops at 4x of four frames read `(>_` and
  `(> stat fps_` exactly.
- **A shot lags its own keys by one step.** `submit1`: the `Tab` frame is empty, the *next* frame holds the empty
  prompt, and the frame after holds all eight characters -- although the key step in between sent nothing valid
  (`gkey=enter` is not a QEMU key name). `conhist9` renders the prompt within its 2 s wait. So an `absent` frame right
  after a key is not evidence the key was lost, and this alone explains the two sessions' contradictory frames.
- **`ret` submits.** `conhist9`: 113 px of `(> stat fps_`, 113 px through `kp_enter` (2,121 px of frame diff, the
  caret), then **0 px** and 3,053 px of frame diff after `ret` -- the input line gone, which is `TypingClose()` in the
  `IK_Enter` branch of the class's own script. `submit1`'s `ret` frame is **byte-identical** to the one before it with
  the line still drawn: the same key, not delivered. The submit exists and its delivery is not reliable.
- **The command's effect is still unread.** No FPS counter draws in `conhist9/07-ret` (420x120 top-left crop and a
  whole-frame read: O'Neill's nameplate only), though `seq13`/`seq14` drew `60 cur FPS` on this disc. Next: one walk
  that reads the `PlayerController`'s console pointer rather than filtering by class name, and takes
  `History[HistoryTop]` from it.
- **No `(> ` literal exists to name the drawer**: the byte pair is in none of the 18 packages and not in
  `default.xbe`, ASCII or UTF-16, and `Engine.Console`'s 23-function table has no `PostRender`, so the prompt is
  composed at run time and no package string can say which class renders it.

### Execution notes (2026-11-06, a loaded level still has no live `Console`, and the highlight frame is not an index read)

- **The clean run happened.** One vpad and no stale pad device, the keyboard at 75 s, presses after 137 s: the first
  `pad=a` opened `QUICK SELECT MAP` (**21,940 px**), the ten `pad=down` taps produced **0 px**, and the `pad=a` after
  them started a level (**31,222 px**). The loaded level's census is **48,647 objects over 295 pages**,
  `StaticMeshActor` **927** — the earlier M00 numbers to the object, and M00 is index 10, so the ten presses did land:
  **the highlight crop simply does not repaint in a keyboard-attached run**, and a frame is not a way to read the
  list's index. The census is.
- **`--class Console --fstrings` at that loaded level finds one object, `0x02344b00`, and it is the class default.**
  Its 0x800-byte window is a `defaultproperties` block (`FontNames(0)="SmallFont"`, `KeyName="SGMenuButton"`,
  `Images(0)=Shader'SG1_MainMenuTex.MM_high_mouseover_S'`, `FontColors(4)=(B=120,G=120,R=120,A=200)`) and the FString
  scan reads **1 pair, 0 carrying text**. A live level does not create the console: `Tab` in the same session produced
  **0 px** and `console-line.py` counted **0 cyan pixels** on the frame after it, so no console existed when the walk
  ran. The `History[16]` read is not blocked by the input plumbing any more — it is blocked by the console not being
  opened, and the frame can say so before a walk is spent.
- **Assert the prompt, then walk.** `console-line.py` answers "is a console up" on a single frame (`absent` = no
  prompt text, not "closed"); a run that walks first and looks second spends 2m21s to rediscover the CDO. Order for
  the next attempt: `Tab`, `console-line.py` on the next frame, and only on a prompt does the walk run.
- **Keys cannot come from a second shell.** `xdotool key`/`type` sent over ssh after `drive.py` had exited went to
  whatever owned focus (`TotK Recomp` here — `windowfocus --sync` on the xemu window did not take) and all three
  frames came back byte-identical. `drive.py` refuses that case with `FocusElsewhere`; a console run sends its keys
  inside its own `--step` list or not at all.






### Execution notes (2026-11-06, T3D resolves its references — 1,178,138 of 1,178,141 properties — and 5.2's old reading was stale)

- **`make t3d` now measures itself.** `tools/map-to-t3d.py --stats inventory/t3d_coverage.csv` writes one row per map
  (actors, properties, unresolved refs, bytes). One run over all 73 maps: **121,225 actors**, **1,178,141
  properties**, **3 unresolved**, **44,375,952 bytes**, in **1m26s**.
- **The "object references are written as `ref:<index>`" note was wrong by three orders of magnitude.** References
  resolve: `prop-stream.py` renders an export as `Class'Package.Name'` and an import as
  `Class'Package.Object'`. The three misses are named in `docs/T3D.md` (`Vector` in M02A decoded as a reference,
  `Max` in M15A and `StaticMesh` in M17B past the export table), and each looks like its own defect.
- **5.2 stays `[~]` for the reasons that survive**: arrays and structs are emitted by size, and the `Level` export's
  script sequence is still unread. The reference half is done, and the plan line says so now instead of carrying the
  stale claim.

### Execution notes (2026-11-06, the console's submit key comes from its source, and no key we can send produces it)

- **`Engine/Console.uc` says exactly what a submit does:** `KeyEvent` on `IK_Enter` + `IST_Press` writes
  `History[HistoryTop] = TypedStr`, runs `ConsoleCommand(Temp)`, clears `TypedStr` and calls `TypingClose()`. A
  submit is therefore observable three ways — History gains an entry, the text clears, the console closes.
- **None of that happens for `kp_enter`, `ret`, `pad=start` or `enter`.** After each, the frame still reads
  `(> stat fps_`, the console is still open, and there is no FPS readout. Letters echo (so `KeyType` is reached), and
  the one missing event is an `IK_Enter` press.
- **Two candidates, and the next instrument is the input map, not a boot**: the guest's keyboard driver does not
  translate either Return scancode to `IK_Enter`, or `PlayerInput` consumes it first — `DefUser.ini` binds
  `Enter=Use`. Read the scancode→`EInputKey` mapping (`UInput`/`PlayerInput` in the XBE) and the answer falls out
  without another game run.
- **The console thread stops here for now**, on the skill's own rule: three negatives is a research question, and the
  next step is reading a table, not spending boots. The value banked is that the console opens, toggles on `Tab`,
  draws its input line, and holds a typed command.

### Execution notes (2026-11-06, `Tab` is a toggle — and the console takes text but not a Return)

- **`Tab` toggles the console; one press is the sequence once the modal is gone.** Two runs differing only in press
  count: two `Tab`s then typing gave a frame **byte-identical** to the `Tab` frame, one `Tab` then typing gave
  **3,394 px** of drawn text. The "send `Tab` twice" recipe in the previous note was only right while M00's tutorial
  modal was up — the first press went to the modal.
- **`Tab` alone draws ~300 px**, inside the frame's own noise, so the console's state cannot be read off the `Tab`
  frame; type a character or read the object array.
- **Neither Return submits.** With `stat fps` on screen, `kp_enter` then `ret` changed 2,121 and 3,053 px (caret and
  echo) and the frame after both still reads **`(> stat fps_`** with no FPS readout. The console takes characters
  and does not take an execute key from either scancode.
- **Next keys, in order**: `pad=start` (xemu maps keyboard Return onto the pad's Start, so the guest may be expecting
  that event), then `gkey=enter`. The console is open and the command is typed; the submit is the only missing event.

### Execution notes (2026-11-06, the console opens — the tutorial modal was the blocker, and the census is not a map fingerprint)

- **The tutorial modal is why three runs saw nothing after `Tab`.** M00's panel ("Press the X button to open the
  door" … "A Continue") freezes the game: four captures over seven seconds are **byte-identical**. `pad=a` is the
  dismiss it asks for, and the next shot moves **41,294 px**.
- **The console then opens and draws its command line.** `Tab`, `Tab`, `stat fps` → a cyan **`> stat fps_`** at the
  bottom-left of a first-person frame, no modal. U15's "the console draws nothing" holds only for an *empty*
  console; text draws. The line is ~300×20 px in a 1,280×720 frame, which is why a menu-region crop never saw it.
- **`Enter` is the remaining gap**: after typing, the frame is **identical** (0 px) and no FPS readout appears, so
  the command is typed but not submitted. Next test is `kp_enter` (or `ret` twice), not another console hunt.
- **Corrected: the object census is not a map fingerprint.** The 45,802-object run recorded above as "not M00" is
  M00 — its `02-mission` frame is the lab with the same modal. Counts move with level state; read the frame.

### Execution notes (2026-11-06, a second clean console run: CDO again, and the d-pad step is what is unreliable)

- **The run was built to remove every confounder and reproduced the result.** One vpad, no stale pad, keyboard at
  75 s, presses from 137 s, `Tab` twice (the first keypress after a load is swallowed), `stat fps`, `Return`:
  `pad=a` opened the list (**21,937 px**), ten `pad=down` gave **0 px**, the select loaded a level (**21,789 px**),
  every key after it **0 px**, and the walk found **45,802 objects** with `--class Console` = **one object, the CDO**
  (FString scan: 1 pair, 0 text).
- **The level was not M00** — M00 is 48,647 objects, measured twice — so the presses did not step the list this
  time. `pad=down` is the unreliable element, the highlight crop repaints in neither case, and **the object census
  is the only evidence of which map loaded**; compare against 48,647 before reading anything into the level.
- **So the CDO finding stands only for the M00 run** (#176, 48,647). This run adds a second CDO-only walk on a map
  whose stepping was unverified, which is why it is recorded as a negative about the *instrument*, not about `Tab`.
- **Next instrument, named**: a live `UConsole` is created from `Engine.Console` when the viewport is made, so its
  absence after a load is the thing to explain. The walk already resolves the `PlayerController`; read its members
  for a pointer whose target is on the `Console` class table, instead of matching the instance's own vtable.

### Execution notes (2026-11-06, the console read has its instrument; three input traps and no PR from the first autonomous run)

- **`objects.py --fstrings` is the read.** A UE2 `string` is `(Data, Count)`, so `--strings` saw a `Console`'s
  `History` as sixteen integers. The new mode resolves every plausible pair, keeps only the ones whose target is
  NUL-terminated text, tries both widths and caps its reads. Validated at the title on a live `PlayerProfile` at
  `0x06ad8a00`: **21 pairs read, 6 carried text** (`DefaultProfile`, `StargateContent.Oneill`, `Jack O'neill`,
  `SG1_MenusTex.HUD_Oneill`).
- **Trap 1: a keyboard attached after the level loads is never read.** `device_add usb-kbd` issued once M00 was up
  left `Tab`, `space`, `stat fps` and `Return` at **0 px** with no overlay, and `--class Console` returned only the
  CDO — because no `Tab` had reached the game. Attach at 75 s, before the title.
- **Trap 2: the keys arrive and the press that matters is swallowed.** With the keyboard at 75 s, letters moved the
  map list (**17,780 px**) but the select press was lost. `missions.sh` retries a boot; `drive.py` needs the select
  press repeated inside the sequence.
- **Trap 3: a killed `drive.py` leaves its virtual pad behind.** `/proc/bus/input/devices` carried two `SG1 virtual
  pad` devices after one, xemu bound the dead one, and every `pad=` step was a no-op in an otherwise healthy run.
  Kill the emulator as `appimage_extracted…` (the AppImage wrapper's real name), and clear stale pads first.
- **The in-mission object count is a measurement of its own**: M00 → **48,647 objects over 295 pages**,
  `StaticMeshActor` 927, against 42,152 / 255 / 171 at the title.
- **The first autonomous run shipped nothing, and that is the finding worth acting on**: it used its whole budget on
  three game runs and hit the iteration cap before writing the diff. The instrument and the negatives above are its
  work, salvaged here. The loop now ships the instrument plus its negative result when a measurement will not finish,
  because a run that ends without a PR costs a boot and teaches the next run nothing.
- **Next, and it is one run**: a single vpad, the keyboard at 75 s, three select taps, then
  `objects.py --walk 0x6c21b0 --class Console --fstrings --object-size 0x800`. That closes U15 and 7.3.

### Execution notes (2026-11-06, the engine's object array is located, and objects are addressable by class)

- **`GObjObjects` is at `0x006c21b0`** and holds **42,152 objects at the title** (`data=0x041e0000`, `num=42152`,
  `max=51886`). The image cannot name it — `.data` is 2.2 MB of virtual size against 215 KB of file bytes, so the
  array is zeros in `default.xbe` — and the guest can: a `TArray`-shaped triple (`Data`, `Num`, `Max`) is a
  candidate when `Data` points into the heap, and it is evidence only when the objects it points at carry vtables
  from the image's class-table set. **64 of 64** sampled objects did; the other three heap-pointer candidates in
  `.data` scored 0. `tools/xemu/objects.py --find`.
- **The walk is a page read, not a sweep**: 42,143 objects over **255** 64 KiB pages, one RSP packet per page,
  **2m21s**. Reading the `min..max` span (the first attempt) or the same pages at 16 KiB chunks (the second) is
  what made it look hung; the stub advertises `PacketSize=0x20020`.
- **The histogram is the engine at the title**: `Function` 8,510, `ObjectProperty` 5,758, `FloatProperty` 4,402,
  `BoolProperty` 4,173, `StructProperty` 3,388, `IntProperty` 3,193, `StrProperty` 2,424, `Sound` 1,409,
  `Class` 1,004, `Package` 332, `State` 320, `LinkerLoad` 188, `StaticMeshActor` 171. `--class Player` names a
  `PlayerController`, a `PlayerInput`, a `PlayerProfile`, six `PlayerReplicationInfo`, two `PlayerChatManager` and
  two `PlayerStart` — an object-level assertion channel for the regression harness.
- **7.3's `History` read is one run away, and the console object at the title is not it.** The only object on the
  `Console` class table holds a `defaultproperties` block (`FontNames(0)="SmallFont"`, …), which reads as the class
  default object; the live console appears with a level. `drive.py` now carries `--gdb=PORT` and `--linger=SECS`,
  so a scripted run leaves the stub up for exactly that read, and `objects.py --class Console --strings` is the
  instrument. A class-table name is a name for the *vtable*, not proof of an instance's class — 179 distinct
  unnamed vtables appear in the walk, and that caveat belongs in any count taken from it.

### Execution notes (2026-11-05, `GetMapList` is found and has no cap; the fill is one read away)

- **U16's routine is `0x002fe5f0`**, and it needed no native index: the build binds an index-less `native` by name (U1),
  so the image's own `intUGUIControllerexecGetMapList` wide literal (`0x00534a4a`) names it and its log format
  `[VL:GetMapList]:%s` (`0x00538d28`) has exactly one dword reference, at `0x002fe7e4`. `ExportAt.java` on that address
  is the whole cost of getting the body.
- **The body rules out two explanations and leaves one.** `Filter` is stepped and never read (the filter box cannot work
  by construction), and there is no comparison against a constant bound in 1,256 bytes (so 41 is not a loop limit). It
  builds `*.unr` from `MapExt`, walks the enumeration source and adds a list item per hit.
- **The 41 is a prefix of the disc's order**: `Maps/` has 73 `.unr`, XDVDFS order is ASCII, index 40 is `M15B.unr` and
  41 `M15C.unr`. Prefixes come from a fill that stops, not from a filter.
- **The source global `0x005fbee4` resolves to the `Paths` globs** (`Default.ini:49-56`, 8 of them) -- and only to
  those, at the title as well as at t+20 s, so it is not the file cache and the names come from the callee at
  `0x00036cc0`.
- **The virtual read does work at the title.** The first attempt's zeros were the stub's `odd hex length from stub`
  failure being mistaken for a value: on a fresh connection `.text`, `MapExt` and the global all read correctly at every
  stage. Retry rather than conclude, and record the retry (`docs/XEMU.md`).
- The physical fallback is still a dead end (one-packet reads answer `E22`) and the emulator can lose its GPU context
  mid-run, so a read that needs the image should go through a virtual read with a retry.
- **The helpers are CRT and the enumeration is below the routine.** `0x36cc0`/`0x36ce0`/`0x36c90` are `_wcsstr`,
  `_wcscat`, `_vsnwprintf`; `_wcsstr(glob, L"*.unr")` is a literal substring test on each `Paths` glob, so `GetMapList`
  collects *paths* and the 41 names come from the virtual call at `0x2fe806` (`[[0x005bf514]+0x30]`). One read of that
  slot at the title, then `ExportAt`, is what is left.
- **That read is done: the enumerator returns all 73, and the image's chain is a decoy.** `0x2fe806` is a double
  indirection (`[[0x005bf514]] + 0x30`); the image's own values end at the 22-byte stub `0x34570` (an empty `TArray`),
  because the `.data` slot is rewritten at runtime. At the title the table is `0x4c8db8` (`FFileManagerGeneric`) and
  slot 12 is `0x24e70`, a `FindFirstFile`/`FindNextFile` loop with no bound; a breakpoint at `0x2fe809` reads back
  **73** names in directory order. The page's count settles it: **print all 73** -- the controller's array reads
  `count=73 max=76` after `QUICK SELECT MAP` opens, ASCII-sorted, `M15B` at 40 and `SGCTF-AbydosVsSGC` last. The
  41-entry table was a fifth page holding one item, and U16 closes: every campaign map is reachable by paging.
- **The second half of the campaign loads.** `tools/xemu/map-index.py` derives the page's index from the disc (73
  entries; six anchors re-derived from the guest, `--verify`), `missions.sh --name M15C` resolves it, and `--all` now
  covers 10-72. Index 41 (`M15C`) and 70 (`M21D`) both loaded on the first attempt; the remaining 30 indices are 30
  boots of harness time, not an access question.
- **The nightly's mission stage rotates instead of repeating two indices.** `map-index.py --rotate 2` walks 10-70 with
  a day-of-year start, so 2 boots a night cover all 61 campaign entries in 31 nights with nothing skipped (2 is coprime
  with 61); `SG1_MISSION_INDICES` still pins an exact list and `SG1_MISSION_COUNT` changes the per-night count. The
  range stops at 70 because 71/72 are `MainMenu` and `SGCTF-AbydosVsSGC`, which are list entries and not missions, and
  `--verify` now refuses a table where those look like campaign maps. Checked over 31 simulated nights: 61 unique
  indices, 10 through 70.
- **A mission PASS had to mean a load, not a menu.** `MainMenu.unr` is a list entry, so index 71 reported PASS on the
  MAIN MENU page: a repaint plus "not the BIOS screen" is true of a menu too. `missions.sh` now also runs
  `tools/xemu/frame-diff.py 00-title.png 02-mission.png` (2.5% of the frame must differ; measured 1.07% for the menu
  against 4.94-13.04% for a cutscene, a CTF map and a mission) and records `FAIL ... main menu`.
- **The console keeps no output to read (U15).** The configured class, `Engine.Console`, declares only `TypedStr` and
  `History[16]`; `WindowConsole.Scrollback` is an int and the only `array<string> Scrollback` is `GUI.ExtendedConsole`'s,
  which this build never instantiates. So 7.3's output channel does not exist in guest memory -- what is assertable is
  that a command was delivered (`History`) or that it had a visible effect (`stat fps`'s overlay). 7.3's remaining half
  is a scripted command path (`ACTION_ConsoleCommand.uc`), not a memory read.

### Execution notes (2026-11-03, the virtual pad drives the menus, and a campaign map loads)

- **A uinput pad on port 1 is the input path 7.2 needed**, and it took four fixes rather than one: an identity that
  collided with the *physical* pad's SDL GUID (`030081b85e0400008e02000014010000` -- the one this desktop's own
  xemu settings bind port 1 to), a mapping that has to be keyed to the pad's own GUID and handed over in
  `SDL_GAMECONTROLLERCONFIG`, `BTN_DPAD_*` keys on the device making SDL map the d-pad to buttons b11-b14 instead of the
  hats (so `pad=down` wrote an axis nobody read), and xemu merging its **base-path** settings over `-config_path` so
  auto-bind gave port 1 to the physical pad and pushed the virtual one to port 2.
- **Measured, one run, floor 405 pixels in the list's crop**: `pad=a` 20,606 (`MAIN MENU` to `SELECT PROFILE`), `pad=y`
  1,197 (to `QUICK SELECT MAP`), 31 `pad=down` 13,800, `pad=a` 31,718 -- **the map loads** -- and 45 s later 3,366 pixels
  with no assert dialog. `EIP` `0x00048cda`, `0x00224a51`, 0 of 2 in kernel, exit 0.
- **Next:** the list's arithmetic (31 presses from index 0 landed on `M12F`, not `M20A`), then `M00` from `Continue`
  with a saved profile in the EEPROM, then the same sequence as the nightly's mission-load stage.

### Execution notes (2026-10-30, the pixel assertion was passing on animation; input is now the open end)

- **The shipped "repaint" measurement was the background, not the key.** `drive.py` reported `differing pixels: 4178`
  for a `Tab` press and called it proof that input reached the guest. The title screen is a live scene behind a
  translucent panel: **two frames captured with no input at all differ by 3,525 (5 s apart) and 3,629 (4 s apart)** in
  the menu crop, so the threshold of 200 could never fail. The harness now measures that floor itself (`noise=SECS`)
  and raises its pass threshold to twice it.
- **Mission loading is a sequence, not a keystroke, and the tool takes one.** `--step` repeats: `shot=NAME` (asserted
  against the previous shot), `key=`/`type=` (window-targeted), `xkey=`/`xtype=` (XTEST), `wait=`, `activate`,
  `noise=`, and `cpu=COUNT`, which samples `EIP` over QMP in the same run — pixels cannot tell a key that was ignored
  from a guest that stopped executing. `--crop` and `--fuzz` scope the comparison to the region the input should
  change. A no-input floor of **591 pixels** in a `640x480+620+140` crop at `-fuzz 15%` is what a menu highlight has
  to beat.
- **Keyboard is mapped onto the pad by xemu itself**, so the guest needs no keyboard driver: `config_spec.yml`'s
  `keyboard_controller_scancode_map` gives `a`/`b`/`x`/`y` = face buttons, arrows = d-pad, `Return` = START,
  `Backspace` = BACK.
- **Nothing sent at the guest has changed the screen yet**, with the guest executing at every check (`EIP` in user
  space, 0 of 2 in kernel): targeted `XSendEvent` `Down` (SDL ignores synthetic events), XTEST `Down`, XTEST `Down`
  after `xdotool windowfocus --sync` (the X input focus is the xemu window), `background_input_capture = true` in the
  run's TOML, and XTEST `a` (the pad's A, which would activate the highlighted `Continue`). `windowactivate` is a
  no-op here — the compositor does not hand activation to an X client — which is the likeliest reason, and the reason
  the next attempt is not another key.
- **A virtual pad is creatable on this host**: `/dev/uinput` is writable by `james` (ACL) and `python-evdev` is
  installed — `UInput` produced `sg1-vpad` on `/dev/input/event27`. SDL reads evdev without window focus, so this is
  the route that does not depend on the compositor; what it still needs is an SDL mapping string for the device GUID
  and the matching `[input] gamepad_mappings` / `port1` entry. **U14** carries it.
- **XTEST keys were going into a running game, not the emulator.** After the runs, `xdotool getwindowfocus` named the
  focused window as a Steam game (`Northgard Battlegrounds`, `steam_app_4487060`): every XTEST `Down` and `a` was
  delivered to that window and never to xemu, and the desktop rang KDE's "input not accepted" sound, which is how the
  owner noticed. `xdotool windowfocus --sync` does move focus when called, which takes the keyboard away from whoever is
  using the machine. The harness now prints the focus owner, **refuses** an XTEST step that would type elsewhere
  (exit 2, naming the window), moves focus only on an explicit `--step=activate`, and hands focus back on exit — an
  evdev pad needs none of this, which is the second reason it is the next thing to build.
- **The guest can be given a keyboard, and the documented port is wrong for this build.** xemu's docs say the way to
  attach a keyboard is a runtime Monitor command (`device_add usb-kbd,port=1.x,id=kbd`, "3,4,1,2 for player 1,2,3,4"),
  with no GUI for it — so it lives in no config file and a GUI session leaves nothing behind. `drive.py` now issues it
  over QMP (`--step=monitor=…`). Measured: `port=1.3` is **player 1's hub** and `device_add` refuses it
  ("usb port 1.3 (bus usb-bus.1) not found (in use?)"); on the hub, one slot past the pad, it attaches —
  `Device 1.0, Port 1.3.2, … Product QEMU USB Keyboard, ID: kbd`. That is the *guest's* half of typed input (what 7.3's
  console commands need); the host half is unchanged and still U14.
- **Two harness bugs found by running it**: `proc.terminate()` leaves `AppRun` holding the HDD image, so the next run
  fails on a "write" lock that reads as a config error (xemu now runs in its own session and the group is killed), and
  a window id can die mid-sequence (a failed capture now re-finds the window once).
- **Next:** `tools/xemu/vpad.py` — a `UInput` gamepad with an SDL mapping, wired into `drive.py` as the input channel —
  then the same sequence with an assertion per menu step. 7.2 stays `[~]` until a mission loads; 7.3 (scripted console
  commands) inherits the same input path.

### Execution notes (2026-10-30, input injection is solved by QMP sendkey — the menus move)

- **`sendkey` over QMP is the input path**, and it needs no focus, no SDL and no X, so a sequence can drive the game
  while somebody is using the machine — which is exactly what `xdotool` could not do. It requires the guest keyboard:
  `device_add usb-kbd,port=1.3.2,id=kbd` (the documented `port=1.3` is player 1's hub and is refused; the pad sits at
  1.3.1, so the keyboard goes one slot past it). `drive.py` grows a `--step=gkey=KEY` for it.
- **Measured key semantics on the menus** (X input focus was on a running game the whole time, so nothing could have come
  from the host keyboard): `ret` = the pad's A — title → `SELECT PROFILE`, 18,790 pixels changed; `y` = Y — `SELECT
  PROFILE` → `QUICK SELECT MAP`, 2,867–9,753; `esc` = B — back out, 13,872. `sendkey` **without** the keyboard device
  changes 0 pixels and still reports success, which is the trap in this channel.
- **The in-game map list names the campaign maps**: `M20A`…`M21D`, beside `CTF-*`/`DM-*` multiplayer maps and
  `MainMenu`, under a `QUICK SELECT MAP` page. So 7.2's "load each mission" is reachable from inside the game, not just
  from a `.unr` dump.
- **Not yet**: moving that list's highlight. Arrows moved nothing (0–246 pixels, the noise floor), `tab` moved focus
  nowhere and opened no console, and `ret` on the page re-rendered the list (10,434) without a loaded level. Until a
  mission loads, 7.2 stays `[~]` — but the blocker is now a key in a list, not the absence of an input path.
- **Next:** the list's navigation key (try the pad's d-pad names the game uses elsewhere, `spc`, `kp_enter`, or hold a
  key longer than `sendkey`'s default 100 ms), then `ret` on `M20A` with a 60 s settle for the load, and an assertion per
  step. Then 7.3's console commands have an input path too.

### Execution notes (2026-10-31, the map list moves by page — and the harness was reporting stale frames)

- **The map list answers to `home`/`end`/`pgup`/`pgdn`, not to the arrows.** Measured on one live instance over
  `sendkey` only: `home` → index 0 (16,760 pixels), one `pgdn` → index 10 (`M00` on the top row, 14,803), `end` → the
  last entry (14,134). `down`/`up`, `left`/`right`, `kp_8`, `kp_2`, `ins`, `del`, `spc` and `tab` move nothing
  (0–351). The list's step is one *page* (ten entries), and the campaign maps are inside it.
- **The harness was reporting the frame from *before* the key.** An `import` grab taken immediately after `sendkey`
  comes back byte-identical to the pre-key frame; the next one differs by 18,790 pixels on the title. Two sessions of
  "nothing moves the list" were partly this. `drive.py` now takes every asserting capture twice, 1.5 s apart
  (`--regrab`), and a second identical key inside ~2 s is coalesced by the guest (19 pixels), so sequences space their
  keys. This is the fix that made the table above possible.
- **The list is 41 entries and `M20A` is entry 31**: `home` + 3 `pgdn` lands on 30, `end` + 1 `pgup` lands on 30.
  The difference is a single step, which the list's own code takes from `XC_PadUp`/`XC_PadDown` — i.e. the pad, i.e.
  U14. The page map is in `docs/XEMU.md` (`0`–`9`, `10`–`19`, `20`–`29`, `31`–`40`).
- **`A` (`ret`) does not load the highlighted map** (13 pixels over 65 s, guest executing; double-tap, no change).
  `QuickLoadMapTempPage` inherits `bButtonA=True` and `URL="?Game=StargateGame.StoryModeGame"` from
  `SinglePlayerChapterSelect` (class defaults read out of the package), so `LoadMap()` is reachable *in principle* —
  but `GUIVertList.InternalOnXControllerEvent` consumes `XC_A` for the focused list first. Either the list swallows it
  or `Travel` refuses the URL; one run with the pad on port 1, or an assert dialog, tells them apart.
- **Next:** U14's virtual pad (`tools/xemu/vpad.py`) for the single step and for a clean `XC_A`/`XC_B`, then `ret` on
  `M20A` with a 60 s settle and one screenshot per mission. 7.2 stays `[~]` until a level loads; 7.3's blocker is now
  the missing console, not the input path.

### Execution notes (2026-10-30, the script side is measurable end to end, and the nightly actually runs)

- **U13 is closed.** A `Function` payload's script starts **16 bytes past `FriendlyName`**, proved three ways by
  `tools/uscript-dis.py --start`: the payload floor (25 bytes, spikes at 26 = `16+3+7` and 28 = `16+3+9`; a 12-byte run
  would floor at 22, where nothing appears), the walk's depth from that offset, and the dword 16 bytes past the name
  not being a size (larger than the script in 9,298 payloads, equal in 2,052, never smaller — the loader uses
  `SerialSize`).
- **The header's encoded part is fixed**: five values for a `Function`, four for a `Class`, the last being
  `FriendlyName`, and the count does not move with the child count (`--header`). `v1` is `Super`, `v2` is `Next`, `v4`
  is `Children`; the zero positions are unidentified. This killed the `Children`-chain model, whose +1/+2/+3 residuals
  were the compact encoding's widths.
- **Five operand widths were four fixed bytes and are the archive's compact index** (groups 0, 13, 14, 15, 19). The
  corpus went from **3,444 to 11,602 of 11,687** scripts walking clean (29% → 99.3%); `--usage` is the census,
  `inventory/opcode_usage.csv`. The 85 that still refuse are a per-opcode tail with nothing common behind them.
- **The encoding indexes two different tables**, and only the opcode says which: objects for the variable, cast and
  final-call tokens (`FinalFunction` names a `Function` 5,424 of 5,424), the name table for `VirtualFunction`,
  `GlobalFunction` and `NameConst` (`--resolve`).
- **The call sites are a table**: 5,424 static edges and 13,507 dynamic ones, `inventory/script_calls.csv`, `--calls`.
- **The comparator exists** (`--compare`): a package against itself compares 3,352 of 3,821 shared functions identical
  (the rest have no script), and a one-byte mutation inside an operand moves L3 by one while L2 stays. 3.8's remaining
  half is the oracle — producing a rebuild needs `ucc`.
- **The decompiler gaps are counted** (`docs/DECOMPILER.md`, `inventory/decompiler_gaps.csv`): state blocks and latent
  declarations are complete; `replication` is 63 of 142 blocks and that is the format's limit — instrumenting
  `FormatReplication` shows every one of the 79 classes that lose a block has `net=0`, i.e. no property carries
  `PropertyFlag.Net`, so there is nothing to reconstruct.
- **The nightly runs.** Six plumbing failures were fixed on the first real run (scripts committed non-executable, the
  manifest counting BIOS images as dump files, a stale artifact, a census stage that seeded nothing, `set -u` on an
  optional variable, and a stage that ran `VAR=value` as a command) — 19 of 19 after that, and **20 of 20 with
  `--xemu`**, the boot test in 140 s with six `EIP` samples all in user space. `docs/REGRESSION.md` records the two
  prerequisites that cost a run each: an X display, and a free HDD image.
- **Next:** 7.2's input injection — pixel-level assertion and mission loading — is now the only route to
  gameplay-level regression. Everything else open is blocked on a tool (`ucc`, `umodel`) or a decision (1.5, 8.5);
  the script and bytecode tracks are at the limit of what is verifiable without a compiler.

### Execution notes (2026-10-28, two build targets, and the first one produces something)

- **6.4's two targets exist and one of them ships a build.** `tools/build.sh --target hybrid|full` (`make
  rebuild-hybrid` / `make rebuild-full`); stages 1-4 are shared. `hybrid` places **18 assembled packages** — the
  writer's own output through a new `pkg-write --rebuild --write DIR`, not a copy of the input — beside
  `Packages.md5`, and `tools/build-manifest.py` writes `manifest.csv` (size + SHA-256 next to the source's) and
  `missions.csv` (the 18 campaign rows, joined from `inventory/campaign_matrix.csv`). The manifest stage fails
  unless all 18 are `identical=1`, so a build cannot report PASS on a tree it assembled wrongly.
- **What the hybrid target is not**: with no `ucc` (3.6) its script half is the merged source tree beside the
  packages (2,319 `.uc` files, asserted as a stage), not packages compiled from it. The "which missions boot in
  each" column of 6.4 needs an HDD image, so `boot` stays SKIP and `missions.csv` is the column it will attach to.
- **The children-chain test the last note named was run, and it is out.** Predicting the script's start as `Super` +
  one reference per parameter + `FriendlyName` + 16 is exact for 181 of the 2,171 payloads with a unique size dword
  (mostly off by +1..+3); adding a leading name index reaches 904 with residuals spread both ways, and the walk
  from the predicted offset tiles in 3,030 payloads against 3,545 at `+2` — no peak, so the walker cannot serve as
  the locator either. `docs/UNKNOWNS.md` U13 carries the two routes that remain (the image's `UStruct::Deserialize`,
  or pinning one start from a source-visible first token).
- **Next:** a route to the script start that does not involve guessing the header, or content work (3.10/8.3) as the
  next finishable unit.

### Execution notes (2026-10-28, the payload's tail is confirmed by content; the header width is not a fixed count)

- **The field order written down from `UFunction::Serialize` now has a content check.** `tools/uscript-dis.py
  --payloads` takes the whole `System` directory (12,472 `Function` payloads) and compares the `u16` at `payload_end-7`
  — `payload_end-9` when `Net` — with the index the function's *script* declares in `native(1234)`: **791 of 797**
  joinable payloads carry exactly it, 5 carry a token where the script declares none, 1 the other way, and **0
  disagree in both**; `FunctionFlags` matches **790 of 797**. The `-payloads` mode now also lists the exceptions
  (`actor.demoplaysound` and four `PlayerController` client RPCs carry flags the script does not declare) and prints
  a per-package table with corpus totals, which is what a total in a doc should be quoted from.
- **The scriptless half is now named as such**: 11,379 payloads carry the epilogue 7 bytes from the end, 337 at 9
  (all `Net`), and **756** at neither — those are the native functions, so `natives.csv`'s 797 joinable rows and the
  756 are the same population seen from two sides.
- **U13's remaining question got narrower, not answered.** The size field *should* sit immediately before the
  script, and a unique dword does equal the distance to the epilogue in 2,166 of 12,472 — but a header of *N* compact
  indexes plus the 16 fixed bytes fits 596 of those at two leading references, 22 at three and 618 at four, so no
  fixed count explains it. The variable part is most likely the `Children` chain (one reference per parameter), and
  the next test is bounded and named in `docs/UNKNOWNS.md`: predict the start from the export table's parameter
  count and require the walk to end on the epilogue.
- **Next:** run that children-chain test; then `replication` blocks and state code, the rest of 3.5.

### Execution notes (2026-10-28, the locator checks tag types, and one measured variant is not shipped)

- **The 11 residue values were a locator finding, and the fix is a scoring term.** A candidate is chosen by the
  fraction of its property *names* the class declares; nothing asked whether a tag's type is one the class's own
  property record allows, so a parse starting inside the record in front of the block could win. `KConeLimit`'s chosen
  candidate typed `CollisionHeight` (a `Float` property) as a 16-byte `Float` and `PhysicsVolume` (an `Object`) as a
  `Float`. `tools/class-defaults.py` now carries `TYPE_AGREEMENT` and counts contradictions per candidate; `choose()`
  sorts `(declared fraction, bad == 0, count, -trailer)`.
- **The map had to be measured, not assumed**: a `Class<X>` property serialises as an **object reference** here — 734
  tags in otherwise-clean blocks carry type `Object` for a `UClassProperty` — so `UClassProperty` accepts
  `{Class, Object}`. Assuming `Class` alone demoted 419 clean blocks and cost 3,800 properties, which is what the first
  run of the change looked like: 2,141 "clean" blocks and 12,787 properties, i.e. worse everywhere.
- **Result**: residue 11 → **4**, clean blocks **1,666 → 1,664**, properties **15,530 → 15,521**, rendered
  **15,517 (99.97%)**, subobject properties 15,802 → **15,790**. Two classes lose their block rather than emit one
  that contradicts their own record (`StargateMenus.SelectPawnPanel`, `SelectTeamPanel`); six of the seven named
  classes are clean, and the residue is now one `array<SpeechReplacement>` plus three delegate tags.
- **The aggressive variant is measured and deliberately not shipped**: ranking `bad == 0` *first* promotes **535
  withheld classes** to clean blocks (2,201 of 2,319, 16,764 properties, 272 unrendered values). Those are exactly
  the classes whose candidate held undeclared package names, and nothing here checks that the shorter block it picks
  instead is complete — so it is written up as the open question, not shipped as coverage.
- `inventory/compile_readiness.csv` and `docs/COMPILE.md` move with the block set (8 rows), since a class without a
  fully declared block is not compile-ready; both are regenerated here.
- **Next:** settle the 535 (a per-class audit of the chosen block, or the runtime reader of the trailing table), then
  `replication` blocks and state code, which are the rest of 3.5.

### Execution notes (2026-10-28, an array of structs is one tagged block per element)

- **The last residue was the reader's shape, not the format.** `docs/CLASS_DEFAULTS.md` had counted 18 arrays and 12
  structs as "element types the tiling test refuses". They are ordinary nested tagged blocks: each *element* of an
  `array<Struct>` ends at its own `None`, and the reader required the whole value region to end there, so only a
  one-element array could ever decode. `tools/defaults-emit.py` now decodes elements one at a time
  (`render_tagged_struct(..., exact=False)`) and the array loop still has to land exactly on the tag's declared size.
- **30 of the 39 render**: arrays unrendered 18 → **2**, structs unrendered 12 → **0**, array elements 1,435 →
  **1,502**, nested structs 207 → **219**, properties 15,491 → **15,519 of 15,530 (99.93%)**. Subobjects gain most
  (15,010 → **15,802 of 15,810**) because a particle system's own defaults are full of struct arrays.
- **`InterpCurve` came with them, and needed two more corrections.** Its `Points` member is an array *inside* a
  struct, and (a) `inventory/class_properties.csv` covers class-owned properties only, so nothing in the tree names
  that inner type — the tag stream is self-describing, so the elements are read from it and tiling is the check;
  (b) an array inside a struct is written inline and positionally (`Points=((InVal=..,OutVal=..),…)`), per
  `UDefaultProperty`'s `WithinStruct` flag, where the code had been printing its `(index, text)` pairs as a Python
  list. Spot-checked against the bytes: `Face(0)=(MeshName=sg1_anim.Tokra_Generic,…,MeshSectionIndex=2)`.
- **The 11 that remain are a different defect and are now measured as one**: in six classes the located block types
  a declared `Float` as a 16-byte `Float`, an `Object` as a `Float`, a `Name` as a `Vector`. The locator scores
  candidates by names only, so it can accept a start inside a preceding native blob even when a type-agreeing
  candidate exists lower in the ranking. Named per class in the doc, not guessed at.
- **Status moved in two places**: default properties rendered 99.75% → **99.93%**, subobject defaults 94.94% →
  **99.95%**; `inventory/class_defaults_emit.csv` regenerates byte for byte on a second run.
- **Next:** make the locator's scoring type-aware (tag type must agree with the declared kind) and re-measure the
  1,666/2,319 block count with it — that is the task the last 11 belong to, and it is deliberately not in this PR.

### Execution notes (2026-10-27, every artifact now has a stage)

- **The audit found nine artifacts with no target.** `docs/ARTIFACTS.md` says of itself "an artifact with no target
  is a gap, not a detail", and nine rows read *no target runs it*: `campaign_matrix`, `content_census`,
  `dialogue_by_mission`, `map_parse`, `package_check`, `package_parse`, `property_types`, `token_map`,
  `xbe_game_modules`. None of them could be regenerated by a command, so none could be checked.
- **Eight got one**: `make campaign-matrix content-census dialogue-index property-types token-map game-modules
  package-check maps-parse`. The ninth (`package_parse.csv`) was already written by `make inventory` through
  Sg1Tool's `inventory` verb; the index simply could not see it, so `tools/inventory-index.py` now resolves a
  `.cs` writer to its verb (`InventoryCommand.cs` → `inventory`, `MapsCommand.cs` → `maps`) instead of looking for a
  file name in a recipe. `docs/ARTIFACTS.md` regenerates to **0 gaps**.
- **One is dump-free and joins CI**: `campaign-matrix` reads four committed CSVs, so `make ci` now regenerates it into
  scratch and diffs it byte for byte. The rest are nightly stages (`tools/nightly.sh`), two of which refuse to run
  without their input (`token-map` without `ORACLE_DIR`, `game-modules`/`property-types` without `$WORK/native`)
  rather than publish a number from a missing input.
- **Two were verified on the real dump**: `dialogue-index` (1,364 WAVs, 14 missions, 29 speakers) and `package-check`
  both reproduce their committed CSVs exactly. The other four stay documented gates, not claims.

### Execution notes (2026-10-27, the save/load surface is readable without reconstructing anything)

- **U5's first question is answered from the shipped text.** The packages carry their own `ScriptText`, so "which path
  calls `SavePackage`" never needed 3.10: `tools/savegame-paths.py` (`inventory/savegame_paths.csv`, 16 rows) shows both
  writes coming from `GameProfile` (`StartNewMatch` 160, `ContinueSinglePlayerGame` 191) through `GameInfo.SavePackage`,
  both loads issued by `PlayerController` as a **console command** (`RestartCheckpoint` 1002 with the
  `c_<mission>_<checkpoint>.sgs` composition, `LoadGame` 3593), and `GameInfo.PostLogin` as the resume path that calls
  `LoadSaveGame(SaveGameFileName)` when `bIsSaveGame` is set.
- **Eligibility is opt-in**: `Object.MarkForSaveGame` is called from `Pawn.PossessedBy`, `KActor.PreSaveGame`,
  `PlayerReplicationInfo.PreSaveGame`, `UnrealTeamInfo.PreSaveGame`, `GravCrane.PostNetBeginPlay` and
  `GravCraneLifter.OnMove` — four of six through the engine's own `PreSaveGame` hook, which fits the exporter's actor
  set being generic rather than bespoke.
- **Where the native half lives, measured**: the `.sgs` literals are name-table strings with no `push` xrefs, but the
  wide `LoadSaveGameInfo` string is referenced by one function, `0x001226a0` (7,486 bytes), in the same `0x0012xxxx`
  region as the package-check readers — the save/load/check neighbourhood is one place.

### Execution notes (2026-10-27, the LIPSinc module is 19 functions and nine of them name themselves)

- **The runtime side is located.** The five `*LIPSincAnim` natives carry no index, but the module logs through 43
  `[LIPSinc]:` strings and the image still carries `Engine\Src\LIPSincSupport.cpp` as an assert path.
  `tools/lipsinc-runtime.py` (`make lipsinc-runtime`, `inventory/lipsinc_runtime.csv`, `--check` in `make ci`)
  attributes them to **19 functions / 23,255 bytes** in `0x000b24f0`–`0x000ba750` plus three in the actor path.
- **Nine name themselves** — `LIPSincInitLevel` (`0xba750`), `LIPSincInitGame`, `LIPSincShutdownLevel`,
  `LIPSincShutdownGame`, `LIPSincLoadDBFromDisk`, `LIPSincReadAnimFile` (`0x8340`: `Read Track %s` and the LTF
  version/validity errors), `LIPSincReadControllerFile` (`0x7260`: `Parsed %s`, `Attempt to import an obsolete .LBP
  file!`), `LIPSincLinkBones`, `LIPSincPlayAnim`. The other ten carry only pose/transform warnings, so they are
  located and deliberately unnamed.
- **What it says about U4's records**: `LIPSincInitLevel` enumerates `L"*.lad"` and hands each file to those two
  readers, and `ValuesAtTime, m_nEndTime = %i, time = %f` (`0xb24f0`) says the container is per-bone **key lists with
  an end time** — which is consistent with the 32-byte stride and the `-1.0f` sentinel measured in the previous note,
  and with file size tracking clip length rather than channel count.
- **The rename is deliberately not in this PR**: the symbol map only changes in a Ghidra run, so the names are
  evidence for that run, not a hand-edit of a generated artifact. The CSV is the list.

### Execution notes (2026-10-27, the LIPSinc containers are strided records and the phoneme maps are text)

- **U4 narrows to field meanings.** `tools/lipsinc-index.py` (`make lipsinc-index`, `--check` in `make ci`, three
  artifacts) measures the `.lad` payload instead of describing it as opaque: after the channel table a **10-byte
  prelude** (`532`, `0`, `0x21500`/`0x21300`, `0`) and then a **32-byte-strided record stream** — the `-1.0f` at a
  record's bytes 24–27 recurs at exactly 32-byte steps (352 of 424 records in the smallest clip, no exception in the
  run) — ending in three length-prefixed names (`BN-Head`, `undefined`, `undefined`). A record is 12 bytes of three
  small signed float32, 12 bytes of three near-zero float32, `-1.0f` and `2`.
- **The residue is the story, not a rounding error**: seven of the eight files do not end on a stride boundary
  (0–28 bytes over), so the last record or the trailer's prefix is shaped differently. `inventory/lipsinc_lad.csv`
  carries that per file and `docs/formats/lipsinc-lad.md` names it as the open half of U4.
- **The `.map` siblings are text and they are copies.** `Mappings/*.map` are INI files —
  `[PhonemeToSpeechTargetMapping]`, `NumSpeechTargets=15`, **43 phonemes**, weights only from `{0, 0.2, 0.5, 0.7,
  0.85}` — and **all four carry identical weights (0 of 645 entries differ)**, so the per-character viseme sets are
  copies and the character differences live in the `.lad` controller packs. `inventory/lipsinc_phonemes.csv` has the
  2,580 weights; the fifteen targets are named in the files' comment header.

### Execution notes (2026-10-26, every function's flags come out of its payload, so the network surface is a census)

- **The tail work pays off immediately.** `UFunction::Serialize`'s flags field (the previous note) is enough to read
  `FunctionFlags` for **every** function in every package, which no committed artifact had: `tools/function-flags.py`
  (`make function-flags`, `inventory/function_flags.csv`, `--check` in `make ci`) writes **12,472 functions over 1,568
  classes in 16 packages**. `Fire.u` and `UnrealEd.u` declare no `Function` export at all.
- **The tail rule validates against a second source**: **795 of 797** functions in `inventory/natives.csv` agree on
  their flags; the two that do not are `UPreview.Created` and `UPreview.SetRenderInfo`, i.e. the class U3 was already
  about, so that is a reader disagreement and not a payload one.
- **The 756-payload residue from the previous note is explained, not written off.** 751 of them have no script at all
  (no `'G'` marker, so no `04 0b 47` epilogue) and all 756 carry `Native` — a body-less function is exactly the case
  `UStruct::Serialize` skips the marker for. The last 5 are Net RPCs whose scripts end `0b 47` instead of `04 0b 47`
  (`Engine.Actor.DemoPlaySound` and four `PlayerController` client/server pairs); their flags resolve at `end-6` as
  the Net rule predicts, and why their scripts are two bytes shorter is the one thing here left unexplained.
- **The network surface is the finding** (`docs/systems/NETWORK.md`): **342 `Net`** functions of which **279 are
  `NetReliable`** (81.6%), **2,016 `Simulated`**, 433 `Exec`, 795 `Native`. `Engine.PlayerController` alone holds
  **125 of the 342 RPCs** (36.5%) while `StargateContent` holds **643 of 2,016** simulated functions (35% of that
  package against 17% of Engine) — so **RPCs are engine-side and client-side prediction is content-side**, and 2,016
  predicted functions against 342 RPCs is a design built for a few players, not a deathmatch. `NetServer` /
  `NetClient` are never set, so RPC direction is not in the flags.
- **Replicated state is the engine's**: **318 of 11,496** declared properties carry `Net`, led by `Engine.Actor` 56,
  `Engine.Pawn` 35, `GameReplicationInfo` 34, `PlayerReplicationInfo` 31, with the fork adding `StargatePawn` 14.
  The build's property vocabulary has **no `RepNotify` and no `RepRetry`**, which is the format-level reason the
  content layer leans on `Simulated` functions rather than notifies.

### Execution notes (2026-10-26, a `UFunction` payload's tail is 7 bytes — 9 for a network function — and the size field is in front of the script)

- **The tail is read out of `UFunction::Serialize` (`0x00039f10`, 283 bytes) and it is four fields**: after
  `UStruct::Serialize`, a `u16` at `+0x84` (the native token), a `u8` at `+0x88` (operator precedence), the `u32`
  `FunctionFlags` at `+0x80`, and a `u16` `RepOffset` at `+0x86` **only when `FunctionFlags & 0x40` (Net)**. The three
  fields a `UFunction` otherwise has — `paramsSize` (`+0x8a`), `paramsCount` (`+0x89`), `returnValueOffset` (`+0x8c`)
  — are behind `if (Arch.Version < 0x40)` gates and this build is **version 130**, so they are not serialized at all;
  the body recomputes them from the `Children` chain on load. That is why earlier sessions kept finding a 7-byte tail
  where UELib's field list promised 12: the reader and the writer agree, the *fields* are gated off.
- **The measurement that confirms it, with no exceptions:** `04 0b 47` (Return, Nothing, and `47` = the `'G'` marker
  `UStruct::Serialize` appends to an uncompressed script) sits at `payload_end-10..end-8` in **3,197** of `Engine.u`'s
  3,842 payloads with the `Net` flag set in **0** of them, and at `payload_end-12..end-10` in **201** with the `Net`
  flag set in **all 201**. The trailer length is predicted by a flag, so this half of U13 is closed;
  `python3 tools/uscript-dis.py --payloads System/Engine.u` reproduces every count.
- **The size field is real but not general yet**: a 4-byte size immediately before the script, with the table walking
  the script exactly, is found for **804** payloads (offsets 19..24, which is where the variable-width header lands).
  Those scripts are 2 to ~30 bytes, so this proves the *layout*, not the *width table* — say that rather than
  implying the disassembler has been validated on real bodies.
- **Two negatives worth not repeating.** The walk cannot locate a script by running to the boundary: for 3,623 of
  3,826 payloads more than one start reaches `end-7`, and a walk started at a guessed header offset desynchronises
  several hundred bytes later (a property of the start, not evidence about the table). And a size field found *by
  value* is not the rule: for **444** payloads the epilogue is at neither position, and for 3,038 no dword among the
  first 48 offsets equals the script's length. Next route is `LinkerLoad::Serialize` (`0x000515d0`), which consumes
  these same fields on the load side.

### Execution notes (2026-10-26, the walker's dispatch is 72 opcodes in 31 groups, and every group's operands are measured)

- **The dispatch is two tables, not one.** Opcode `0x00..0x47` indexes a **72-byte map at `0x39ec0`**, whose values
  name one of **31 handler groups**; the group indexes the jump table at `0x39e44` (31 entries, all inside the walker,
  and `0x39e44 + 31*4 == 0x39ec0`). The previous note read the jump table as opcode-indexed, which is why it reported
  "31 opcodes `0x00..0x1e`": the count was right, the index was the group number. `inventory/opcode_table.csv`
  replaces `inventory/opcode_dispatch.csv` (`make opcode-table`), and `docs/NATIVE_VM.md` is corrected in place.
- **Every group's operand stream is now measured**, not inferred from UE2 folklore: 31 patterns read out of the
  handler bodies, each handler's address in the CSV so the instructions can be re-read. `bytes:4` for
  local/instance/default variables and object constants, `expr,expr` for the `Let`/`ArrayElement` family,
  `bytes:4,parms` for virtual/global/final calls, `labeltable` for `0x0c`, NUL-terminated strings for `0x1f`/`0x34`,
  `expr,expr,expr,expr` for `New`, and thirteen opcodes with no operand at all. **35 of 256 opcodes are refused**:
  eleven in the map (`0x03`, `0x15`, `0x2b`, `0x35`, `0x3a..0x3f`, `0x46`) and `0x48..0x5f` by range.
- **The pinned UELib token map is the independent check**, and it agrees on the shapes that were not used to build the
  table (`New` = four children, `Iterator` = expression + code offset, `DebugInfo` = three int32 + a byte,
  `EndOfScript` = no operands). Where it disagrees, this build is the narrower one: no `LineNumber` (`0x15`), no
  `RangeConst` (`0x35`), no `ReturnNothing` (`0x3a`), no delegate tokens (`0x3b..0x3f`) — a format-level statement
  that these packages did not come from the `BuildGeneration.UE2` compiler branch UELib special-cases.
- **U13 moved without closing.** `tools/uscript-dis.py --payloads System/Engine.u`: `FunctionFlags` is the dword at
  `payload_end-4` (439 of 444 joinable to `inventory/natives.csv`), the script's epilogue `04 0b 47` sits at
  `payload_end-10..end-8` in 3,197 of 3,842 payloads, a size dword in front of the script is **not** there for longer
  scripts (937 hits, 518 of them the epilogue-only body), and a token walk that lands exactly on the boundary leaves
  3,623 of 3,826 payloads with more than one candidate start. The next route is `LinkerLoad`'s `Serialize`
  (`0x000515d0`) and the store to `[reg+0x4c]`, not another payload-shape guess.
- **The disassembler's gate is real but small**: `--selftest` (in `make ci`) splits eleven streams and honours four
  refusals, including the three-byte epilogue that most shipped functions end with. It has **not** been run over a
  long shipped script, because none is located yet — say that rather than implying L2 coverage.

### Execution notes (2026-10-26, the walker's table is 31 opcodes, and above them it is range checks)

- **The dispatch is measured**: `0x398e0` seeks to the script buffer in the object (`+0x48` bytes, `+0x4c` length), reads
  one opcode byte, then either runs a nested walk until it returns `0x16` (`EX_EndFunctionParms`) for opcodes above
  `0x5f`/`0x6f`, or switches through a table at **`0x00039e44`** with **31 entries, `0x00..0x1e`, one handler each**,
  every handler inside the walker. Entry `0x1f` onward is `.rdata` (`0xcccccccc`, then ASCII) — the table is *not*
  `0x100` entries, and reading it as such yields convincing nonsense. `make opcode-dispatch`,
  `inventory/opcode_dispatch.csv`.
- **Not claimed**: the handlers' operand widths. They are inside the 31 separate bodies, and inferring them from UE2
  docs would be exactly the guessing this thread has now failed at three times. Reading the bodies is the next step.
- **Two primitives nailed down** by the same decompilation: `0x00037110` is `operator<<(int32)` — 4 raw bytes through
  archive vtable `+4`, no compact encoding — and `0x00038dc0` is `UField::Serialize`, which serialises `Next` (`+0x28`)
  through archive vtable `+0x18`, the object-reference method. That is the concrete reason the compact-index payload
  layout could not work, now stated as a fact about the archive rather than a suspicion.

### Execution notes (2026-10-26, the script walker is 0x398e0, and the payload widths belong to the archive)

- **The `+0xa0` virtual is answered**: `Struct`, `Function` and `Class` all point at **`0x000398e0`** (1,373 bytes,
  `UnClass.cpp`), and 327 of 492 classes have that slot at all — 134 distinct targets, lopsided (117 share
  `0x00020f10`, 61 `0x000171c0`), which reads as inherited engine implementations rather than per-class overrides.
  `inventory/class_serialize_slots.csv` now carries `script_walk` with the symbol it resolves to.
- **A bug worth recording**: the first version read 64 slots from every table, so tables shorter than 40 slots had
  their "walker" read out of whatever `.rdata` held next — it reported `0x0d` and `0x31` as code. The read is now
  bounded by each table's declared slot count (`inventory/xbe_class_tables.csv`), and the tool takes its section
  boundaries from the XBE header rather than from a Ghidra byproduct, which is also why it failed in a clean worktree.
- **The third payload-layout attempt is a measured negative**: using the field order from `UStruct::Serialize`
  (`Super`, `Children`, `FriendlyName`, line, text position, size, script) with compact indices validates for **0 of
  3,931** `Engine.u` functions. That is consistent with the reader — those fields go through the *archive's*
  object-reference and name methods, not through a property block's compact index — so the order is right and the
  widths are the archive's. `FUN_000370e0` / `FUN_00037110` are the primitives to read next, then the walker's jump
  table, which is the opcode table 3.7 needs.

### Execution notes (2026-10-26, slot 11 is Serialize — and UStruct's body validates scripts by walking them)

- **The class tables are vtables and slot 11 is `Serialize`.** Comparing the 492 per-class tables against their
  parents leaves five differing slots (3, 9, 10, 11, 23); slot 3 turned out to be the deleting destructor
  (`param & 1` → operator delete), and slot 11's targets name themselves: `Struct` `0x392b0`, `State` `0x394c0`,
  `Function` `0x39f10`, `Class` `0x3a800`, `LinkerLoad` `0x515d0`, `LinkerSave` `0x50050`. **82 distinct overrides**
  over 492 classes, `make serialize-slots`, `inventory/class_serialize_slots.csv`.
- **`UStruct::Serialize` is the reader 3.7 needed**, and it says what the two payload-bytes attempts could not: the
  script is **a size plus that many bytes**, with a **`'G'` marker** appended on save when absent, and the engine then
  **walks the script token by token** (virtual at vtable `+0xa0`) and asserts
  `"Script serialization mismatch: Got %i, expected %i"`. The token table is therefore the same dispatch the
  interpreter uses, and the stored size is a check rather than a guess.
- **Two things that now look different**: a script may be stored compressed (the `'G'` marker), which would explain a
  payload that does not decode as bytecode; and the `assert`-driven decompilation route (find the class table, read
  slot 11, decompile) is faster than analysing payload shapes — three routes tried, this is the one that produced a
  reading of the actual reader.
- **Next for 3.7**: identify the `+0xa0` walker per class (it is a vtable slot too, so the same table read gives it),
  then the opcode table falls out of that function's dispatch instead of from UELib's approximation.

### Execution notes (2026-10-26, 7.2 is blocked on a HDD image, not on the harness)

- **A third configuration, a third kernel idle**: xemu's own freshly created HDD image, retail flash, XISO mounted —
  30 of 30 CPU samples from t+12 s to t+102 s sit in the kernel's idle loop. Combined with the two earlier runs that
  is *image resident, never entered* under three configurations.
- **`EIP=0x8001b03x`, `CPL=0` is the kernel idling** and is now written down as such: sampling it for longer is not
  evidence of progress, which is what made the earlier "the title runs" note hard to reconcile with these runs.
- **The hypothesis that fits all three**: the launch path is HDD-oriented, so a kernel with no dashboard and no
  installed game loads the disc's XBE (the memory channel sees it) and never enters it. Settling it needs a HDD image
  with a dashboard or the game installed — an asset this project does not have, not a flag or a tool.
- **So the xemu line stops here** until such an image exists. What the project gained from it: `tools/xemu/mem.py`
  (physical memory), `tools/xemu/bootcheck.py` (three verdicts), the XISO recipe, and the two host facts that made
  unattended runs possible.

### Execution notes (2026-10-26, two more negatives on the title: the flash is not it, and the framebuffer was not found)

- **The debug flash changes nothing that matters.** Under `bios_debug_4627.bin` the title image is still resident (title
  string at `0x001be190` instead of `0x000e0190`, so the load path does differ) and the CPU is still in the kernel:
  10 of 10 samples `CPL=0`. The XISO builder's warning about retail signature checks is therefore not the visible
  blocker.
- **The framebuffer was hunted and not found.** With `screendump` missing, the idea was to decode the screen out of
  RAM. A 16 MiB dump of `0x3000000..0x4000000` has no UI-like block — every 1 MiB block scores the same (300–373
  sampled colours, ~93k/128k non-zero bytes), which is heap. That fits "no title, nothing drawn", but it does not
  prove the screen is blank; the NV2A surface registers at `0xFD000000` are what would settle the framebuffer address.
- **Both negatives are recorded where the next session looks first** (`docs/XEMU.md`), with the three candidates worth
  trying in order: a fresh formattable HDD image (the kernel may refuse to run a title with no dashboard/save state),
  the NV2A registers, and sampling inside the first 60 s rather than after it.

### Execution notes (2026-10-26, the XISO mounts and the title is resident — but the CPU stays in the kernel)

- **The disc is no longer a blocker.** `tools/xemu/build-xiso.sh` produced **2,258,370,560 bytes** (exactly the size
  the doc's table predicted) in ~3 minutes, and xemu accepts it as `dvd_path`. The kernel loads the title's
  `default.xbe` from it: the UTF-16 title string appears **once at physical `0x000e0190`** in a 6 MiB scan, so the
  image is resident near `0xE0000`.
- **The CPU was not in the title.** Five samples over ~180 s all read `EIP=0x8001b030`, `CPL=0`. That contradicts this
  doc's earlier "the title runs" note; the candidates are a different HDD/EEPROM state in that run, or a title that
  starts and returns inside the window — neither is measured, and the entry says so instead of picking one.
- **`tools/xemu/bootcheck.py`** makes the distinction a command: *executing* (`CPL=3` with `EIP` inside the image),
  *resident but not running*, *no image at all*. It composes the two mechanisms already proven (QMP register sampling,
  the GDB stub's physical reads); its own first end-to-end run hit xemu's `GLib: g_source_destroy` assertion after a
  `pkill -9` of a previous instance — a harness flake, recorded rather than papered over.
- **Next for 7.2**: a fresh HDD/EEPROM state (the current image is the 4.5 MB one from the NAS) and a longer sampling
  window, then `bootcheck.py` as the assertion; `Continue` and mission loading remain behind that.

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

- [x] 8.1 `docs/BUILDING.md`: end-to-end reproduction, toolchain versions, expected hashes.
      Done: §3 is the ordered pipeline with each stage's needs and outputs, §4 maps every count to the document that
      states it, §5 is what the gate can and cannot prove (with its real output), §6 is the release recipe. The third
      piece, expected hashes, is new: `inventory/artifact_hashes.csv` records the SHA-256 and size of all **76**
      committed artifacts under `inventory/` and `symbols/`, `make artifact-hashes` writes it and
      `python3 tools/artifact-hashes.py --check` verifies it in `make ci` — failing on a changed hash, a vanished
      file, or an artifact the manifest does not mention. Verified with two manufactured failures (a forged hash and
      a dropped row), each reported by name. Toolchain versions stay in `tools/versions.lock` with the honest
      UNPINNED entries for `umodel`/`xemu`, which nothing here has run.
- [x] 8.2 `docs/ARCHITECTURE.md`: engine fork overview, VM, script-to-native mapping, level flow.
      Done: the three layers and their sizes, the address space (code, 462 KB of class tables, the name table) with the
      measured finding that code is ordered per translation unit rather than per module, the object model down to the
      five addresses that form the script/native boundary, the property/tag format that doubles as the save format, the
      content layer with the mission/multiplayer split, and an explicit "not measured" for the init call graph. §8 maps
      each claim to the check that reproduces it.
- [~] 8.3 Per-system write-ups: AI (`AIScript`/`AIMsgManager`), vehicles, weapons, mission objectives, checkpoints,
      UI, audio (LIPSinc), network. **Network done** (`docs/systems/NETWORK.md`): every function's `FunctionFlags` is
      readable from its export payload now, so the replication surface is a census rather than a reading — 12,472
      functions over 1,568 classes in 16 packages, of which **342 `Net`** (279 reliable), **2,016 `Simulated`**, 433
      `Exec`, 795 `Native`, and **318 of 11,496** declared properties replicated. `Engine.PlayerController` holds 125
      of the 342 RPCs; `StargateContent` holds 643 of the 2,016 simulated functions; the build has no `RepNotify`
      vocabulary at all. **Checkpoints done** (`docs/systems/CHECKPOINTS.md`): the nine `.sgs` files hold 1,739 property
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
      **The reference graph is measured** (`tools/weapon-links.py`, `inventory/weapon_links.csv`): 315 class
      references over 176 of the 231 classes, the firing chain joined at the ammo class, and the effects
      wiring read rather than left open.
      **The effective chain is measured** (`inventory/weapon_effective.csv`): a reference is a setting,
      not a value -- 62 of the 69 weapons carry an ammo class (53 set it, 9 inherit it) and 7 carry none,
      42 reach a projectile (41 set on the ammo, 1 inherited) and 60 reach a damage type (39 from the
      projectile, 21 from the ammo's fallback). The "a weapon that names no ammo class spawns its shot in
      script" reading therefore applies to those 7, not to every weapon that does not set one.
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
      blocked on the same package reader as 5.2/4.7. **Voice selection is a per-pawn default** (`docs/systems/AUDIO.md`, `tools/voice-packs.py`,
      `inventory/voice_packs.csv`, `make voice-packs`): of 61 classes under `StargatePawn` 17 carry a
      `SGVoicePackClass` value and 5 inherit one — 13 packs — 38 inherit none and never speak, and the
      six-bucket `VoiceSelector` lottery runs only for the SG-1 four, because `SG1VoicePack` is the only
      subclass of the extended pack.
      **What a per-sound `VoiceDesc` holds is measured** (`tools/voice-descs.py`,
      `inventory/voice_descs.csv`): 1125 descriptors over 273 sets, all 11 fields -- `Tone` in 189 multi-tone
      sets (the alternate takes), a message-type *string* rather than the int, and no probability anywhere;
      only the SG-1 four carry the array at all.
      8.3 is complete: checkpoints, AI, vehicles, weapons, objectives, UI, network,
      audio.
      Note: `FORK_SURFACE.md` should gain the GUI example when next touched.
- [~] 8.4 Milestone releases: (a) inventory+tooling, (b) scripts decompiled, (c) scripts compile, (d) bytecode match,
      (e) playable rebuild, (f) asset-complete. **Milestone A is met and releasable**: `docs/RELEASES.md` defines all six
      with exit criteria and measured status, and `tools/release-notes.py` generates the release body from the committed
      artifacts (so two releases diff and a stale figure fails rather than misleads). Cutting the tag is a maintainer
      action after merge; B stays partial because the oracle only agrees on 204 of 291 classes, and C–F have not
      started.
- [ ] 8.5 **Blocked on a decision, not on code**: publishing the tooling separately means a new repository and a
      licence/ownership call. Publish tooling separately (package scripts, native-symbol generator) as generally useful
      UE2/Xbox RE tools.

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
