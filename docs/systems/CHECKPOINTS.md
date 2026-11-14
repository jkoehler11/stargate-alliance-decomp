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

## The load half, measured (2026-11-14)

`tools/xemu/checkpoint-load.sh` boots a map, waits for a gameplay frame, and types console commands at a shipped
checkpoint; `tools/xemu/checkpoint-load.py` reads the frames back into `inventory/checkpoint_loads.csv`. The witness
is `map-frame.py`'s HUD corner, unchanged: 0.140-0.986 on the 65 frames that are play and 0.000-0.027 on the 7 that
are not.

Both runs boot `12` `M02B` and name `C_M02B_Secondsymbol.sgs` -- a checkpoint **of the level it was loaded in**, the
case the one recorded attempt never covered (that one typed an `M02A` checkpoint from `M00`). Each run carries a
**control** too: `stat fps`, in the same console, with the same keystroke count and the same waits, and a command
that cannot touch the level.

**The control keeps playing; the load does not.** After `stat fps` the picture repaints 294 px and then 326 px at
+30 s and +70 s of a 1280x960 frame, the HUD corner reads `0.1852`, and six EIP samples are six distinct user-space
addresses. So the console, the typing and the waits are not what moves anything.

`loadgame c_m02b_secondsymbol.sgs` -- the route `RestartCheckpoint` and `SavedGameList` use, via `LoadSaveGame` --
then reads, frame by frame:

| when | picture | HUD corner | EIP samples |
|---|---|---:|---|
| before the submit | gameplay | 0.1753 | 6 distinct, 0 in kernel |
| +30 s | changed 14,184 px, no HUD | 0.0000 | 3 of 6 in kernel (`0x80024aab`, `0x80024a97` x2) |
| +60 s | changed 3 px | 0.0000 | -- |
| +100 s | unchanged (0 px) | 0.0000 | -- |
| +180 s, after the second spelling was typed at the stalled guest | changed, HUD drawn | 0.1708 | 5 of 6 in kernel |
| +260 s | unchanged (0 px) | 0.1708 | 5 distinct, 5 in kernel |

**No resumed play in the window, and the reason is a stalled guest rather than a lost one.** The kernel idle
addresses (`0x80024a97`, `0x80024a98` -- the pair `docs/XEMU.md` records for the BIOS prompt) turn up in five of six
samples two minutes after the submit, and every frame after the one the HUD appears in is byte-identical to it: a
playable level animates. The pad still reaches the guest -- `pad=start` moved 32,177 px into the pause menu and
`pad=a` moved another 32,177 -- so the input path is alive while the game loop is not, and "the load left the guest
in kernel idle" is the reading the two witnesses agree on.

The spelling is not the difference: in `ckpt1` the same file went through `loadgame` and then through
`checkpointload` (`CheckpointLoad` -> `LoadCheckPoint`, the route the recorded attempt used), and both rounds sit on
one stopped picture -- the second command was typed at a guest that had already stopped, which is why that round
reads one picture and no HUD instead of a separate verdict.

What this does **not** say: that the nine files are unreadable. `docs/formats/sgs-checkpoint.md` reads all nine and
the loader at `0x00134d40` is whole. What it says is that the shipped build does not give back a playable level from
one inside three minutes, through either console route -- and the console is the only route this dump offers, because
`GameInfo.SavePackage` is a stub (so no profile save is ever written beside them) and the profile index has no row
for any of the nine.

<!-- generated:checkpoint-load -->
| run | round | frames after the submit | of those, pictures | drawing the HUD |
|---|---|---:|---:|---:|
| `ckpt1` | `r1` | 6 | 2 | 0 |
| `ckpt2` | `r0` | 2 | 2 | 2 |
| `ckpt2` | `r1` | 6 | 5 | 2 |

