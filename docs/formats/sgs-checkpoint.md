# `.sgs` — checkpoints are Unreal packages

**Container:** a UE2 package, same build as the game (`magic 0x9E2A83C1`, version `130`, licensee `4`,
package flag `0x1: AllowDownload`, one generation, zero GUID). UELib loads it with the `SG1_TA` build
profile and parses every object; `Sg1Tool save` does exactly that.

This is the important fact about the save format: a checkpoint is not an opaque blob and not a
`SaveGame` class record. It is a package whose **exports are the level's actors as they stood when the
checkpoint was taken**, each serialised with the engine's normal tagged-property stream. Consequences:

- the reader is the package reader we already have, so no `.sgs` parser has to be written;
- what a checkpoint means is a property-schema question (`inventory/sgs_schema.csv`);
- a rebuilt checkpoint is a rebuilt *package*, so the integrity check in PLAN 6.2 applies to saves too.

## Measured (all 9 shipped checkpoints)

| file | bytes | names | imports | exports | objects with tags | property tags |
|---|---|---|---|---|---|---|
| `C_M02A_Drillsite` | 44,927 | 1,072 | 93 | 210 | 166 | 2,130 |
| `C_M02A_Firstsymbol` | 42,291 | 987 | 93 | 208 | 164 | 1,983 |
| `C_M02B_Secondpositioningdevice` | 38,643 | 884 | 83 | 198 | 152 | 1,879 |
| `C_M02B_Secondsymbol` | 39,247 | 908 | 88 | 198 | 152 | 1,864 |
| `C_M02C_TeopalPyramid` | 47,410 | 1,082 | 99 | 212 | 180 | 2,386 |
| `C_M04a_VanatagePoint2` | 42,256 | 1,035 | 105 | 178 | 152 | 2,026 |
| `C_M04b_Ruins approach` | 30,965 | 877 | 88 | 114 | 98 | 1,238 |
| `C_M10B_EnteringtheDepot..` | 47,813 | 1,072 | 113 | 213 | 178 | 2,357 |
| `C_M12A_Mission 12 - Checkpoint 1` | 57,154 | 1,125 | 107 | 245 | 218 | 3,217 |
| **total** | **391,106** | — | — | 1,756 | 1,460 | **19,080** |

Zero objects failed to load, in all nine. `Sg1Tool save --path <file> --out <csv>` reproduces the
per-object rows; `tools/sgs-schema.py` reduces them to `inventory/sgs_schema.csv` (1,739 distinct
`class.property/type/size/index` tags, of which **415 appear in all nine** checkpoints).

## Layout

```
0x00  package summary (36 bytes: tag, version, licensee, flags, name/import/export counts + offsets)
0x24  GUID (16 bytes, zero) + generation count (1) + generation record
0x44  name table   -- n × (u8 length, ASCII bytes, u32 flags)
      import table -- one record per referenced class/package
      export table -- one record per object: class, outer, name, flags, serial size, serial offset
      object data  -- at each export's serial offset: the tagged-property stream
```

Property tags are the engine's `FPropertyTag`: property name, type name, serialised size, array index,
bool value, then the value. The stream ends with the `None` name. That is why the schema can be listed
without understanding a single value.

## What a checkpoint contains

Exports by class, all nine files (the actors that appear in every checkpoint are the persistent
infrastructure, not the mission-specific content):

| class | files | role |
|---|---|---|
| `Mover` | 9 | doors, lifts, the moving pieces of a level |
| `IntelligentSpawner` | 9 | enemy spawn control |
| `SVehicleTrigger`, `Trigger`, `TriggeredCondition` | 9 | volumes and the conditions attached to them |
| `BotSG`, `JaffaSerpentL`, `JaffaHorusL` | 9 | NPC instances |
| `InventoryAttachment`, `FragGrenade`, `FragGrenadeAmmo`, `AttachmentP90Weap`, `Binoculars` | 9 | the player's carried kit |
| `GameReplicationInfo`, `HealthRegenerator`, `FORMATION_4_Y` | 9 | level infrastructure |
| character pawn (`Oneill`, `Carter`, `Tealc`, `Jackson`) | per checkpoint | 37–38 tags: `Location`, `Physics`, `Inventory`, `Controller`, `PlayerReplicationInfo`, `KParams`, `InitialHealth`, `OwnerName`, … |

