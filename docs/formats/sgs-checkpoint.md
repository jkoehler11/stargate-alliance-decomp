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

## Using this for the rebuild (PLAN 6.4 / 7.2)

A rebuilt build needs checkpoints that the profile lists and `LoadSaveGame` accepts. Since a
checkpoint is a package, that work is: write the package, keep its class/property layout identical,
and add the slot to the profile's `[Saved Games]` section. The nine shipped files are the fixture set
for verifying a rebuilt one.