| run | shot | corner | prompt | read | note |
|---|---|---:|---:|---|---|
| `ckpt1` | `00-title` | 0.0049 | 0.0668 | no-hud | first frame |
| `ckpt1` | `01-highlight` | 0.0049 | 0.0668 | no-hud | repaint 77 px |
| `ckpt1` | `02-mission` | 0.2267 | 0.0000 | hud | repaint 7578 px |
| `ckpt1` | `03-gameplay` | 0.2161 | 0.0000 | hud | repaint 595 px |
| `ckpt1` | `r1a-typed` | 0.2171 | 0.0000 | hud | repaint 27 px |
| `ckpt1` | `r1b-30` | 0.0000 | 0.0000 | no-hud | repaint 3204 px |
| `ckpt1` | `r1c-60` | 0.0000 | 0.0000 | no-hud | same bytes as `r1b-30` |
| `ckpt1` | `r1d-100` | 0.0000 | 0.0000 | no-hud | same bytes as `r1b-30` |
| `ckpt1` | `r1e-cptyped` | 0.0000 | 0.0000 | no-hud | repaint 0 px |
| `ckpt1` | `r1f-cp50` | 0.0000 | 0.0000 | no-hud | same bytes as `r1b-30` |
| `ckpt1` | `r1g-cp80` | 0.0000 | 0.0000 | no-hud | same bytes as `r1b-30` |
| `ckpt2` | `00-title` | 0.0049 | 0.0668 | no-hud | first frame |
| `ckpt2` | `01-highlight` | 0.0049 | 0.1055 | no-hud | repaint 19070 px |
| `ckpt2` | `02-mission` | 0.1823 | 0.1028 | hud | repaint 24815 px |
| `ckpt2` | `03-gameplay` | 0.1838 | 0.1175 | hud | repaint 455 px |
| `ckpt2` | `r0a-typed` | 0.1882 | 0.1104 | hud | repaint 154 px |
| `ckpt2` | `r0b-30` | 0.1852 | 0.1128 | hud | repaint 294 px |
| `ckpt2` | `r0c-70` | 0.1874 | 0.1090 | hud | repaint 326 px |
| `ckpt2` | `r1a-typed` | 0.1753 | 0.1075 | hud | repaint 13 px |
| `ckpt2` | `r1b-30` | 0.0000 | 0.9767 | no-hud | repaint 14184 px |
| `ckpt2` | `r1c-60` | 0.0000 | 0.9767 | no-hud | repaint 3 px |
| `ckpt2` | `r1d-100` | 0.0000 | 0.9767 | no-hud | same bytes as `r1c-60` |
| `ckpt2` | `r1e-cptyped` | 0.0000 | 0.9767 | no-hud | repaint 0 px |
| `ckpt2` | `r1f-cp50` | 0.1708 | 0.0000 | hud | repaint 14348 px |
| `ckpt2` | `r1g-cp80` | 0.1708 | 0.0000 | hud | repaint 0 px |
| `ckpt2` | `h0-pause` | 0.0049 | 0.0005 | no-hud | repaint 32177 px |
| `ckpt2` | `h1-after-a` | 0.1708 | 0.0000 | hud | repaint 32177 px |

| run | step | vCPU | samples | distinct | in kernel |
|---|---:|---|---:|---:|---:|
| `ckpt2` | 37 | running | 6 | 6 | 0 |
| `ckpt2` | 44 | running | 6 | 6 | 0 |
| `ckpt2` | 72 | running | 6 | 6 | 0 |
| `ckpt2` | 146 | running | 6 | 5 | 3 |
| `ckpt2` | 238 | running | 6 | 3 | 5 |
| `ckpt2` | 245 | running | 6 | 5 | 2 |
<!-- /generated:checkpoint-load -->

## Not measured

* Which of the nine files is which mission — the committed schema aggregates across files
  (`inventory/sgs_schema.csv` drops the per-file rows), so the per-mission view needs a re-parse.
* Reference resolution on load, above.
* Whether a load rebuilds the level and then applies the snapshot, or restores it wholesale; that
  distinction decides how much of the map parser (5.2) the rebuild needs.
