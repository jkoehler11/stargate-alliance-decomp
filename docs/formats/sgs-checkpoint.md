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

**The instrument, and the trap it exists for.** `tools/vtable-reach.py --va <addr>` runs the three scans a "zero
callers" result hides behind: `E8 rel32` call sites (a direct call is relative, so no dword scan can see it —
`0x00136a40` has five and `tools/xbe.py xrefs` answers none), dword references (what a dispatcher table or a vtable
looks like), and — for a reference that lands inside a known class table — the class, the slot index and the
`call [reg+slot*4]` sites for that offset. The offset alone never names a class: every object's slot 59 answers to
`+0xec`, which is why the tool prints those sites as *candidates* and the surrounding literal (`SAVEGAME` at
`0x0013a4f2`) is what fixes the object.

## Using this for the rebuild (PLAN 6.4 / 7.2)

A rebuilt build needs checkpoints that the profile lists and `LoadSaveGame` accepts. Since a
checkpoint is a package, that work is: write the package, keep its class/property layout identical,
and add the slot to the profile's `[Saved Games]` section. The nine shipped files are the fixture set
for verifying a rebuilt one.
