# Unknowns

Things this project does not know yet, with the evidence that raised the question and what would
settle it. A guess does not belong here either — only a gap that is being tracked.

## U1. 523 of the 797 `native()` declarations serialise no index

**Observed.** `inventory/natives.csv` records 797 native functions across 18 packages. 274 carry an
explicit index and 270 of those resolve to a `GNatives` slot (`inventory/native_names.csv`). The
other 523 carry `native_token` 0 — for example `Engine.Actor.KWake`, `Engine.Canvas.Clear`,
`GUI.GUIComponent.ActualWidth` and every `IpDrv` native.

**Why 0 is the package's value, not a parser default.** UELib reads `NativeToken` unconditionally
from the serialised `UFunction` (`src/Core/Classes/UFunction.cs`, `NativeToken = _Buffer.ReadUShort()`
in `Deserialize`), and it reads the right value for the 274 indexed declarations in the same
packages, so the offset is correct for build 130/4. The zeros are what the build shipped.

**Why it matters.** A declaration with no index claims no dispatch slot, so it cannot be named from
the table, and 720 slots stay `Native_<index>`. It also means those functions are not reached the way
the indexed ones are: something else must dispatch or inline them.

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

**Status.** Open. No effect on the naming in 4.4: those declarations never claimed a slot, so
nothing was named wrongly.

## U2. Three `Engine.Actor` natives declare indices beyond the table

**Observed.** `Actor.MoveSmooth`, `Actor.SetPhysics` and `Actor.AutonomousPhysics` declare indices
3969, 3970 and 3971; the dispatch table has 994 entries (0–993), so there is no slot to name. The
table itself is corroborated independently (270 of 274 declared indices resolve, one code xref, no
size ambiguity), so this is not a table-detection error.

**Hypotheses.** Same family as U1 — indices assigned in a numbering space this build does not
dispatch through (a PS2/GameCube build, or the `ucpp` compiler's own space) — or the three
functions are native-only and never dispatchable from script.

**What would settle it.** Find a script call site for any of the three and see whether it compiles to
an index opcode; look for their C++ bodies in the XBE during 4.7.

**Status.** Open.

## U3. `UPreviewMapListClient.Created` claims index 2, which is a null slot

**Observed.** The only declarer of index 2 is `UPreview.UPreviewMapListClient.Created`; slot 2 of
`GNatives` is null. `UPreview` is an editor-side package that the shipped game does not load.

**Hypotheses.** Editor packages number their natives inside the package rather than in the engine's
global index space, so their small indices are unrelated to the runtime table.

**What would settle it.** The same test on a second editor package with indexed natives (`UnrealEd`,
`Editor` have none with an index here, which is itself consistent with the hypothesis but does not
prove it).

**Status.** Open, low priority. `Editor`/`UPreview` are excluded from naming
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

**What would settle it.** Cross-referencing the helper addresses against the 2006 engine source (which
is the engine fork, so `appError`-style helpers appear there) in the private oracle-diff harness, or a
call-site census of the helpers across all 15,018 functions — if `FUN_00058a30` has hundreds of callers
that all pass file/line, the assert reading is settled.
