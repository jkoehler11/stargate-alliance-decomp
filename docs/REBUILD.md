# The rebuild pipeline: six stages proven, three missing, and the compiler in between

PLAN 6.1 asks for `tools/build.sh` → compile → repack → place. This is that script, and the interesting
result is not that it runs: it is *which* stages run. Six run today — the four container and input stages, plus
`write-check-data` and `package-check` on the target that has packages to check — and three are blocked behind a
compiler this project does not have. The blocked ones fail visibly with a reason instead of being discovered at
the end.

    tools/build.sh --dry-run                                  # every stage that needs no compiler
    SG1_DUMP=~/sg1/dump tools/build.sh                        # + the dump-side stages (target `full`)
    SG1_DUMP=~/sg1/dump tools/build.sh --target hybrid        # the packages a hybrid build carries + a manifest
    SG1_DUMP=~/sg1/dump UCC=~/tools/ucc/ucc tools/build.sh    # + compile (no such ucc exists: see below)

One directory per run under `build/rebuild/<UTC>/`, one log per stage, `summary.txt` at the top, nothing committed.

## Stage by stage

| # | stage | what it proves | today |
|---|---|---|---|
| 1 | `writer-relay` | every package re-lays through the writer's own placement path and comes back byte-identical (18 of 18) | **proven** |
| 2 | `rebuild-identity` | each package is assembled from header + tables + payloads and matches the original byte for byte (**100 of 100** — 18 `.u`, 9 `.sgs` checkpoints, 73 `.unr` maps; 100% of bytes accounted for, zero gaps in every file) | **proven** |
| 3 | `source-tree` | the 2,319 classes' own source is written out as one `.uc` per class | **proven** |
| 4 | `merge-defaults` | each class's `defaultproperties` block is merged into that tree (1,664 of 2,319 classes) | **proven** |
| 5 | `compile` | a compiler turns that tree into `.u` payloads | **missing**: no `ucc` matching package version 130 / licensee 4 |
| 6 | `repack` | generated payloads are placed by the writer | **blocked** on 5 |
| 7 | `write-check-data` + `package-check` | the build **writes** its own `Packages.md5` from the packages it places, then checks them against the cook's records | **proven on the hybrid target**: **18 records** written for 18 placed packages (17 of them records the cook's file already has, 1 new: `UPreview.u`), read back 18 of 18 (`tools/package-check-emit.py`, byte-identical reproduction of the shipped 20,111-byte file); **17 of 17** placed records still reproduce against the source (`tools/package-check-quick.py`; the quick transform `0x00121cf0` hashes the header dwords plus 8 bytes per name entry, `docs/PACKAGE_CHECK.md`). `UPreview.u` is the one `System/*.u` package with no record; a target with generated payloads is blocked on 5-6 |
| 8 | `boot` | the rebuilt image boots | **blocked** on 5-7; the harness exists (`tools/xemu/smoke.py`, `docs/XEMU.md`) |

So 6.1 is *half built*: the container is a non-risk (stages 1-2), the input tree is complete for two thirds of the
classes (stages 3-4), and everything from payload generation onward waits on a compiler that this project does not
have. That is the same conclusion `docs/PATH.md` reaches from the estimate side.

## The two targets (PLAN 6.4)

`--target hybrid|full` selects what the run is *for*; stages 1-4 are shared.

