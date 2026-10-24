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

## U6. Three shared helpers, and four game functions that are not registration stubs

**Observed.** In the 43 functions of the `stargategame`/`stargatevehicles` modules, `FUN_00058a30` is
called by every one of them as `(expression, file, line)`, and `FUN_000340c0`/`FUN_0003af60` by 40 of
them, at the site that constructs a class from `(name, outer, object size, flags)` literals. Those three
unnamed functions are the reason the modules read as `FUN_*` soup: naming them changes every function at
once. Four of the 43 do not follow the registration pattern at all — the `StargateCar` physics body
(`0x00311520`), two `SGCameraEffects` bodies (`0x00316b10`, `0x00318060`) and a `GoauldDrone` function
(`0x003107b0`) whose constructor call carries no class-name literal in the decompiled text.

**Unknown.** The helper names and signatures (the assert one is inferred from its arguments only, not
from a symbol), what `FUN_000340c0` does, and what `0x003107b0` constructs.

**Call-site census (measured).** `tools/call-census.py` over the Ghidra export: `FUN_00058a30` has
**2,245 call sites from 1,270 distinct callers**, spread across the engine's own modules (Core/UnTemplate
731, Core/UnMath 136, Core/UnObjBas 115, Core/UnClass 107, Engine/UnActor 100, Core/UnObj 76,
Engine/UnChan 71, unnamed 60, Gui/UnGUI 32, Engine/KFarfield 31). A helper called from a third of the
binary's functions is not script-specific glue; combined with the `(expression, __FILE__, line)` argument
shape at every inspected site, the assert/verify reading is settled.

**What is still open.** What to call them. `FUN_00058a30` is assert-shaped (`check`/`verify` family) but
whether it logs, breaks into the debugger or aborts is unmeasured, so a name would overstate it. The
other two are unresolved: `FUN_0003af60` takes `(…, class name literal, outer "System", flags, vtable)`
with the object size from the caller, and `FUN_000340c0` has not been read at all. The 2006 oracle is the
other route (it is the engine fork, so its error/assert helpers are the same ones). Naming them is its
own commit per `AGENTS.md`.

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

**Assert-site residuals: 91 xrefs, down from 102.** The seeder now collects *several* plausible
entries per site (nearest first, one attempt per round) instead of only the nearest fill run, so a site
whose nearest candidate is a function body cut short by a jump can still be reached from the fill run
before it. Measured on 2026-10-24: **76 seeds, 58 verified by containing their evidence site, 18
rejected** (was 52 of 64), functions 14,912 -> 14,918, assert-attributed 1,256 -> 1,262, and sites with
no function **199 -> 93** at the end of the pass, **88** against the committed symbol map, 102 -> 91 by
the xref count this file used before.

**Why the remaining 88 stay.** They are not unseeded, they are rejected: the candidate disassembles,
but the body it produces does not contain the assert, because the assert sits in a block the entry
reaches only through an unconditional jump. Their distribution says the same thing — Engine 46, Core
31, D3DDrv 5, XboxAudio 3, XboxLive 2, stargategame 1, median 346 bytes past the previous function's
end, 73 of 88 within 1 KB of one. What would settle them: give the created function a body that
includes the site's block (a jump target inside the same extent), rather than accepting only what
linear flow from the entry reaches.

**Status.** `ProcessEvent` closed; the slot index and the 102 sites open. Neither is correctness. 

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

## U10. The tagged property record's field order

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

**Status.** Open. Nothing in the property type-space work depends on it.