Tag types across the nine: `ObjectProperty` 7,272, `StructProperty` 5,889, `FloatProperty` 2,076,
`NameProperty` 1,512, `BoolProperty` 1,041, `IntProperty` 542, `StrProperty` 316, `ArrayProperty` 264,
`ByteProperty` 168.

What it does **not** contain is the profile: character choice, volumes and difficulty live in
[sgp-profile.md](sgp-profile.md). A checkpoint is level state; the profile is player state.

## Who reads and writes it

From the shipped classes and native declarations (not from guessing):

- `Engine.GameInfo.SavePackage` and `Engine.GameInfo.LoadSaveGame` are natives with no serialised
  index (`inventory/natives.csv`), i.e. engine-side implementations — the writer and reader are in the
  XBE, not in script.
- `StargateGame.AutosaveCheckpoint` is a `Trigger` subclass: walking into it is what takes a
  checkpoint.
- `StargateGame.ACTION_SaveGame` is a `ScriptedAction` (a Kismet/level-script action), and
  `StargateMenus.SaveGameMenu`, `SavedGameInfo`, `SavedGameList`, `OverwriteSavedGamePage`,
  `DeleteSavedGamePage` are the UI side.
- `Core.Object.MarkForSaveGame` is the per-object flag that decides whether an actor is written.

Which of those calls `SavePackage` in which order is script behaviour to be read from the
reconstructed sources (PLAN 3.10); the format itself is settled. See U5 in `docs/UNKNOWNS.md`.

## The two natives, measured (2026-11-09)

Both addresses come from the registration table (`inventory/native_targets.csv`, `docs/NATIVE_VM.md` *The
registration table*), so none of this needed a boot.

**The load half is whole.** `GameInfo.LoadSaveGame` is `0x00149370` (237 bytes). It decodes its one `string`
argument through the VM's step (`Stack.Code` at `+0xc`, `GNatives[b]`), then calls `0x00134d40` (2,472 bytes) — the
loader, which logs through one prefix of its own:

    [VL:LOADGAME]: Loading save game: %s            literal 0x00508f10, xref site 0x00134eb2
    [VL:LOADGAME]: Loading save game: FAILED because no viewports!
    [VL:LOADGAME]: LevelName: %s %d
    [VL:LOADGAME]: LoadGame [%s] Summary Names=%d Import=%d Export=%d

It answers 1 when the loader returns non-zero and 0 otherwise, and the `no viewports` branch calls `Disconnect` —
the shape of a load that is refused, not of one that is missing.

**The write half is a stub.** `GameInfo.SavePackage` is `0x00298bb0` (145 bytes) and its whole body is the same
argument decode: it builds the `FString` local, steps the parameter, and stores **0** into the result pointer. There
is no call, no log and no package writer in it, so `bool SavePackage(string)` answers *false* on every call. Its two
script callers are `GameProfile.StartNewMatch` and `GameProfile.ContinueSinglePlayerGame`
(`inventory/savegame_paths.csv`), and neither reads the return value, so that path fails silently.

**A writer exists, but not there.** The `[VL:SAVEGAME]` family is in the image:

    [VL:SAVEGAME]: Saving game to file: %s          literal 0x00509b50, xref site 0x00138c7b, in FUN_00138680
    [VL:SAVEGAME]: SaveGame Summary Names=%d Import=%d Export=%d   literal 0x004df8e8, site 0x00070938

`FUN_00138680` (2,278 bytes) is the save routine — the 2,278-byte counterpart of the loader's 2,472 — and it has no
direct call site in the image, because it is a **virtual**: the only dword in the whole image equal to its address is
slot 59 of the `GameEngine` vtable. The console command that reaches that slot is measured in *The console path*
below. The `SaveGame Summary` literal lives at `0x00070938`, inside `Core_UnObjBas`
(5,552 bytes), i.e. the exporter that prints those counts is the engine's own package serializer, which is what U5
asked.

