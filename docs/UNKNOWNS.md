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

## U5. Which script path writes and reads a `.sgs`

**Observed.** The checkpoint container is settled (a UE2 package whose exports are the level's
actors). The engine side is `Engine.GameInfo.SavePackage` / `LoadSaveGame`, both natives without a
serialised index; the game side ships `StargateGame.AutosaveCheckpoint` (a `Trigger` subclass),
`StargateGame.ACTION_SaveGame` (a `ScriptedAction`), `Core.Object.MarkForSaveGame`, and the menu
classes `StargateMenus.SaveGameMenu` / `SavedGameInfo` / `SavedGameList` / `OverwriteSavedGamePage` /
`DeleteSavedGamePage`.

**Unknown.** Which of them calls `SavePackage`, in what order, and whether the writer is the engine's
generic package serializer (which would explain the exporter's choice of actor set) or a bespoke
pass. Also unknown: what makes an actor eligible besides `MarkForSaveGame`.

**What would settle it.** Read the reconstructed bodies for those classes (PLAN 3.10) and their
native call sites in the XBE (4.6/4.9). No format work is blocked on it; rebuilding a checkpoint
(PLAN 6.4) is.

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

**Why it has not been attempted from those two routes yet.** Two sessions tried the payload-bytes route first; this
entry exists so the third starts from the reader, not from another alignment guess.
