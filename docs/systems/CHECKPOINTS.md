# Checkpoints: what the game persists

The nine shipped `.sgs` files hold **1,739 property rows over 96 classes**, and their shape answers a
question the format documents do not: a checkpoint is not a small progress record, it is a snapshot of
the live level's actors.

    python3 tools/checkpoint-report.py            # tables, from the committed schema
    python3 tools/checkpoint-report.py -o inventory  # + inventory/checkpoint_summary.csv

Source: `inventory/sgs_schema.csv` (one row per class+property across all nine files, with the number of
files that contain it and the instance count). The format these rows were parsed from is measured in
`docs/NATIVE_PROPERTIES.md` §6–7.

## What is in there

`ObjectProperty` dominates — 693 of 1,739 rows (39.9%) are references to other objects, which is what a
snapshot of a live level looks like:

| type | rows | share |
|---|---:|---:|
| ObjectProperty | 693 | 39.9% |
| StructProperty | 424 | 24.4% |
| FloatProperty | 198 | 11.4% |
| BoolProperty | 112 | 6.4% |
| NameProperty | 110 | 6.3% |
| ArrayProperty | 64 | 3.7% |
| IntProperty | 61 | 3.5% |
| StrProperty | 56 | 3.2% |
| ByteProperty | 21 | 1.2% |

Only 26 rows carry an array index, and they are structural rather than gameplay-sized: `Teams` on
`GameReplicationInfo`, `KInertiaTensor` on `KarmaParamsRBFull`, `Occupant` on a formation, `Rosters` on
`SGLevelGamePlay`. Nothing here looks like an inventory list of arbitrary length.

## What dominates by instance count

| class | instances | properties | in every file |
|---|---:|---:|---:|
| `Mover` | 2,227 | 59 | 15 |
| `IntelligentSpawner` | 1,860 | 20 | 8 |
| `SVehicleTrigger` | 1,115 | 16 | 12 |
| `BotSG` | 890 | 57 | 26 |
| `TeamPlayerReplicationInfo` | 597 | 28 | 16 |
| `Trigger` | 581 | 21 | 12 |
| `FragGrenade` | 447 | 19 | 14 |
| `P90Prototype` | 432 | 23 | 15 |
| `ShieldBeltAttach` | 360 | 9 | 9 |
| `Jackson` | 338 | 64 | 24 |

Movers, spawners and triggers outnumber characters by an order of magnitude, and `FragGrenade` and the
dropped-weapon classes (`P90Prototype`, `PistolPrototype`) are objects that only exist mid-firefight.
That is not a save of "progress" — that is the level's actor list written out.

## What the characters carry

The SG-1 pawns are the property-rich classes: `Oneill` 65 properties, `Jackson` 64, `Carter` 62,
`Tealc` 53, and `StargatePlayer` 45. Equipment, animation and AI state for the squad is persisted per
pawn, which is consistent with 338 `Jackson` instances across nine files: the squad exists in most
checkpoints, not as one object.

## The baseline every actor carries

415 rows appear in **all nine files**, but only 105 distinct property names — because every persisted
actor carries the same engine header:

| property | classes carrying it |
|---|---:|
| `Level` | 36 |
| `PhysicsVolume` | 36 |
| `Region` | 36 |
| `Tag` | 35 |
| `Location` | 27 |
| `Rotation` | 27 |
| `Owner` | 24 |
| `Instigator` | 21 |

That is `UObject`/`AActor` baseline state, not game design. The interesting split is the other end:
**533 rows appear in exactly one file**, which is where mission-specific state lives — the part a
checkpoint actually differs by.

## What this implies for the rebuild (6.x)

1. The save format *is* the property system, so a rebuilt package needs actor serialization to match,
   not a bespoke save writer. `docs/NATIVE_PROPERTIES.md` §6–7 already measures the tag layout from both
   reader and writer.
2. **40% of the rows are object references**, so load-time reference resolution is the hard part: a
   checkpoint names objects that must be found again in a freshly loaded level. How that resolution
   works — by name, by level index, by `PackageCheckInfo`-style table — is not measured, and it is the
   blocker on writing a checkpoint as well as reading one.
3. Physics state is persisted (`KarmaParamsRBFull`, inertia tensors), so a load cannot simply respawn
   rigid bodies and let them settle; the state is restored.

## Not measured

* Which of the nine files is which mission — the committed schema aggregates across files
  (`inventory/sgs_schema.csv` drops the per-file rows), so the per-mission view needs a re-parse.
* Reference resolution on load, above.
* Whether a load rebuilds the level and then applies the snapshot, or restores it wholesale; that
  distinction decides how much of the map parser (5.2) the rebuild needs.