**Consequence for the rebuild.** A checkpoint can be read — `LoadSaveGame` is complete and is what
`PlayerController.RestartCheckpoint` reaches through `ConsoleCommand("LoadSaveGame …")`. Writing is split: the
**stubbed** half is only the script-visible `GameInfo.SavePackage` that `GameProfile` calls, while the engine's own
writer behind the `SaveGame` console command is whole (`The console path`, below). So a rebuild does not have to
reimplement saving to keep the checkpoint key working; it has to keep — or re-point — the `SavePackage` native.
The nine `.sgs` files on the disc are still authoring output rather than something this build demonstrably writes.

## The console path, measured (2026-11-09)

**The three console commands are UnrealScript, and none of them is a native.** `CheckpointSave`, `QuickSave` and
`NewSaveGame` are `exec function`s on `PlayerController` (`inventory/console_commands.csv`; `function_flags.csv` lists
them `Defined;Exec;Public`), so there is no registration-table record to look up — "one `native_targets.csv` lookup
each" was the wrong instrument. Their extracted bodies (`tools/pkg-script-text.py` on `Engine.u`,
`$WORK/save2/PlayerController.uc`) are the dispatch, and every one of them ends in a `ConsoleCommand` string:

    NewSaveGame            ConsoleCommand("SaveGame Manual Save")
    QuickSave              ConsoleCommand("SaveGame Q Quicksave")   after a Pawn / Health / NetMode guard
    CheckpointSave(desc)   ConsoleCommand("SaveGame C" @ desc)      same guard, and skips a repeated desc
    MapChangeAutoSave      ConsoleCommand("SaveGame A")
    MatineeAutoSave        ConsoleCommand("SaveGame M")

The kind of save is therefore the **second token, one letter**, and `NewSaveGame` sends none at all.

**The handler is `FUN_00139d50` (4,829 bytes).** It walks a fixed command list with `FUN_00056dd0`, which takes a wide
literal and an `FString` by pointer, answers nonzero on a match and strips the token (an `FParse::Command`). The
literals, in the order the body tests them:

| literal | VA | what the body does with it |
|---|---|---|
| `SAVEGAME` | `0x00509854` | reads the second token `A` / `C` / `M` / `Q` into four booleans, then dispatches the writer |
| `DELETESAVEGAME` | `0x0050a56c` | calls `0x001347d0` |
| `LOADCHECKPOINT` | `0x0050a54c` | calls `0x00136a40` with one mode flag set |
| `LOADQUICK` (+ optional `BACKUP`) | `0x0050a538` / `0x0050a528` | two more calls to `0x00136a40`, different flags |
| `LOADTRAVEL` | `0x0050a510` | a third |
| `LOADSAVEGAME` | `0x0050a4f4` | a fourth, all flags clear |
| `CANCEL` | `0x0050a4e4` | a guarded travel-cancel branch |
| `GSPYLITE` | `0x0050a5b4` | takes a `GSpyLite.exe` argument |

**`SAVEGAME` reaches the writer through a vtable slot, which is why no caller exists.** At `0x0013a5bb` the body does

    13a5a5  mov  eax,[esi-0x28]        ; the GameEngine object's vtable
    13a5b7  lea  ecx,[esi-0x28]        ; this
    13a5bb  call DWORD PTR [eax+0xec]  ; slot 59

