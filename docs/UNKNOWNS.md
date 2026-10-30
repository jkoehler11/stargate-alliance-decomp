# Unknowns

Things this project does not know yet, with the evidence that raised the question and what would
settle it. A guess does not belong here either — only a gap that is being tracked.

## U1. 523 of the 797 `native()` declarations serialise no index

**Observed.** `inventory/natives.csv` records 797 native functions across 18 packages. 274 carry an
explicit index, all 274 distinct (`inventory/native_names.csv`), and 273 of those indices have one
declarer inside the runtime packages. The other 523 carry `native_token` 0 — for example
`Engine.Actor.KWake`, `Engine.Canvas.Clear`, `GUI.GUIComponent.ActualWidth` and every `IpDrv`
native.

**Why 0 is the package's value, not a parser default.** UELib reads `NativeToken` unconditionally
from the serialised `UFunction` (`src/Core/Classes/UFunction.cs`, `NativeToken = _Buffer.ReadUShort()`
in `Deserialize`), and it reads the right value for the 274 indexed declarations in the same
packages, so the offset is correct for build 130/4. The zeros are what the build shipped.

**Why it matters.** A declaration with no index claims no dispatch slot, so once the runtime table is
read (U7) those 523 still cannot be named from it — they are reached some other way, or not through
it at all.

**Hypotheses, none confirmed.**

1. They are declared `native` without a number and the compiler resolved their call sites to a
   per-function C++ pointer (`EX_FinalFunction` with the `UFunction` reference) instead of a
   `GNatives` index.
2. They are the script-to-C++ (`ucpp`) classes: Perception compiled hot script classes to C++, and
   the declaration survives as an interface with no VM entry (the project already expects this for
   `Pawn` and `ShadowProjector`, PLAN risk 4).
3. This build writes 0 into the index field for declarations that did not say `native(N)`, and the
   real binding is by name at engine start.

**What would settle it.** Disassemble a script function that calls one of them (`Actor.KWake` is a
good target: `Engine.Actor` has dozens of callers) and read the emitted opcode and operand — an
index opcode settles hypothesis 3, a function reference points at 1 or 2. Cross-check with 4.7
(the `.ucpp` bodies) if a C++ implementation exists for the same class.

**Cross-checked 2026-10-26 against the other half.** `tools/native-reconcile.py` (`inventory/native_reconcile.csv`,
`docs/NATIVE_CLASSES.md`) compares these records with the `native()` declarations in the shipped script: 256
declarations carry an explicit index and every one of them agrees with its record's token — 0 disagreements. So the
count in the header of this entry needs one refinement: **274 records carry a token, 256 declarations spell one out.**
The 18-record difference is not a parser artefact and is measured below (U3).

**Partially answered 2026-10-23.** Hypothesis 3 is what the build does. The routine that fills
`UFunction::Func` (`0x0003a030`, `Core\Src\UnClass.cpp`) has an explicit branch for
`FUNC_Native` with `iNative == 0`: it formats a name with `"int%sexec%s"`, looks it up, and stores the
first dword of the result as `Func`. So an index-less `native()` declaration claims no dispatch slot
because it is bound by *name*, not by index — which is also why no amount of reading `GNatives` will
name them (`docs/NATIVE_VM.md`, *The body of a script function*).

**Still open.** What the two `%s` arguments contain, whether the lookup succeeds in a debug build
(the string suggests a debug symbol lookup, which would mean the binding only exists when a map/PDB is
present), and therefore whether the shipped prototype resolves those functions at all.