| target | definition | what it does today |
|---|---|---|
| `hybrid` | our scripts + the original assets | places **18 assembled packages** into `workdir/System` (the writer's own output, not a copy — `--rebuild --write`), **writes** `Packages.md5` beside them from those packages (`write-check-data`), writes `manifest.csv` + `missions.csv`, and checks the merged source tree is 2,319 files. `manifest.csv` must say `identical=1` for all 18 or the stage fails, and runs `package-check` over the placed tree, so a record that no longer matches fails the build |
| `full` | our scripts + re-extracted assets | the same four stages plus `compile`/`repack`/`boot`, which report SKIP with their reason (no `ucc`), and `package-check`, which waits for a tree this target does not produce yet |

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

Stage 2 covers every container kind rather than the game packages alone: 18 `.u`, the nine `.sgs` checkpoints and
the 73 `.unr` maps all reassemble byte for byte through the same writer (`make pkg-rebuild-all`). The checkpoints
only joined it once the save package's export entry was read — an export whose serial size is 0 writes no serial
offset, which is 316 of the 1,776 save exports and a byte lost at each of them (`docs/formats/sgs-checkpoint.md`).

## The shader compiles are part of the rebuild

The image carries 60 shader programs as assembly source — 19 `vs.1.1`, 3 `xvs.1.1`, 9 `ps.1.1`,
12 `xps.1.1`, 17 `ps.1.4` — plus
`#pragma screenspace`, one `#include` and two `#define`, and the SDK's own `// Xbox Shader Assembler %s`
banner (`docs/NATIVE_NOTES.md`, "The shader toolchain"). They are not in any package: the title compiles them
at runtime through `PreProcessor` → `d3dxasm_parse` → `XGRAPHICS::D3DTokensToUCode` → the
`VS_GRAPH_OPTIMIZER::Instruction` graph pass, and **every one of the 60 is referenced** — 56 from per-program
wrappers that call `ShaderCompileVertex` or `ShaderCompilePixel`, 4 from one function's `mov` immediates
(`inventory/shader_programs.csv`). The compile is **not** in the boot window: 1,200 EIP samples across the first
102 s, with rendering active, hit neither dispatcher nor `_XGAssembleShader` (`docs/NATIVE_NOTES.md`, "Runtime
sampling"). It is a map-load cost, and that is where a rebuild must reproduce it — **seven programs, and the same seven on
each of three missions sampled** (`M00`, `M04B`, `M12F`, counted at the assembler), not all 60, and not a different
set per map.

That makes shaders the one part of the script/toolchain surface a rebuild cannot skip by reusing shipped
assets: either the compile path is reproduced, or the microcode for the programs a map actually selects is produced once and
shipped in their place. `tools/build.sh` does neither today, and the difference is invisible in a script-only
check — the packages would verify and the first draw would fail.

## Where files go: the game's own deploy scripts (2026-11-19)

`System/` carries four `.bat` files that are not game data but the **build's deploy step**: `copybuild.bat` (69 lines),
`copycontent.bat` (21), `copyini.bat` (27) and `copyscript.bat` (5). They drive the Xbox SDK's `xbmkdir`/`xbcp`/`xbdel`
against `xe:\\stargate\\`, so they state the shipped layout exactly — which is what a rebuilt tree has to match.

**The tree.**

| path under `xe:\stargate\` | what lands there |
|---|---|
| `Animations/`, `Maps/`, `StaticMeshes/`, `Sounds/`, `Textures/`, `Resources/`, `Speech/` | whole directories, copied recursively |
| `Music/` | `*.wma` **and** `*.wav`, copied as two separate patterns |
| root | `default.xbe`, `xbox.bin`, `splash.raw` |
| `System/` | `*.u`, `*.int`, `Default.ini`, `DefUser.ini`, `uw.ini`, `Stats.ini`, `Packages.md5` |
| `System/tweak/` | `misc/`, `pawns/`, `vehicles/`, `weapons/`, `surfaces/`, `mp/` |
| `System/LIPSincData/` | the `.lad`/`.dat`/`.map` set, copied recursively |

**Three details that matter and are easy to miss.**

1. **`Packages.md5` is part of the build, not a side file.** `copybuild.bat` and `copyscript.bat` both copy it into
   `System/`, next to the packages it describes — so the integrity manifest ships with the game and a rebuilt package
   must be reflected there or the shipped tree is internally inconsistent.
2. **The deploy deletes stale config first.** `xbdel` removes `UnrealEngine2.ini`, `Uw.ini`, `user.ini` and `Stats.ini`
   from `System/` *before* copying `Default.ini`, `DefUser.ini`, `uw.ini` and `Stats.ini` — i.e. per-user configs are
   expected to exist at run time and are deliberately not shipped, only the defaults.
3. **`Music` ships two formats.** The scripts copy `*.wma` and `*.wav` as separate lines rather than one wildcard, so the
   directory legitimately holds both; a rebuild that assumes one format will silently drop half the music.

**What this changes for the rebuild.** PLAN 6/7's placement question has an answer that does not need guessing: a rebuilt
`.u` goes in `System/` beside `Packages.md5`, `default.xbe` and `xbox.bin` stay at the root, `splash.raw` stays at the root,
and the content directories keep their names and case. `copyscript.bat` exists precisely because the script half is the
part that gets rebuilt repeatedly — it copies only `*.u`, `*.int`, `xbox.bin` and `Packages.md5`, which is the minimum a
script-only rebuild has to replace.

**Limits.** These are the *build's* scripts, so they describe what the developers deployed to a dev kit (`xe:`) rather
than the retail disc image; the retail layout is the same shape but the scripts do not prove the disc used it verbatim.
Nothing here says what `xbox.bin` is (it is copied, never produced, by these files).

## `Packages.md5` is a package, and the game reads it as a wide string (2026-11-19)

The shipping layout above says a rebuilt package "must be reflected" in `System/Packages.md5`. That claim is now measured,
and the file is not what its name suggests.

**It is a UE2 package.** `System/Packages.md5` begins `C1 83 2A 9E` — the package magic — and is 20,111 bytes holding
**`PackageCheckInfo0` … `PackageCheckInfo167`**: 168 entries, each carrying **two** md5 strings (168 uppercase, 168
lowercase), plus the class names `Core`, `Engine` and `Package`. It is not a text manifest and cannot be edited by
appending a line.

**The engine does open it — as UTF-16, and the references are in code.** `"Packages.md5"` occurs **twice in `default.xbe`
encoded as UTF-16LE and zero times as ASCII**, and `"Packages"` occurs eight times wide against zero narrow. The wider of
the two occurrences is at `0x00504470` and carries **nine references** from code; the second, at `0x005086d6`, has none.
So an ASCII-only scan reports the file as unreferenced — the conclusion this page first drew before checking the wide
encoding — while the truth is nine call sites. The integrity check is real, and it covers **168** packages, far more than
the 18 `.u` files.

**Reference counts need the runtime to settle them.** `splash.raw` is also named twice as UTF-16LE (`0x004c8656`,
`0x004d1326`) and has **zero** direct xrefs from either — and this page first concluded from that count that the game never
opens it, calling it launcher material. A breakpoint on `_CreateFileA@28` shows the opposite: `d:\splash.raw` is opened
**three times at boot**, plus once as `z:\splash.raw`, in the first seconds before anything else. It is a 640×480×3 raw
image (921,600 bytes) that the build itself puts on screen, and the zero count meant the name is composed or reached
through a table, not that it is unused.

**Consequences for a rebuild.**

1. **Refreshing an entry means regenerating a package**, not editing text: the manifest is loaded by the engine's package
   machinery, so it has to stay a well-formed package. PLAN 3.6's package writer is the tool that can produce it.
2. **Two hashes per entry, and the target is not a visible string.** No package filename appears anywhere in the manifest —
   only the class names — so the entries are keyed some other way (name-table index or order), and mapping entry → file is
   not something this reading established.
3. **A rebuilt tree that leaves `Packages.md5` stale is detectable at run time**, because the game verifies 168 packages,
   not just the scripts.

**Limits.** The mapping from `PackageCheckInfoN` to a package file is unidentified; the meaning of the upper/lower hash pair
is not established (candidate: the manifest's own hash beside the package's, which would explain the pairing); and which
168 packages are covered is not enumerated.

## The boot chain: `Default.ini` is read, `UW.ini` is not, and the missing map is named anyway (2026-11-19)

`System/UW.ini` (745 lines) is the game's own engine configuration; `Default.ini` (430) is the SDK default and repeats the
same four keys, so the values below hold whether the shipped tree reads one or the other.

| section | key | value |
|---|---|---|
| `[URL]` | `Map` | **`Index.unr`** |
| `[URL]` | `LocalMap` | `Entry.unr` |
| `[URL]` | `MapExt` | `unr` |
| `[Engine.Engine]` | `Console` | `Engine.Console` |
| `[Engine.Engine]` | `GameEngine` | `Engine.GameEngine` |
| `[Engine.Engine]` | `DefaultGame` | **`StargateGame.StoryModeGame`** |
| `[Engine.GameEngine]` | `ServerPackages` | `GamePlay`, `StargateEffects`, `StargateGame` |
| `[Engine.GameEngine]` | `ServerActors` | `IpDrv.MasterServerUplink` |

**The discrepancy worth knowing about.** `Maps/` holds 73 `.unr` files and **none of them is `Index.unr`**; `Entry.unr` is
there (337,241 bytes). Neither name appears in `default.xbe` — both occur only in `UW.ini` and `UnrealEngine2.ini` — so the
startup map is config data, and the config names a map the shipped tree does not contain. Either the front end is reached
through `LocalMap` (or a launch-time URL), or this default is stale relative to what the retail disc boots; the config
alone cannot say which.

**`ServerPackages` is a dependency list.** `GamePlay`, `StargateEffects` and `StargateGame` are the packages the engine
loads for a server-side game, so they are the ones a rebuild must keep loadable even when it replaces script content — a
useful short list to test against instead of "all 18 `.u` files".

**What would settle the boot question.** The engine's own startup path reads `[URL] Map` before any `LoadMap`, so a
breakpoint on the map-name reader with the config value in hand shows whether `Index.unr` is consumed or skipped — the
same instrument `docs/XEMU.md` documents for the mission harness, pointed one step earlier in the boot.

**Limits.** These are config values, not behaviour: the keys are read, but which one wins at startup is exactly the open
part. `Entry.unr`'s role (front end, intro, or a fallback) is not established here either.

### What is resident twelve seconds in

A RAM dump taken **12 s after boot** (`drive.py --step=watch=`, `tools/xemu/ramscan.py` over the 128 MB image) settles
which of these names the running build actually carries. Counting terminated UTF-16 strings:

| text | copies in RAM at t = 12 s |
|---|---|
| `Index.unr` | **3** |
| `Entry.unr` | **3** |
| `StoryModeGame` | **3** |
| `Default.ini` | **3** |
| `Packages.md5` | **2** |
| `UW.ini` | **none** |

Three readings, in order of strength:

1. **The config values are consumed, not ignored.** `Map=Index.unr`, `LocalMap=Entry.unr` and
   `DefaultGame=StoryModeGame` are all resident three times over, so the engine has parsed those keys and is holding the
   values. The map it names is absent from the disc, and the build still reaches the menu — so the failure is handled
   rather than fatal.
2. **The name that is resident is `Default.ini`, and `UW.ini` does not appear at all.** That answers the question this
   page opened with: the ini the engine reads at boot is `Default.ini`, the standard UE2 file, and `UW.ini` — the one the
   deploy scripts copy into `System/` — is not present in memory. It is the player-facing config the build does not read
   when no user copy exists, not the boot configuration the engine loads.
3. **The package manifest is already open.** `Packages.md5` is resident twice at 12 s, which is consistent with the nine
   code references to it and with the check running early.

**Limits.** Presence is not proof of an open: a filename can be resident without ever having been passed to the file
system, so "`Default.ini` was read" rests on the value strings being parsed alongside it rather than on a traced open.
The reader of `Index.unr` was **not** caught — the watchpoint armed on one of the three copies and saw no read in twenty
seconds, and the read may well have happened before the dump. Tracing the open itself is the instrument that would close
both gaps; nothing here did that.

## Bake or reproduce: the two costs, with the numbers in hand

The requirement is settled — a mission compiles seven of the 60 programs, through a 50-class assembler-and-optimiser
suite (`docs/NATIVE_NOTES.md`, "How many of the 60 programs a map compiles" and "Inside the assembler"). What is not
settled is which side a rebuilt build takes, and the two sides are not close in size:

| | bake the microcode | reproduce the compile path |
|---|---|---|
| what it takes | one instrumented campaign run (`missions.sh --all` with the `_XGAssembleShader` breakpoint) to get the union of programs any mission compiles, then a table of program → microcode | reimplementing `PreProcessor`, the yacc grammar in `CD3DXAssembler`, the encoder, `DeadCodeStripper`, `Renamer`, `Swizzle`, `InstructionGraph`'s scheduler and temp allocator, `PixelShaderAssembler`, `TLEngineSim` |
| size | N programs × a few hundred bytes of microcode | **50 classes, 464 methods, 84,387 bytes** of assembler and optimiser |
| pass criterion | the blobs equal what the original driver received | byte-identical microcode from a re-implementation |
| main risk | a mission outside the measured set needs a program the table lacks — mitigated by logging an unknown program at runtime instead of failing | any policy difference (scheduling order, renaming choice, swizzle preference) changes the output, and the pass criterion is exact |
| unblocked after | the union run, which is one automated campaign | a faithful backend, which is the largest single item in Phase 6 |

**The recommendation this measurement supports is bake-first.** The set a mission needs is small and enumerable,
the alternative is a compiler backend whose output must match byte for byte, and nothing in `PLAN.md` requires the
rebuilt build to compile shaders the original did not ship — which is the only case that would demand the
reimplementation. Reproducing the path remains the right answer if that requirement ever appears, and the interior
mapping above is what that work would start from.

This is a project decision rather than a technical one, so it belongs here as a costed pair rather than as a
unilateral choice: the next session should run the union measurement, and the decision is James's once N is known.

## What the script does not do

* **It never writes into the repository.** Everything lands under `build/` (git-ignored) or `$WORK`.
* **It does not touch the dump.** Stages read the dump, never modify it; `docs/LEGAL.md` and `AGENTS.md` apply.
* **It is not a packaging tool.** `--dry-run` still runs stages 1-4, because they are cheap and they are the ones
  a change is likely to break; `UCC` is only consulted for stage 5.

## Reproducing each claim

```
make rebuild              # == tools/build.sh --dry-run with SG1_DUMP
make pkg-relay            # stage 1 alone
make pkg-rebuild          # stage 2 alone, on the 18 game packages
make pkg-rebuild-all      # stage 2 over every container kind: .u, .sgs checkpoints and .unr maps
make extract-script       # stage 3 alone
make defaults-tree        # stage 4 alone
```