and slot 59 of the `GameEngine` vtable (`0x00509438`, 66 slots, named by `inventory/xbe_class_table_names.csv` from
the registration literal and the constructor's store) is `0x00138680` — the `[VL:SAVEGAME]: Saving game to file: %s`
writer. Two independent structures agree, and neither is a guess: `0x00509438 + 59*4 = 0x00509524` is the **only**
dword in the image equal to `0x00138680`, and `0x0013a5bb` is the only `call [reg+0xec]` inside the exec body. The
call passes an `FString` (the description, copied out of the command tail at `0x0013a58e`) and four booleans — the
kind flags, so `SaveGame C <desc>` and `SaveGame Q Quicksave` differ only in that word.

**The name is composed on the load side, in `0x00136a40` (1,053 bytes).** All five load commands call it directly
(`E8` sites `0x0013a613`, `0x0013a657`, `0x0013a66e`, `0x0013a697`, `0x0013a6c3`, all inside the exec body) with
different flag words; it is `FString` concatenation and compares the same `SAVEGAME` / `CHECKPOINT` / `QUICKSAVE`
literals (`0x00509854`, `0x00509868`, `0x00509880`) twice each. Nothing else in the image calls it. What the write
side does with the name is inside the writer, which is the remaining read.

**What the writer does with the name.** `0x00138680` opens by choosing a *label* from its three stack arguments, in
the order `[esp+0x678]` → `Map Change`, `[esp+0x674]` → `QUICKSAVE`, `[esp+0x670]` → `CHECKPOINT` (else `SAVEGAME`),
and logs it as `[VL:LOADGAME]: %s` (`0x00509c0c` — the writer reuses the loader's prefix). The filename comes from a
second chain over the same arguments, and its whole vocabulary is five format strings:

| branch, in body order | format | then |
|---|---|---|
| first flag | `TRAVEL_%s` | `%s.sgs` |
| quick | `Q_A_%s` | `%s.sgs` |
| checkpoint | `C_%s` | `%s.sgs` |
| matinee | `MATINEE_%s` | `%s.sgs` |
| none of them (manual) | `%s_%s.sgs` | `%s%s` |

Which argument feeds which row is *not* settled by this read — the label chain is ordered by stack offset and the name
chain by the decompiler's argument order, and they do not have to agree. The checkpoint row is checkable against the
shipped data anyway: `C_%s` followed by `%s.sgs` gives
`C_<SaveDescription>.sgs`, and **all nine** `.sgs` files in `System/Profiles/DefaultProfile/` have exactly that shape
(`C_M02A_Drillsite.sgs`, `C_M04b_Ruins approach.sgs`, `C_M12A_Mission 12 - Checkpoint 1.sgs`). The manual row is the
only two-part one, which is how a manual save carries its map name in front. The loader composes `TRAVEL_%s.sgs` for
its travel case too (`0x00136cc7`), so the two sides agree on that name.

**After the name.** It logs `[VL:SAVEGAME]: Saving game to file: %s`, sets a byte at `+0x708` to **2**, calls the
engine serializer (`Core_UnObjBas` — the body that prints `SaveGame Summary Names=%d Import=%d Export=%d`), walks the
object at `[engine+0x11c]`, and restores `+0x708` to **0**. So the shipped build's checkpoint write is whole, and the
only stub on the path is the script-visible `SavePackage` native.

**Correction to the note this one follows.** PR #299 said the writer "compares the same `SAVEGAME` / `CHECKPOINT` /
`QUICKSAVE` literals" at `0x001386dc`… — measured now, it *selects* one of them for that log line, in the order
`Map Change`, `QUICKSAVE`, `CHECKPOINT`, `SAVEGAME`; nothing there is compared. Where the letter lands is the second
chain above, which tests the flags positionally rather than comparing strings.

**The instrument, and the trap it exists for.** `tools/vtable-reach.py --va <addr>` runs the three scans a "zero
callers" result hides behind: `E8 rel32` call sites (a direct call is relative, so no dword scan can see it —
`0x00136a40` has five and `tools/xbe.py xrefs` answers none), dword references (what a dispatcher table or a vtable
looks like), and — for a reference that lands inside a known class table — the class, the slot index and the
`call [reg+slot*4]` sites for that offset. The offset alone never names a class: every object's slot 59 answers to
`+0xec`, which is why the tool prints those sites as *candidates* and the surrounding literal (`SAVEGAME` at
`0x0013a4f2`) is what fixes the object.

## The write's second half, measured (2026-11-09)

**Every literal here was read at the address the instruction pushes, not from the decompiler's text.**
`ExportAt.java` labels this build's wide literals two bytes late, so the reading instrument is
`tools/ghidra/ghidra_scripts/ExportLiterals.java` (new): the full instruction listing of a function with each
operand that lands in memory resolved to the string at that VA. On `0x00138680` it gives:

| VA | text | site |
|---|---|---|
| `0x004d491c` | `%s%s` | `0x00138c5e`, second half of the name |
| `0x00509b50` | `[VL:SAVEGAME]: Saving game to file: %s` | `0x00138c7a` |
| `0x004c7948` | `Engine` | `0x00138ca5` |
| `0x004df84c` | `Saving` | `0x00138caa` |

**`Engine` and `Saving` are a config *read*, not the profile write the earlier note assumed.** They are the
two arguments of `FUN_000584a0` (31 bytes), which calls `FUN_00058010` with the literal `Progress` in front;
that body formats `%s%ssystem%s%s.%s`, then calls slot `+0x10` of the config object at `0x005fbed4` with a
`0x400`-byte output buffer — a `GetString`, with the `.int` file patched in as a fallback. Its result is handed
to slot 3 of the object at `0x005fbed0`, and the matching slot 4 call (`0x00138ea4`) closes it after the
serializer: a begin/end pair around the write, not an ini write. The save routine writes **one** artefact, the
package; the index row is written two frames further down (*The profile index*, below).

**The flag.** `0x00138c96` sets byte `[[[this+0x11c]+0x2c]]+0x708` to **2**, `0x00138ebc` restores it to **0**,
and the serializer call sits between them. `[this+0x11c]` is the level the writer saves: its `+0x2c`/`+0x30`
pair is a counted pointer array that the same body then walks actor by actor (`0x00138df0`–`0x00138e96`), and
the flag sits on the array's **first element**. So 2 is "saving", 0 is "running", and no other value is written
anywhere in the body.

**The serializer call, `0x00138cf2`.** Seven words are pushed (`0x00138ce0`–`0x00138cf1`) with `ECX =
[level+0x18]` as `this`; one of them is the composed filename buffer (`LEA EDX,[ESP+0x468]` at `0x00138ce2`),
i.e. the same string that was just logged. The callee is `0x0006f4b0` (5,552 bytes), the exporter that prints
`SaveGame Summary Names=%d Import=%d Export=%d` (`0x00070938`). The caller cleans only 16 bytes (`ADD ESP,0x10`
at `0x00138cfe`) for seven pushes, so the callee returns cleaning the rest — **its exact arity is not settled**
(its epilogue is not at the address its Ghidra body size implies, and the scan found no `RET` there), which is
the one number a rebuild would still need from this call.

**The writer's own arity is seven stack arguments** (`RET 0x1c` at `0x00138f63`), and the three the name chain
reads are `[esp+0x670]`/`0x674`/`0x678` — the kind of save arrives in the arguments, not in a global.

## The profile index, measured (2026-11-09)

`DefaultProfile.sgp` is a **UE2 text INI** (CRLF, `[section]` and `key=value`), two sections, 153 rows:
`[Engine.PlayerProfile]` (11 settings) and `[Saved Games]` (142). The second one is the checkpoint index —
key = the `.sgs` file name, value = the description the menu shows (`AutosaveCheckpoint4`, `Mission 7 - Tombs -
Lower Cavern`) — so a checkpoint is **two artefacts**, and the package alone is not resumable.

Counted by `tools/sgs-profile.py` (`make sgs-profile`, dump-side, `--check` is a synthetic self-test):

| key shape | rows | example |
|---|---|---|
| `C_M07b_#_#.sgs` | 111 | `C_M07b_20050413_170733.sgs` |
| `C_M07b_OLD_#_#.sgs` | 19 | `C_M07b_OLD_20050414_194043.sgs` |
| `C_M07C_#_#.sgs` | 11 | `C_M07C_20050415_212331.sgs` |
| `C_M07B_loadtest_#_#.sgs` | 1 | `C_M07B_loadtest_20050417_174330.sgs` |

(`#` is a run of six or more digits.) Values: `autosave` 10, `label` 132. **None of the nine shipped `.sgs`
files is in the index and none of the 142 rows has a file.** The index is a mission-7 authoring session
(`20050413`–`20050418`) whose keys carry a `_<YYYYMMDD>_<HHMMSS>` suffix; the nine files are
`C_<description>.sgs` with no timestamp, which is exactly what the writer's `C_%s` + `%s.sgs` produces. The
index and the files therefore come from different builds, and the writer's name chain matches the files, not
the index.

**Which code owns the section, with an address.** The wide literal `Saved Games` at `0x00515664` (its
neighbours are `Profiles` and `.sgp`) has exactly three xrefs: `0x001be619` and `0x001be67b` inside
`FUN_001be520`, and `0x001bf9df` inside `FUN_001bf7b0`. `FUN_001be520` is on the save path: the writer calls
`FUN_001be720` at `0x00138d9d`, and that body calls `FUN_001be520` when `[this+0xa8]` is non-null. `+0xa8` is
the profile's saved-game array — count at `+0x38`, two parallel string arrays at `+0x28` (names) and `+0x34`
(descriptions). Inside `FUN_001be520` the section is touched through the config object's slot `+0x20`, then
each element is written through slot `+0x34` with **five** arguments (section first, then the two array
strings, then `[profile+0x4c]` plus the `.sgp` literal at `0x0051567c`, then a literal `1`). Four arguments is
what `SetString(Section, Key, Value, Filename)` takes and the fourth argument is the profile's own file name,
which fits; the trailing `1` does not, so the callee is *probably* the config setter but is **not confirmed**.
`FUN_001be520` ends `RET 0x10`.

## Not a reader: `0x001226a0` is the engine's name table (2026-11-09)

U5 called this 7,486-byte body "where the save/load/check module begins" because it is the one function that
references the wide literal `LoadSaveGameInfo`. It is the engine's **startup name table**: 290 wide literals, one
`FName` construction each (`CALL 0x0005bed0` with the find mode `2`), each stored into its own global across
`0x007aae9c`–`0x007b5dbc` (`0x001226bf`–`0x00124171`, 296 stores). The names are the engine's exec/event
vocabulary in alphabetical order — `Accept`, `AcceptInventory`, … `ZoneChange` — with four appended in a block of
their own (`OnQueryCompleted`, `CalculateRating`, `ReceivedPlayerSkill`, `UpdatePlayerSkill`, the XOnline/XNet
pair behind them). `LoadSaveGameInfo` is **row 118 of 290** (`0x00505b14`, site `0x0012318a`), between
`LIPSincAnimEnd` and `Login`; `PreSaveGame` is row 189 and `PostLoadSavedGame` row 176 — same list, nothing
special about the save names.

After the names the body does three things: resolves the packages `Engine` (`0x004c7948`, sites `0x00124273` and
`0x001242d4`) and `Core` (`0x004c793c`, site `0x00124367`), walks the global class array (`DAT_006c21b0`, count
`DAT_006c21b4`) running the class registration step with flag `0x2002`, and logs `Unreal engine initialized`
(`0x0050494c`, site `0x001243b9`).

**Consequence for U5.** The save/load native half has **no missing reader**: the loader `0x00134d40`, the writer
`0x00138680` and the profile writer `FUN_001be520` are the whole path. The literal-xref route is what made this
look like a lead — every engine name appears exactly once in a table like this, so the body found by an xref
search will always look like the module that "starts" that subsystem.

## Using this for the rebuild (PLAN 6.4 / 7.2)

A rebuilt build needs checkpoints that the profile lists and `LoadSaveGame` accepts. Since a
checkpoint is a package, that work is: write the package, keep its class/property layout identical,
and add the slot to the profile's `[Saved Games]` section. The nine shipped files are the fixture set
for verifying a rebuilt one.