**Status.** Partially answered; the index question (U1's original wording) is settled, the binding
route for the 523 is measured, the name construction is not.

## U2. Three `Engine.Actor` natives declare indices beyond the table — closed

**Closed 2026-10-23.** They are not beyond the table. `GNatives` has `EX_Max` = 4,096 entries
(`docs/NATIVE_VM.md`); 3969–3971 are ordinary indices inside it. The bound of 994 that made them look
impossible came from the pointer array that was wrongly identified as the table.

**Observed.** `Actor.MoveSmooth`, `Actor.SetPhysics` and `Actor.AutonomousPhysics` declare indices
3969, 3970 and 3971. Under the bound of 994 entries reported for the table at `0x00598e00`, they were
past its end, and U2 asked whether they belonged to a different numbering space.

**Resolution.** The table they were measured against was not the dispatch table (it is the CRT
initialiser list). The real bound is `EX_Max` = 4,096, read from the interpreter's own assert, so all
three indices are ordinary slots. Nothing about them is anomalous after all.

**Status.** Closed 2026-10-23. What remains unknown about them is U7, not their index.

## U3. `UPreviewMapListClient.Created` claims index 2

**Observed.** The only declarer of index 2 is `UPreview.UPreviewMapListClient.Created`. The claim was
written when slot 2 of the array at `0x00598e00` was null; that array is the CRT initialiser list, not
`GNatives`, so the null-slot half of the observation no longer holds. `UPreview` is an editor-side
package that the shipped game does not load, and its small indices remain suspicious on their own.

**Hypotheses.** Editor packages number their natives inside the package rather than in the engine's
global index space, so their small indices are unrelated to the runtime table.

**What would settle it.** Reading the runtime table (U7) and checking whether slot 2 is used at all,
or the same test on a second editor package with indexed natives (`UnrealEd` and `Editor` have none
with an index here, which is consistent with the hypothesis but does not prove it).

**Status.** Open, low priority. `Editor`, `UPreview` and `UnrealEd` are excluded from naming
(`tools/native-names.py`), so nothing depends on the answer.


**Measured 2026-10-26.** The class is one of exactly **two** where the record carries a token and the shipped text
does not: `Core.Object` has 160 token-carrying records against 143 indexed declarations (17 more), and
`UPreview.UPreviewMapListClient` has 1 against 0. So `Created`'s index 2 is not a stray value — it is the compiler
assigning an index to a declaration that does not spell one out, and the same happens 17 times in `Core.Object`,
mostly for the per-signature operator records (`Concat_StrStr`, `EqualEqual_ObjectObject`, …) the compiler
synthesises. The practical consequence for the native work: **the record's token is authoritative and the text's
index is not exhaustive**, so a rebuild must read indices from the packages, never from the source.

Still open: which 17 of `Object`'s records are compiler-synthesised and which are hand-declared without an index —
the class's own text does not distinguish them.

## U4. The `.lad` payload layout is not established

**Observed.** All 8 files share a header (`u32 2`, `u32 2`, `u8` channel count, then that many
length-prefixed bone names) which ends at `0x181` for 21 channels and `0x160` for 19
(`docs/formats/lipsinc-lad.md`). The payload after the table is float data: it opens with `532` and a
second word whose second byte equals the channel count (`0x21500` for 21, `0x21300` for 19), and the
last 128 bytes of every file end in `-1.0f` sentinels and `undefined` name strings. File size tracks
clip length, not channel count, so it is per-frame curve data.

**Why it is not parsed yet.** Nothing in the script reads it: the five `Engine.Actor`
`*LIPSincAnim` natives carry no serialised index, so the loader is engine-side. Guessing a record
layout from one clip would produce exactly the plausible-looking wrong output this project bans.

**What would settle it.** The middleware's own format notes (LIPSinc authoring tool), or the native
loader from PLAN 4.8. Two cheap experiments in the meantime: compare a clip whose speech waveform is
short with the `Controllers/` pack for the same character (the difference is the per-frame data), and
test whether the `0x21300`/`0x21500` word is a count of visemes or frames (both files with the same
channel count share the value exactly, so it is not per-file).

**Narrowed 2026-10-27: the container and the stride are measured, the fields are not.** `tools/lipsinc-index.py`
(`make lipsinc-index`, `inventory/lipsinc_lad.csv`, `inventory/lipsinc_phonemes.csv`) establishes:

* after the channel table a **10-byte prelude** (`532`, `0`, `0x21500`/`0x21300`, `0`), then a **32-byte-strided
  record stream** — the `-1.0f` at a record's bytes 24–27 recurs at exactly 32-byte steps in `grey.lad`,
  `sg1_crew_jackson.lad` and `Controllers/sg1_crew_tealc.lad` (352 of 424 records in the smallest clip, no
  exceptions in the run) — ending in a block of three names (`BN-Head`, `undefined`, `undefined`);
* a record is 12 bytes of three small signed float32, 12 bytes of three near-zero float32, `-1.0f`, and a `2`;
* **seven of the eight files do not end on a stride boundary** (0–28 bytes over), so the last record or the
  trailer's prefix has a different shape — that residue is the open question, not a rounding error;
* the `.map` siblings are **plain text**: `[PhonemeToSpeechTargetMapping]`, `NumSpeechTargets=15`, 43 phonemes,
  and **all four maps carry identical weights**, so character differentiation is in the `.lad` packs.

What is still open: what the three floats mean (position? rotation?), whether the near-zero triple is padding, and
the trailer's exact length. The route is unchanged — the LIPSinc authoring tool's notes, or the native loader from
4.8 — but the file is no longer opaque, and `--check` in `make ci` keeps the measurement honest.

**Correction, and one more measured step (same day, later).** An earlier revision of this entry and of
`docs/formats/lipsinc-lad.md` described the whole `.lad` body as 32-byte-strided records with a small remainder. That
is wrong, and the comparison with the channel table is what shows it: the stride holds for **exactly one record per
channel** — a 21- or 19-entry bind pose whose first entry is the same `(3.0464, 8.6571, 10.3889)` in every file — and
nowhere after it. `inventory/lipsinc_pose.csv` records the block per file (it starts 10 bytes after the channel table,
i.e. at 395 or 362) and `--check` enforces that it never exceeds the channel count and that the scan reaches the end
of the file. The animation payload that follows is 96–99% of every file: **32-byte-strided runs, not a uniform
array** — 1 of 8 payloads is a whole number of 32-byte records, and the block each record carries recurs at gaps of 32,
37, 64, 128 and 192, so headers or longer records punctuate the runs (37 = five bytes more than the common case). Its
key times and values remain unread, and the questions are now "what does a record hold" and "what makes one 37 bytes". So U4 has narrowed twice in one day: the container is known to a bind pose plus an
unread animation payload, and what is left is a keyframe decoder, not a container.

**The runtime side is located too (same session).** The loader is native but not anonymous: the module logs through 43
`[LIPSinc]:` strings and the image carries `Engine\Src\LIPSincSupport.cpp` as an assert path.
`tools/image-strings.py --topic lipsinc` (`inventory/lipsinc_runtime.csv`) attributes 19 functions to those strings, and nine name
themselves — `LIPSincInitLevel` (`0xba750`), `LIPSincInitGame`, `LIPSincShutdownLevel`, `LIPSincShutdownGame`,
`LIPSincLoadDBFromDisk`, `LIPSincReadAnimFile` (`0x8340`, the *track* reader: `Read Track %s`, the LTF version
errors), `LIPSincReadControllerFile` (`0x7260`, `Parsed %s` and `Attempt to import an obsolete .LBP file!`),
`LIPSincLinkBones`, `LIPSincPlayAnim`. `LIPSincInitLevel` enumerates `L"*.lad"` and hands each file to the two
readers, and `ValuesAtTime, m_nEndTime = %i, time = %f` in `0xb24f0` says the container is per-bone key lists with an
end time — which is what the 32-byte stride and the `-1.0f` sentinel are. The names are evidence for a rename, not
applied: the symbol map only changes in a Ghidra run.

## U5. Which script path writes and reads a `.sgs` — the script half is measured

**Observed.** The checkpoint container is settled (a UE2 package whose exports are the level's actors). The engine side
is `Engine.GameInfo.SavePackage` / `LoadSaveGame`, both natives without a serialised index; the game side ships
`StargateGame.AutosaveCheckpoint` (a `Trigger` subclass), `StargateGame.ACTION_SaveGame` (a `ScriptedAction`),
`Core.Object.MarkForSaveGame`, and the menu classes `StargateMenus.SaveGameMenu` / `SavedGameInfo` / `SavedGameList` /
`OverwriteSavedGamePage` / `DeleteSavedGamePage`.

**This did not need PLAN 3.10.** The packages carry their own `ScriptText`, so the call sites are readable today:
`tools/pkg-script-text.py` extracts them and `tools/savegame-paths.py` (`make savegame-paths`,
`inventory/savegame_paths.csv`) finds **16 rows** — 5 declarations, 9 calls, 2 console commands:

| role | class.routine | line | what it says |
|---|---|---|---|
| declare | `GameInfo` | 268 | `native final function bool SavePackage(string packageName)` |
| declare | `GameInfo` | 273 | `native simulated final static function bool LoadSaveGame(string GameFile)` |
| declare | `Object` | 556 | `native final function MarkForSaveGame()` |
| declare | `GameInfo` / `StoryModeGame` | 277 / 47 | `event LoadSaveGameInfo(GameInfo SavedGameInfo)` — base and override |
| **call** | `GameProfile.StartNewMatch` | 160 | `CurrentLevel.Game.SavePackage(PackageName)` |
| **call** | `GameProfile.ContinueSinglePlayerGame` | 191 | `Level.Game.SavePackage(PackageName)` |
| **call** | `GameInfo.PostLogin` | 1860 | `LoadSaveGame(SaveGameFileName)` |
| **console** | `PlayerController.RestartCheckpoint` | 1002 | `ConsoleCommand("LoadSaveGame" @ "c_" $ Left $ "_" $ LastCheckpointFilename $ ".sgs")` |
| **console** | `PlayerController.LoadGame` | 3593 | `ConsoleCommand("LoadSaveGame" @ Filename)` |
| call | `Pawn.PossessedBy`, `KActor.PreSaveGame`, `PlayerReplicationInfo.PreSaveGame`, `UnrealTeamInfo.PreSaveGame`, `GravCrane.PostNetBeginPlay`, `GravCraneLifter.OnMove` | | `MarkForSaveGame()` |

So the chain is: **both writes come from `GameProfile`** (profile start and continue), through `GameInfo.SavePackage`;
**both loads come from `PlayerController` as a console command**, not as a call to the native — and the checkpoint
filename is composed there, `c_<mission>_<checkpoint>.sgs`. `GameInfo.PostLogin` is the resume path: when
`bIsSaveGame` is set it calls `LoadSaveGame(SaveGameFileName)` for the filename a console command left pending.
Eligibility is opt-in and per-class: `MarkForSaveGame` is called from six places, four of them the engine's own
`PreSaveGame` hook, which is consistent with the exporter's actor set rather than a bespoke selection pass.

**Still open.** The native bodies behind `SavePackage`/`LoadSaveGame` — whether the writer is the engine's generic
package serializer and what the console path's handler does with the name. The `.sgs` literals in the image are
**name-table strings, not log strings**: they have no `push` xrefs, so the code has to be found another way. One
measured lead: the wide string `LoadSaveGameInfo` is referenced by a single function at `0x001226a0` (7,486 bytes),
inside the same `0x0012xxxx` region that holds the package-check readers (PLAN 6.2) — the save/load/check neighbourhood
is one place, which is worth knowing before decompiling anything there.

## U6. Two shared helpers named from their own strings; the third and four game bodies remain open

**Observed.** In the 43 functions of the `stargategame`/`stargatevehicles` modules, `FUN_00058a30` is
called by every one of them as `(expression, file, line)`, and `FUN_000340c0`/`FUN_0003af60` by 40 of
them, at the site that constructs a class from `(name, outer, object size, flags)` literals. Those three
unnamed functions were the reason the modules read as `FUN_*` soup. Four of the 43 do not follow the
registration pattern at all — the `StargateCar` physics body (`0x00311520`), two `SGCameraEffects`
bodies (`0x00316b10`, `0x00318060`) and a `GoauldDrone` function (`0x003107b0`) whose constructor call
carries no class-name literal in the decompiled text.

**Named 2026-10-24, from the strings each one logs.** Both names are recorded in
`symbols/xbe_overrides.csv` with their evidence; nothing was inferred from a guess.

| address | name | evidence |
|---|---|---|
| `0x00058a30` | `appAssertFailed` | 67 bytes. Wide literal `"Assertion failed: %s [File:%s] [Line: %i]"` (`0x004dafb0`) is the format it logs; **2,432 direct call sites**, every one passing `(expression, __FILE__, line)`. |
| `0x000340c0` | `appMallocBeforeInit` | 29 bytes. Logs the wide literal `"Called %s before memory init"` (`0x004d2ecc`) with `"appMalloc"` (`0x004d2f08`) and returns 0; **961 call sites, all `call [reg]`**, i.e. reached through a pointer, which is what an allocation entry point looks like before `GMalloc` exists. |

What the assert helper does is now measured rather than assumed: it converts the expression and the file
name to wide strings through `0x00058740` (a scratch converter with a `0x400` cap that bumps a 256-slot
block allocator at `0x00637e90`), passes both plus the line to `0x000574d0` (formats into a `0x1000`
stack buffer with `vsnprintf` at `0x003e5cc4`, checks the log-suppression globals at `0x006be0c8`/
`0x006be0dc`, then dispatches through the output device's vtable), and **returns** — there is no `int3`,
no abort and no debugger break in the body, so `check`/`verify` here logs and continues.

**Still open: `FUN_0003af60`.** Measured, but not named: 273 bytes, **493 direct call sites**, installs
the **`Class`** class table (`0x004d4870`, object size 1312, `inventory/xbe_class_table_names.csv`),
`or`s `0x12` into the flags at `+0x498`, clears `+0x49c`…`+0x51c`, stores 13 parameters at `+0x4a0`…,
returns `this` with `ret 0x34`, and starts by calling `FUN_0003a5f0` (125 bytes). The ucpp registration
stubs call it with the class literal and the `System` outer package, so it constructs the class object
for a script class; what it should be *called* needs the caller-side evidence PLAN 4.7 collects, so it
stays `FUN_*` — `docs/CONVENTIONS.md` says a hypothesis goes in the comment, not in the symbol.

**Still open: `0x003107b0`.** The `GoauldDrone` function whose constructor call carries no class-name
literal; nothing above changes that.

**Call-site census (measured).** `tools/call-census.py` over the Ghidra export: `appAssertFailed` has
**2,432 call sites** in `build/ghidra/evidence/calls.tsv` (2,245 of them from 1,270 distinct callers
after the census collapses repeated sites), spread across the engine's own modules (Core/UnTemplate
731, Core/UnMath 136, Core/UnObjBas 115, Core/UnClass 107, Engine/UnActor 100, Core/UnObj 76,
Engine/UnChan 71, unnamed 60, Gui/UnGUI 32, Engine/KFarfield 31). A helper called from a third of the
binary's functions is not script-specific glue.

## U7. How `GNatives` is populated, and how to read it

**Observed.** `GNatives` is at `0x006313e8` with `EX_Max` = 4,096 entries, in the part of `.data` the
image does not initialise — the file contains no slot bytes (`docs/NATIVE_VM.md`). Startup fills all
4,096 slots with one default stub (`0x000404a0`, the `rep stos` in one of the static initialisers) and
then patches individual slots: 233 `mov [slot], reg` write sites are visible statically, 776 places call
a fixed slot directly (`call [slot+N*4]`), and 1,428 places index it (`call [slot+reg*4]`), covering 256
distinct slots.

**Why it matters.** 4.4 — naming natives from the script's `native(index)` declarations — needs
`index -> address`. Without it, the 274 declared indices can be named but not located, and the 523
index-less declarations (U1) stay unexplained.

**Options, none tried yet.**

1. **Read it from a running build.** The prototype already boots in xemu to the main menu
   (`docs/XEMU.md`). With the symbol map loaded, dump the 16 KB array at `0x006313e8` from guest memory
   once the engine has started; that is the whole table, one command.
2. **Follow the write sites.** The 233 `mov [slot], reg` sites are inside initialisation code that knows
   both the slot number and the function it stores, so disassembling them yields a partial static map.
3. **Trace the interpreter.** Break at `0x0003a0a7` and record `(iNative, GNatives[iNative])` while the
   menu runs; script-visible behaviour then pairs an index with a body.

**Resolved 2026-10-23, partly.** Option 1 was taken: a 16 KB dump over the xemu GDB stub at the main
menu gives 356 registered slots (3,740 still hold the startup default `0x000404a0`), all 356 distinct
and inside `.text`, and **all 273 declared indices are present**. `inventory/gnatives_slots.csv` and
`symbols/xbe_dispatch.csv` carry the map; `docs/NATIVE_VM.md` has the validation and the procedure.

**Still open.** 83 registered slots have no declarer (engine-internal execs, named `Native_<index>`),
and the table is incomplete because natives are registered with the class that declares them: a full
map needs a dump after a mission loads, which is blocked on input automation (`docs/XEMU.md`).

## U8. Which slot dispatches `ProcessEvent`, and the assert sites with no function

**`ProcessEvent` found 2026-10-24, at `0x0004bf20`.** It was located by scanning the executable for
calls to `ProcessInternal` rather than by name: its two call sites (`0x4c10f`, `0x4c23e`) are the only
ones in the image and both are inside this function. Its shape confirms it — `Function->iNative` at
`[Function+0x84]` selects the native path (`call [Function+0x90]`), and the script path builds an
`FFrame` on the stack (vtable `0x4d9618`, `Object +0x04`, `Node +0x08`, `Code +0x0c`, `Locals +0x10`)
and calls `ProcessInternal` directly (`docs/NATIVE_VM.md`).

**Dispatch slot closed 2026-10-24: slot 21, byte offset `+0x54`.** The 492 pointers are 492 tables
of code pointers in `.rdata` (`0x004c78c0`–`0x0053b168`, 30–159 slots each, packed), each with exactly
21 code slots before `0x0004bf20` and no other address at that slot; every table start is named by an
immediate in `.text`. So the fork dispatches `ProcessEvent` as a virtual at a fixed slot through
`[object+0x00]`, which is why nothing calls it. Measurements and the tool:
`docs/NATIVE_VM.md`, `tools/class-tables.py`, `inventory/xbe_class_tables.csv`.

**Two families closed 2026-10-24, two residuals open.** The tables are named from the build's own
class registrations (`docs/NATIVE_CLASSES.md`, `inventory/xbe_class_table_names.csv`): 466 of 492.
Slots 19 and 20 hold `0x00020f00` — one `ret` — in 327 tables and real bodies in 165, and the 165 are
the actor-side engine hierarchy (`Actor`, `Pawn`, `Controller`, `Info`, `NavigationPoint`, …): the
split is which classes override those virtuals, not which are abstract.

Still open: **which virtuals slots 19 and 20 are** (the override reads `[this+0x2dc] & 1`, then
`[this+0xa4]` and `[+0xf4]`; nothing in the property track calls them), and **the 26 tables whose
registration cannot be followed** — their constructors install the table but the registration pushes
neither a literal nor a thunk the tool can follow. What would settle the first: a call site that
dispatches `+0x4c`/`+0x50` through a known object.

**Assert-site residuals: 6 sites, down from 199 (2026-10-24).** Four passes now run, in order: entry
candidates at the fill byte before a site (**76 seeds, 58 verified, 18 rejected**), entries named by
data tables (629), **entries found by decoding the gap** (`seedFromGaps`, 12 entries), and block
promotion for what is left (25 blocks). Measured on the same dump with the same command (`make ghidra`):

| measurement | before the gap pass | after |
|---|---|---|
| sites in no function, end of the seeder | 50 | **5** |
| sites in no function, against the committed map | 49 | **6** |
| the xref count `XbePost` reports | 50 | **5** |
| functions | 14,944 | **14,956** |
| assert-attributed functions | 1,288 | **1,300** |

The gap pass is the "second, independent decode" this section asked for, and it confirms what the
earlier note only suspected: the 49 were not undecodable. It takes the byte after a 0xCC fill run and
accepts an entry only on three measurements — the body created there contains the site, the
instruction covering the site is a `PUSH` whose immediate is the file string the asserts table names
for that row, and the body ends at a terminator. The 47 code sites had been rejected by
`looksLikePrologue`, an opening-byte list: none of the 14 entries they sit behind opens with a byte
the list contains (they open with an argument load — `mov eax,[esp+8]`, `mov eax,[0x7baaf8]`,
`movzx eax,[esp+4]`). The list is kept for the first pass so its measured results do not move; the
entries recovered without it are listed in `inventory/xbe_gap_seeds.csv`, like block seeds.

**What the last six are.** Two are not call sites at all: `0x00592cb6` and `0x00592cbe` are dwords in
a table of 8-byte records in `.rdata` whose value happens to equal the address of the file string
`0x00520040`. The xref scan looks for the address anywhere in an executable section and this build
marks `.rdata` executable, so an address alone cannot tell a dword from an operand;
`tools/assert-residual.py` separates them with the byte before the dword, which has to be `0x68`
(`push`) at a call site and is `0x87` there. The other four are `push` sites whose candidate entries
all belong to a function the analysis already has, and the three tests reject what is left; they are
listed, with the entry the decode found, in `inventory/xbe_assert_residual.csv`
(`make assert-residual`).

**The promoted blocks are not function entries.** `inventory/xbe_block_seeds.csv` lists the 25
blocks the last pass promoted, each with the evidence site it still contains and its body size, so a
decompiled body that starts at one is not mistaken for a function (`docs/NATIVE.md`). They exist so
assert-attributed code can be read and decompiled; they are not entries the build names.

**Status.** `ProcessEvent` closed. The assert-site thread is closed at 6 sites, two of which
are dwords in a data table rather than call sites. The cost of the gap pass is one body that moves:
one line of the map is **not reproducible**: `FUN_00256280`'s body measures **35,070 bytes in four runs
and 38,716 in two others** of identical code on the same dump, and the difference is exactly the 3,646
addresses (0.09% of the image) that fall out of a function in the smaller variant. That is the
analyser's order, not this pass — one run of the unmodified seeder reproduced the committed map byte
for byte and another produced the smaller body. Until that body is explained the map cannot be called
byte-for-byte reproducible, and the next run that moves it is not a regression. The slot-index question (which virtuals 19/20 are, the 26 unnamed tables) is
unchanged and is not a coverage problem. 

## U9. Phase 4.2's signature libraries cannot be carried by this repository

**Observed.** 12,347 of 14,280 functions are still `FUN_*`. 4.2 asks for MSVC 7.1 / Xbox SDK signature
libraries (xbox-includes, FLIRT-style signatures) to name the CRT, XAPI, D3D8, XNet, XOnline, zlib and
math routines. Those databases are built from Microsoft's SDK libraries and toolchain output, which
`AGENTS.md` forbids committing, and an ordinal-to-name table for `xboxkrnl.exe` is the same kind of
artefact.

**Consequence.** The part of 4.2 that can be done from the image alone is done: the XBE's own library
table names the statically linked libraries and their versions (XAPILIB 1.0.5849, D3D8, D3DX8,
XONLINE, XGRAPHC, DSOUND, XBOXKRNL, XVOICE, VOICEMAIL, HLIBCP, LIBC). What is missing is the
function-level signature data.

**What would settle it.** A signature database supplied locally by whoever runs the analysis (kept
outside the repository, like the 2006 source), with `tools/versions.lock` recording which database was
used and `docs/NATIVE.md` reporting the measured effect. Until then 4.2 stays open with the reason
written down rather than a guess at names.

**Status.** Open, blocked on data the project may not ship.

## U10. The tagged property record's field order — closed 2026-10-24

**Observed.** A `.sgs` checkpoint is a UE2 package whose exports are the level's actors
(`docs/formats/sgs-checkpoint.md`), which means its properties are serialised as tagged records —
a name, a type, a size and the value — not as a positional dump. The walk at `0x0003bdc0` selects
that stream with a flag (`[archive+0x3c] == 0` recurses into nested structs, the binary path only
does so for a property flagged `0x80000`), resolves the tag name against the struct
(`"Property %s of %s not found"`, `__wcsicmp`) and calls each property's serializer through vtable
`+0x94`. `0x0003ceb0` reads a tag name and `0x0003cd70` writes one (`docs/NATIVE_PROPERTIES.md`).

**Why it matters.** PLAN 6.2/6.4 rebuild packages and checkpoints; reproducing a tagged stream needs
the record's field order, and 5.4's schema (`inventory/sgs_schema.csv`, 1,739 tags) is only
describable at the level of names until then.

**What would settle it.** Read `0x0003bdc0`'s two branches to exhaustion and pair every store to the
stream with the read that consumes it, or dump a checkpoint's first 64 bytes at a known tag and map
the fields onto the reader's offsets. The type names are already indexed
(`inventory/property_types.csv`), so the type field can be checked by value, not by string.

**Resolved 2026-10-24.** `0x0003ceb0` is the tag reader and its body gives the order outright
(`docs/NATIVE_PROPERTIES.md` §6): int32 **Name** (index 0 ends the list), one byte **Type** whose low
nibble is the property type, bits 4–6 the size class and bit 7 "array index follows", an int32
**StructName** only for a struct property, a **Size** that is either implied by the size class
(1/2/4/12/16) or read as 1/2/4 bytes, then a compact **ArrayIndex** when bit 7 is set and the type is
not bool, then the value. The type nibble is the reserved name index of the property type (1..16), and
each property class's `vtable+0xcc` returns exactly that byte, so the tag and the class agree by
construction. UELib's `DeserializeTagUE1` — the parser that reads all 9 checkpoints and 19,080 tags
of this build with no failures — implements the same masks (`0x0F`/`0x70`/`0x80`) and the same order
independently.

**Writer measured 2026-10-24.** `0x0003cd70` fills the same record in the same order — name index at
`+4`, struct name at `+8` for a struct property, size at `+0xc`, array index at `+0x10`, type byte that
is OR-ed with the size class (1/2/4/12/16 implied, `<0x100` one byte, `<=0x10000` two, else four) and
with `0x80` when the array index is non-zero or a bool property's value is true. It also shows the
**size is a measured serialized length**, produced by running the value through a scratch archive —
which is why the reader can report a size mismatch. `docs/NATIVE_PROPERTIES.md` §6.

**One residual:** the routine that writes the assembled tag struct to the archive byte by byte was not
followed, so the on-disk *caller* of the writer is open for 6.2 even though field order, packing and
size thresholds are measured.

## U11. Which D3D8 device caps the renderer asks for

**Observed.** PLAN 2.6 also asks which D3D8 device caps this build uses. There is no import table to
read them from (`docs/IMPORTS.md`): the device is created inside the statically linked `D3D` section
(302 functions, none named), so the caps are arguments at run time, not values in a table.

**Why it matters.** The renderer-side natives (PLAN 4.8) behave the way they do because of the caps the
device was created with; knowing them also says which Xbox features the fork relies on.

**What would settle it.** Break on `CreateDevice` in a live run (xemu's GDB stub plus
`symbols/xbox.sym.o`, `docs/SYMBOLS.md`), read the `D3DPRESENT_PARAMETERS`/cap arguments, and record
them. Until then nothing is claimed, and the cap bit constants that appear in `.rdata` are not evidence
of which were requested.

**Status.** Open, needs a runtime probe (7.3 territory), not more static reading.

## U12. ~~The XBE entry point does not decode from the header~~ — resolved

**Was.** The header field at `0x128` reads `0x94B66E5C`. The documented retail key (`0xA8FC57AB`) decrypts it to
`0x3C4A39F7`, outside every section, so the entry looked unreadable. The earlier reader also mistook `0x118` (the
certificate address, `0x10184`) for the entry.

**Resolved.** The field is XOR-encrypted with the **debug-build** key `0x94859D4B`, giving **`0x0033F317`**, which
is inside `.text`. Three independent checks agree: the five bytes before the address are another function's
`xor eax,eax; ret 4` tail, so it is a function start; the body reads the XBE header back (`[0x10118]`,
`[0x10108]`), aligns the heap to 16 bytes and registers a static initialiser, which is what a CRT entry does; and
the retail key produces nothing mapped. `Xbe.entry`/`Xbe.entry_key` now carry it and `tools/xbe.py … sections`
prints it.

**Second half, also resolved.** Ghidra *was* told an entry, just the wrong one: `run-analysis.sh` passed
`0x10184` (the certificate) to both scripts. With the real entry passed, the analysis creates `XbeEntry` at
`0x0033F317` (114 bytes) and the function map goes 14,956 → 14,957 with nothing removed and no name changed.

## U13. Where a function's bytecode lives in its export (PLAN 3.7)

**Observed.** PLAN 3.7 needs a disassembler, and the reader is blocked on the *serialised* layout of a `UFunction`,
not on the opcode table: `tools/uelib` (pinned) maps opcode → token class in `src/Branch/DefaultEngineBranch.cs` and
gives every operand width in `src/Core/Tokens/*.cs`, so the decoding side is specified. What is missing is where the
script bytes start and how long they are inside the function's export payload.

**Two negatives, both measured, both worth keeping.**

1. **UELib's own reader does not transfer.** Its `UStruct.Deserialize` reads `Super`, `ScriptText`, `Children`, an
   optional `StructFlags`, `Line`, `TextPos`, `ByteScriptSize`, then the script — but parsing our 3,931 `Engine.u`
   function payloads that way validates for **1 of them** (identifier widths and the trailing function header were
   tried in both compact and `int32` forms; none fit).
2. **The content side anchors but does not locate.** Trivial bodies are unambiguous: `return true;` compiles to
   `EX_Return` + `EX_True` (`04 27`) and that pair sits exactly **12 bytes before the payload end in 424 functions**
   (`return false;` in 463). So the trailing 12 bytes are the function header and the script ends there — but no
   consistent size field precedes it: a dword at offset *k* with value `payload_end - 12 - (k+4)` exists in only
   **11 of 1,224** candidates.

**What is known.** The runtime side (`docs/NATIVE_VM.md`) reads `Function->Script` at object offset `+0x4c`, and the
interpreter treats `EX_Return` as the terminator, so the loader must fill that field from somewhere in the payload.

**What would settle it.** Decompile `UStruct::Serialize` in `default.xbe` and read the field order: it is the function
that writes `ScriptStorageSize` and the script bytes, and it is reachable through the class tables the project has
already named (`inventory/xbe_class_tables.csv`). Second route, cheaper if it works: find the *store* to `[reg+0x4c]`
in the loader with a Ghidra script and read backwards from it.

**Third session, 2026-10-26: the trailer is identified, the size field is not.** `docs/BYTECODE.md` lands the opcode
table, and its U13 probe (`python3 tools/uscript-dis.py --payloads System/Engine.u`, counts only, no payload bytes)
narrows this entry. Over `Engine.u`'s 3,842 `Function` exports with a payload of 16 bytes or more:

* **`FunctionFlags` is the dword at `payload_end-4`**: 439 of the 444 exports that join to `inventory/natives.csv` by
  (class, function) carry exactly that file's `function_flags`, so the last four payload bytes are flags and not
  script.
* **the script ends 7 bytes before the payload end** in most payloads: `04 0b 47` (`Return`, `Nothing`,
  `EndOfScript`) sits at `payload_end-10..end-8` in **3,197 of 3,842**. So the payload is
  `[header][script][3 bytes][flags]`, and only the header's field order and the script's length encoding are missing.
* **a size field in front of the script is not a plain int32 there**: a dword whose value equals the remaining length
  exists in **937** of the 3,842, and 518 of those are the 3-byte epilogue-only body; for a longer script no such
  dword is at that position.
* **and the walk cannot find the start instead**: requiring a token walk from a candidate offset that lands exactly on
  `payload_end-7` leaves **3,623 of 3,826** payloads with more than one candidate start (a scratch probe, not carried
  by the committed tool), so a self-delimiting walk is not a locator either.

**Fourth session, 2026-10-26: the tail is solved, the header's widths are not.** `UFunction::Serialize`
(`0x00039f10`) settles what the trailing bytes are, and its version gates explain why earlier readings of this
payload went wrong. After `UStruct::Serialize` it writes, in order, a `u16` (`+0x84`, the native token), a `u8`
(`+0x88`, operator precedence), the `u32` `FunctionFlags` (`+0x80`) and — **only when `FunctionFlags & 0x40` (Net)** —
a `u16` `RepOffset` (`+0x86`). The three fields a `UFunction` otherwise carries (`paramsSize`, `paramsCount`,
`returnValueOffset`) sit behind `if (Arch.Version < 0x40)` gates and this build is **version 130**, so they are
**not in the payload at all**; the body recomputes them at load by walking the `Children` chain. UELib's reader
applies the same gate (`PackageObjectLegacyVersion.Release64 = 64`), so for this build its tail order is
`NativeToken`, `OperPrec`, `FunctionFlags` — 7 bytes, or 9 with `RepOffset`.

Measured on `Engine.u`'s 3,842 `Function` payloads (`python3 tools/uscript-dis.py --payloads System/Engine.u`):

* the epilogue `04 0b 47` (Return, Nothing, and `47` = the `'G'` marker `UStruct::Serialize` appends to an
  uncompressed script) sits at `payload_end-10..end-8` in **3,197** payloads, and the `Net` flag is set in **0** of
  them;
* at `payload_end-12..end-10` in **201**, and in **all 201** the `Net` flag is set — the two extra bytes are the
  `RepOffset` the body writes. The trailer's length is therefore *predicted by a flag* with no exceptions in 3,398;
* **444** payloads carry it at neither position, which is the residue this entry now starts from;
* the script's size is a 4-byte field immediately in front of it in **804** payloads, and in exactly those the
  opcode table walks to the boundary. Those scripts are short (2 to ~30 bytes), so the evidence proves the layout
  rather than the table.

**Fifth session, 2026-10-28: the tail's fields are confirmed by content, and the header width is now the only
question left.** `tools/uscript-dis.py --payloads` takes the whole `System` directory (12,472 `Function` payloads,
797 joinable to `inventory/natives.csv`) and compares the `u16` at `payload_end-7` — `payload_end-9` when `Net` —
with the index the function's *script* declares in `native(1234)`:

* **791 of 797** payloads carry exactly the declared `NativeToken`; 5 carry a token where the script declares none
  and 1 the other way, **0 disagree in both**; `FunctionFlags` matches in **790 of 797**. The field order written
  down from `UFunction::Serialize` is therefore checked by a value that appears in two unrelated places, not by
  position alone.
* the epilogue counts over the corpus are **11,379** at 7 bytes, **337** at 9 (all Net) and **756** at neither —
  that last number is the scriptless native half, which is why it sits just under `natives.csv`'s 797.
* **the size field's position is still not derivable**, and two hypotheses have now been tested against each other
  over the 2,166 payloads where a *unique* dword equals the distance to the epilogue: `UStruct::Serialize`
  (`0x000392b0`) writes `Super`, a `Children` chain, `FriendlyName`, `StructFlags` (version > 119), `Line`,
  `TextPos`, `ScriptStorageSize`, then the script; a header of *N* compact indexes plus 16 fixed bytes fits 596
  payloads at two leading references, 22 at three and 618 at four — i.e. no fixed count, so the variable part is
  most likely the `Children` chain (one reference per parameter). **Next test, not tried:** take the parameter
  count from the export table (exports whose `outer` is the function and whose class name ends in `Property`),
  predict the start as `Super` + one reference per child + `FriendlyName` + 16, and require the walk from the
  predicted offset to end exactly on the epilogue.

  **That test has now been run, and the children-chain prediction does not hold.** Over the same 2,171 payloads, the
  prediction `Super` + one reference per child + `FriendlyName` + 16 is exact for **181**, and off by +1/+2/+3 for
  722/639/258 — adding one leading name index raises the exact count to **904** and leaves residuals spread either
  way, which is what a wrong rule looks like rather than a missing byte. Using the walker as the locator is no help
  either: walking from the predicted offset, and from ±3 around it, succeeds for 3,030 payloads at the predicted
  offset against 3,545 at **+2**, i.e. no peak where the rule says the start is — a wrong start still lands on a
  boundary in a quarter of payloads because most scripts are short. So the header width is neither a fixed count of
  references nor the `Children` chain alone, and `uscript-dis` keeps refusing rather than guessing. The two routes
  left are the ones this entry started with: `UStruct::Deserialize` in the image (the reader, not the writer), or a
  payload whose script's first token is unambiguous in the *source* — a function whose `.uc` body begins with a
  distinctive literal — used to pin one start and work outward.

**What is left is the header.** It serialises the struct's `Next`, `Super` and `Children` object references and its
`FriendlyName`, then a `u32` (written when `Version > 119`), then `Line` and `TextPos`, then the size — but the
four indices are archive-encoded and their widths vary per payload (the 804 size fields land at offsets 19..24).
For the other 3,038 payloads no dword at any of the first 48 offsets equals the script's length, so a size field
found by value is not the general rule. **Do not run the walk backwards from the tail to find the start**: a walk
started anywhere near the header walks hundreds of bytes before desynchronising, which is a property of a wrong
start rather than of the table (and 3,623 of 3,826 payloads have more than one start that reaches the boundary).

**Sixth session, 2026-10-29: the header's encoded part is pinned and the `Children`-chain rule is dead.**
`tools/uscript-dis.py --header` decodes the payload's leading values with the archive's own compact index and
requires the **sixth** to be the export's `FriendlyName` name index: that aligns **12,360** payloads, and it is
**11,687 of the 11,716** that carry the epilogue (99.75%) — so the header's last field is identified by content, the
same way the tail's was. The count of values in front of it is **constant at five** for every payload, whatever the
parameter count, which is what the previous session's test got wrong: `Super` + one reference per parameter is not
the rule, and the earlier +1/+2/+3 residuals were the encoded *widths*, not a missing reference. Resolved against the
package tables, the five are `v1` = `Super` (import 3,226, same-named function in a parent class 2,406, zero 6,728),
`v2` = `Next` (another function of the same class 10,529), `v4` = `Children` (a child of this function 9,223), and
`v0`/`v3` always zero.

**What that leaves is the fixed run after the name.** With the writer body's three `u32`s plus the size — 16 bytes
past `FriendlyName` — the size dword equals the remaining length in **672** payloads and the walk from there reaches
the epilogue in **1,975**; the rest fail at that start in the walker's bad-token case (3,824) or on an unterminated
parameter list (1,617), and the failures concentrate in the long scripts, so the 1,975 that land say nothing about
them. The two explanations still standing are therefore (a) a field
in the fixed run that the reading of `UStruct::Serialize` misses, and (b) the opcode table's 35 unnamed opcodes
being what stops the walk. **The test that separates them is a script whose first token the shipped `ScriptText`
fixes** — a body that begins with a literal the table classifies unambiguously — and it is now the cheapest route,
because everything needed is in the packages.

**Seventh session, 2026-10-29: the serializer's call sites are measured, and the loader is a vtable slot.**
`tools/ghidra/ghidra_scripts/Callers.java` exports every function that references an address, so the load side can be
looked for from the reader rather than guessed. `UStruct::Serialize` (`0x000392b0`) has **exactly two direct call
sites** — `0x000394c9` inside `0x000394c0`, and `0x00039f1a` inside `UFunction::Serialize` (`0x00039f10`), which is
the order `docs/BYTECODE.md` documents — and `UFunction::Serialize` itself has **none**, because it is dispatched
through a class table. So **the linker never calls the struct serializer directly**: the payload's header is read by
whatever `Object->Serialize(Ar)` virtual call the export loader makes, and a search for a call to `0x000392b0` from
linker code cannot succeed. The route that remains is the vtable slot, which the project already has named for 492
class tables (`inventory/xbe_class_tables.csv`).

**And the header decode does not line up with what that serializer writes.** Decoding the payload from offset 0 puts
`FriendlyName` sixth in **7,104 of 7,115** `Function` payloads in `Engine.u` and `StargateGame.u`, but decoding from
offset 2 puts it third in 4,629, fourth in 2,121 and fifth in 354 — a variable count that **does not track the export
table's child count** (three values with 0 children: 899 payloads, with 1: 1,472, with 4: 321). So the variable part of
the header is not the parameter list, and the leading values are not simply `Super` + `Children` as the body reads.
Two readings survive: a fixed prefix of linker bytes in front of the struct's own output, or a value whose decoded
width differs from its stored width. Neither is measured yet, and the fixed run after the name stays open on top of it.

**Eighth session, 2026-10-29: the header is a fixed four values for a class and five for a function, and classes are
the control.** A class runs the same `UStruct::Serialize` and carries no `UFunction` tail, so it separates "a field per
child" from "a fixed header" without any reading of the body: a class's children range from 1 (`Time`, `Locale`) to 618
(`Actor`) and **every one of the 2,316 aligned classes holds four values** in front of `FriendlyName`; every one of the
12,355 aligned functions holds five. The `Children`-chain model is therefore dead as a width rule, and the residuals
that killed the earlier per-parameter prediction were compact-encoding widths.

What the five values are, over the whole corpus: `v0` always zero, `v1` = `Super` (3,226 imports, 2,406 same-named
functions in a parent class), `v2` = `Next` (another function of the same outer, 10,529), `v3` always zero, `v4` =
`Children` (9,218). For a class the order shifts: `[Super][0][Children][…]` (2,317 of 2,318 resolve `v2` to a child).

The fixed run after the name is the last piece, and the class control cannot reach it: **no class payload carries the
`04 0b 47` epilogue at all (2,319 of 2,319)**, so a class's script is not stored the way a function's is and there is no
end to measure the run against. On functions the dword at `name_end+16` equals the distance to the epilogue in **2,141**
payloads and the walk from there reaches it in 1,975; the ones that miss fail on the walker's refusals spread across
the opcode range, so the start is probably still wrong for them and the run's length is not settled.

**Ninth session, 2026-10-29: the locator is solved — the script starts 16 bytes past `FriendlyName`.** Three
independent readings agree, and `python3 tools/uscript-dis.py --start System` reproduces all of them:

* the **floor**: an empty body still pays the epilogue and the trailer, so the smallest payload after `FriendlyName` is
  the run plus 3 plus 7 (9 when `Net`). Measured: **25** is the floor over 12,360 payloads, with spikes at **26**
  (1,380) and **28** (801) = `16+3+7` and `16+3+9`. A 12-byte run would floor at 22, where nothing appears.
* the **walk depth**: from `name_end + 16` the decoder gets 16–63 bytes in 3,494 payloads, 64–255 in 1,486, past 256
  in 86, and all the way to the epilogue in 3,328. A wrong start desynchronises within a token or two — that is 979.
* the **dword at +12** is not the script's length: larger than the physical script in 9,298 payloads, equal in 2,052,
  never smaller. So the fourth word of the run is another field, and no size field is required: `SerialSize` already
  fixes the script's end.

That closes 3.7's *locator*: the start is computed from the header, not searched for. What is left is the opcode
table's coverage (the 979 early refusals plus the ~5,000 later ones are unnamed or mis-patterned opcodes) and the
identity of the run's four words.

**Tenth session, 2026-10-29: the disassembler runs on shipped functions, and the walk refusals have no single cause.**
`--script` disassembles a function from `name_end + 16` to the epilogue, and `--usage` runs the corpus: **3,444 of
11,687** scripted payloads walk clean end to end, and their opcode census (`inventory/opcode_usage.csv`, 153 opcodes)
is now the order the per-opcode work should be done in. The refusals are spread across the table — `VirtualFunction`
708, `BadToken` 690, `FinalFunction` 424, `LabelTable` 415, `0x77` 271, `0x00` 186 — and **a single-opcode fix does not
work**: giving `0x03` each operand width from 0 to 6, or an expression, completes at most **75 of its 690** payloads.
That is the signature of a desynchronisation earlier in the stream, which makes the walk refusals a per-opcode
investigation with the usage census as its guide, not a bug to be patched in one place.

**Eleventh session, 2026-10-29: five operand widths were four fixed bytes and are a compact index — 99.3% of scripts
now walk.** The walker's handlers for groups 0, 13, 14, 15 and 19 read an `int`, and `tools/opcode-table.py` recorded
that as `bytes:4`. Held one group at a time against the corpus, with the start now known, the payloads say the operand
is the archive's **compact index** (bit 7 sign, bit 6 continuation, six bits then seven per byte):

| groups as `bytes:4` (shipped) | groups 0, 14, 15 as `index` | + group 13 | + group 19 |
|---:|---:|---:|---:|
| 3,328 of 11,350 walk | 8,869 | 10,040 | **11,269** |

`--usage` over the whole corpus agrees: **11,602 of 11,687** scripted payloads walk clean, against 3,444 before, and
`--start`'s depth histogram moves with it (11,269 to the epilogue, 81 refusing at all). The table now regenerates with
`index` for those groups and `inventory/opcode_usage.csv` is the census of the new set. This is the per-opcode work
3.7 was waiting on, and it was one rule — not the 690-payload `BadToken` case, which a width change still rescues at
most 75 of.

**Twelfth session, 2026-10-29: the index operands resolve, and the encoding indexes two tables.** With 99.3% of
scripts walking, `--resolve` reads every index operand and asks the package what it lands on. The unambiguous tokens
agree perfectly — `FinalFunction` **5,424 of 5,424** resolve to a `Function`, `DynamicCast` **4,153 of 4,153** and
`MetaClassCast` **129 of 129** to a `Class`, every variable token to a property — which is a semantic check on the
operand rules that no walk can give, because a wrong width can still tile. The virtual and global call tokens do not
name objects at all: **10,405 of 13,398** `VirtualFunction` operands and **109 of 109** `GlobalFunction` operands name
a function the package declares, i.e. they index the **name table**, and `NameConst` does the same. So the same compact
codec indexes exports/imports on one token and names on another, and only the opcode distinguishes them.

**Thirteenth session, 2026-10-29: the corpus's call sites are a table.** `--calls` records every call in the 11,602
scripts that walk: **5,424 static** (`FinalFunction`, an object reference — **all 5,424 resolve to a `Function`**) and
**13,507 dynamic** (`VirtualFunction`, `GlobalFunction`, a name), of which **10,514** name a function the package itself
declares. `inventory/script_calls.csv` carries package, class, function, token, dispatch, target and target class. The
2,993 dynamic sites whose name is not declared locally are not a defect to fix but a fact to carry: the name table is
per package, so a call into a native parent's function is indistinguishable from a call into a sibling package's until
the hierarchy is consulted.

**Fourteenth session, 2026-10-29: two negatives worth keeping.** The 85 scripts that still refuse after the operand
correction are a per-opcode tail, not a rule: `LabelTable` non-termination 16, then `0x52` 6, `0x54` 5, `0x49` 5,
`0x03` 4, `0x4c` 3, `0x2b` 3 and four parameter lists ending on `0xff`. And **class scripts are not compressed**: all
2,319 `Class` payloads were tried at five offsets past `FriendlyName`, with four skips and both zlib framings, and none
inflates to something that starts with a `Return` or ends on the `'G'` marker. Since no class carries the `04 0b 47`
epilogue either, a class's script is stored some third way — a fact to carry, not a lead.

**The image route was attempted and its address is wrong.** `LinkerLoad::Serialize` at `0x000515d0`, named in this
entry as the definitive route, is **not a function start**: `ExportAt.java` reports "no function at this address"
after `SeedGaps` has run, and the cached decompilations of the neighbourhood (`0x00038dc0` = `UObject::Serialize`,
`0x00037110` = the 4-byte field writer, `0x00036740` = a byte writer) contain no reader for this header. So the
route needs the real address first, from a call site into the export loader, rather than the number recorded here.

**What would settle it, now that the tail is known.** The remaining question is only how the script's length is
encoded in front of it, so read the *load* side rather than the payload bytes: `LinkerLoad`'s own `Serialize`
(`0x000515d0`, slot 11 of the `LinkerLoad` table in `inventory/class_serialize_slots.csv`) is the code that fills
`Function->Script` and its size, and the store to `[reg+0x4c]` is the anchor to read backwards from.

**Why it has not been attempted from those two routes yet.** Two sessions tried the payload-bytes route first; this
entry exists so the third starts from the reader, not from another alignment guess.

**Sixth session, 2026-10-29: the header's encoded part is pinned and the `Children`-chain rule is dead.**
`tools/uscript-dis.py --header` decodes the payload's leading values with the archive's own compact index and
requires the **sixth** to be the export's `FriendlyName` name index: that aligns **12,360** payloads, and it is
**11,687 of the 11,716** that carry the epilogue (99.75%) — so the header's last field is identified by content, the
same way the tail's was. The count of values in front of it is **constant at five** for every payload, whatever the
parameter count, which is what the previous session's test got wrong: `Super` + one reference per parameter is not
the rule, and the earlier +1/+2/+3 residuals were the encoded *widths*, not a missing reference. Resolved against the
package tables, the five are `v1` = `Super` (import 3,226, same-named function in a parent class 2,406, zero 6,728),
`v2` = `Next` (another function of the same class 10,529), `v4` = `Children` (a child of this function 9,223), and
`v0`/`v3` always zero.

**What that leaves is the fixed run after the name.** With the writer body's three `u32`s plus the size — 16 bytes
past `FriendlyName` — the size dword equals the remaining length in **672** payloads and the walk from there reaches
the epilogue in **1,975**; the rest fail at that start in the walker's bad-token case (3,824) or on an unterminated
parameter list (1,617), and the failures concentrate in the long scripts, so the 1,975 that land say nothing about
them. The two explanations still standing are therefore (a) a field
in the fixed run that the reading of `UStruct::Serialize` misses, and (b) the opcode table's 35 unnamed opcodes
being what stops the walk. **The test that separates them is a script whose first token the shipped `ScriptText`
fixes** — a body that begins with a literal the table classifies unambiguously — and it is now the cheapest route,
because everything needed is in the packages.

**Seventh session, 2026-10-29: the serializer's call sites are measured, and the loader is a vtable slot.**
`tools/ghidra/ghidra_scripts/Callers.java` exports every function that references an address, so the load side can be
looked for from the reader rather than guessed. `UStruct::Serialize` (`0x000392b0`) has **exactly two direct call
sites** — `0x000394c9` inside `0x000394c0`, and `0x00039f1a` inside `UFunction::Serialize` (`0x00039f10`), which is
the order `docs/BYTECODE.md` documents — and `UFunction::Serialize` itself has **none**, because it is dispatched
through a class table. So **the linker never calls the struct serializer directly**: the payload's header is read by
whatever `Object->Serialize(Ar)` virtual call the export loader makes, and a search for a call to `0x000392b0` from
linker code cannot succeed. The route that remains is the vtable slot, which the project already has named for 492
class tables (`inventory/xbe_class_tables.csv`).

**And the header decode does not line up with what that serializer writes.** Decoding the payload from offset 0 puts
`FriendlyName` sixth in **7,104 of 7,115** `Function` payloads in `Engine.u` and `StargateGame.u`, but decoding from
offset 2 puts it third in 4,629, fourth in 2,121 and fifth in 354 — a variable count that **does not track the export
table's child count** (three values with 0 children: 899 payloads, with 1: 1,472, with 4: 321). So the variable part of
the header is not the parameter list, and the leading values are not simply `Super` + `Children` as the body reads.
Two readings survive: a fixed prefix of linker bytes in front of the struct's own output, or a value whose decoded
width differs from its stored width. Neither is measured yet, and the fixed run after the name stays open on top of it.

**Eighth session, 2026-10-29: the header is a fixed four values for a class and five for a function, and classes are
the control.** A class runs the same `UStruct::Serialize` and carries no `UFunction` tail, so it separates "a field per
child" from "a fixed header" without any reading of the body: a class's children range from 1 (`Time`, `Locale`) to 618
(`Actor`) and **every one of the 2,316 aligned classes holds four values** in front of `FriendlyName`; every one of the
12,355 aligned functions holds five. The `Children`-chain model is therefore dead as a width rule, and the residuals
that killed the earlier per-parameter prediction were compact-encoding widths.

What the five values are, over the whole corpus: `v0` always zero, `v1` = `Super` (3,226 imports, 2,406 same-named
functions in a parent class), `v2` = `Next` (another function of the same outer, 10,529), `v3` always zero, `v4` =
`Children` (9,218). For a class the order shifts: `[Super][0][Children][…]` (2,317 of 2,318 resolve `v2` to a child).

The fixed run after the name is the last piece, and the class control cannot reach it: **no class payload carries the
`04 0b 47` epilogue at all (2,319 of 2,319)**, so a class's script is not stored the way a function's is and there is no
end to measure the run against. On functions the dword at `name_end+16` equals the distance to the epilogue in **2,141**
payloads and the walk from there reaches it in 1,975; the ones that miss fail on the walker's refusals spread across
the opcode range, so the start is probably still wrong for them and the run's length is not settled.

**Ninth session, 2026-10-29: the locator is solved — the script starts 16 bytes past `FriendlyName`.** Three
independent readings agree, and `python3 tools/uscript-dis.py --start System` reproduces all of them:

* the **floor**: an empty body still pays the epilogue and the trailer, so the smallest payload after `FriendlyName` is
  the run plus 3 plus 7 (9 when `Net`). Measured: **25** is the floor over 12,360 payloads, with spikes at **26**
  (1,380) and **28** (801) = `16+3+7` and `16+3+9`. A 12-byte run would floor at 22, where nothing appears.
* the **walk depth**: from `name_end + 16` the decoder gets 16–63 bytes in 3,494 payloads, 64–255 in 1,486, past 256
  in 86, and all the way to the epilogue in 3,328. A wrong start desynchronises within a token or two — that is 979.
* the **dword at +12** is not the script's length: larger than the physical script in 9,298 payloads, equal in 2,052,
  never smaller. So the fourth word of the run is another field, and no size field is required: `SerialSize` already
  fixes the script's end.

That closes 3.7's *locator*: the start is computed from the header, not searched for. What is left is the opcode
table's coverage (the 979 early refusals plus the ~5,000 later ones are unnamed or mis-patterned opcodes) and the
identity of the run's four words.

**Tenth session, 2026-10-29: the disassembler runs on shipped functions, and the walk refusals have no single cause.**
`--script` disassembles a function from `name_end + 16` to the epilogue, and `--usage` runs the corpus: **3,444 of
11,687** scripted payloads walk clean end to end, and their opcode census (`inventory/opcode_usage.csv`, 153 opcodes)
is now the order the per-opcode work should be done in. The refusals are spread across the table — `VirtualFunction`
708, `BadToken` 690, `FinalFunction` 424, `LabelTable` 415, `0x77` 271, `0x00` 186 — and **a single-opcode fix does not
work**: giving `0x03` each operand width from 0 to 6, or an expression, completes at most **75 of its 690** payloads.
That is the signature of a desynchronisation earlier in the stream, which makes the walk refusals a per-opcode
investigation with the usage census as its guide, not a bug to be patched in one place.

**Eleventh session, 2026-10-29: five operand widths were four fixed bytes and are a compact index — 99.3% of scripts
now walk.** The walker's handlers for groups 0, 13, 14, 15 and 19 read an `int`, and `tools/opcode-table.py` recorded
that as `bytes:4`. Held one group at a time against the corpus, with the start now known, the payloads say the operand
is the archive's **compact index** (bit 7 sign, bit 6 continuation, six bits then seven per byte):

| groups as `bytes:4` (shipped) | groups 0, 14, 15 as `index` | + group 13 | + group 19 |
|---:|---:|---:|---:|
| 3,328 of 11,350 walk | 8,869 | 10,040 | **11,269** |

`--usage` over the whole corpus agrees: **11,602 of 11,687** scripted payloads walk clean, against 3,444 before, and
`--start`'s depth histogram moves with it (11,269 to the epilogue, 81 refusing at all). The table now regenerates with
`index` for those groups and `inventory/opcode_usage.csv` is the census of the new set. This is the per-opcode work
3.7 was waiting on, and it was one rule — not the 690-payload `BadToken` case, which a width change still rescues at
most 75 of.

## U14. No scripted input path has reached the guest yet (PLAN 7.2, 7.3)

The title boots to its real five-item menu and the frame is capturable, but nothing sent at it from the harness has
changed what it shows. Measured on 2026-10-30, guest executing throughout (`EIP` in user space at every check):
`xdotool key Down` targeted at the window (`XSendEvent`, which SDL ignores), the same through XTEST, XTEST after
`xdotool windowfocus --sync` (the X input focus *is* the xemu window), and XTEST with
`[input] background_input_capture = true` in the run's TOML — in every case the highlight stayed on `Continue` and the
frame moved by no more than the animated background's noise floor (591 pixels in a 640x480 crop at `-fuzz 15%`,
measured with no input sent at all). XTEST `a`, the pad's A button, produced no transition either.

**What would settle it, in this order.** (1) A virtual pad: `/dev/uinput` is writable by the desktop user and
`python-evdev` is installed, so a `UInput` device can be created (`sg1-vpad` was created on `/dev/input/event27`) —
SDL reads an evdev gamepad with no window focus, so this is the one path that does not depend on the compositor handing
focus to an ssh-started process. It needs an SDL mapping so xemu treats the device as a controller, plus
`[input] gamepad_mappings` and `[input.bindings] port1` in the run's TOML. (2) If the virtual pad also shows nothing,
the question moves to the guest: whether this build reads the pad at all before the first frame, which the same
harness answers by sampling what the guest does after a click that must have an effect (a mission load changes the
screen by whole frames, not by the noise floor).

The distinction to keep: what is missing is **input injection**, not a boot or a display. Pixels were the weak channel
until the noise floor was measured; `cpu=` (QMP `EIP` sampling) is what says the guest is alive in the same run.
