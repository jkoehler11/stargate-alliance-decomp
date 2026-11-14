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

## Where the loader looks (2026-11-14)

The open question this page left was whether the nine shipped files resolve at all: a console command types a bare
name, so the path has to be built by the loader. It is built, and the build is readable in `0x00134d40` with no boot.
`tools/save-path-compose.py` (`make save-path-compose`, `inventory/save_path_compose.csv`) reads the same chain back at
every address it quotes and holds this page to it.

**The path is three joins.** `FUN_0001b260(L"defaultdevice")` makes the device segment (sites `0x00134dc5` and
`0x00134e2b`), `FUN_001be450(out, <device>, <profile>)` joins the device to a profile, and `FUN_00036c90(buf, L"%s%s",
<prefix>, name)` appends the name the console typed. The profile is the literal `L"Auto"` (`0x004dc798`) when the name
starts with `TRAVEL_` (`wcsnicmp` at `0x00134dab`), otherwise the `FString` at `+0x4c` of the object at `+0xf4c` --
the active profile.

**The device segment contributes no characters.** `FUN_001be350` compares its argument against the literal at
`0x00508cd4` (`wcsicmp` `FUN_00036d00`, site `0x001be389`) and, on a match, asks the file manager -- the singleton at
`[0x005bf514]`, its vtable's slot `+0x38` -- for the device path, then appends `\`, `L"Profiles"` (`0x00515650`) and
another `\`. That slot is `0x00034210`: **33 bytes, whose whole body constructs the empty string from the literal at
`0x004c85ac`** and returns its argument. Every other device name takes the helper's else arm and yields an empty
prefix outright. So the prefix the loader composes is `\Profiles\<profile>\` -- rootless, and the device name itself
never reaches the string.

**That is the whole save module's root, not the loader's quirk.** The table below lists all **26** sites in the image
that push or store the `defaultdevice` literal: the loader, the deleter beside it, the writer's region, and the whole
profile family at `0x001be7xx`-`0x001bfdxx` (`FUN_001be720`, `FUN_001be520` and their neighbours). One getter that
answers `""` therefore puts every save and profile path in the build one segment short of the disc's tree.

**The disc's tree is the same tail, one directory deeper.** All twelve save/profile files sit under
`System/Profiles/<profile>/` -- `DefaultProfile`, `sean` and `lbcddnyzq` -- and the loader composes
`\Profiles\<profile>\<name>`: identical in the profile and the file name, one `System\` short. Neither the image nor
the ini supplies that segment. `DefaultProfile` appears in **no** wide literal at all, `Profiles` in two (one of them
the helper's own), the only save-directory literal in the build is `..\Saves\` (`0x00523374`, one reference), and the
three shipped inis say `[Core.System] SavePath=..\Save` -- a directory that exists in the dump under neither spelling.

**What that does and does not settle.** If the file manager's base directory is the game's `System\` directory, the
composed path lands on the shipped file exactly and the load gets far enough to fail inside the level -- which is what
a guest that ends on the kernel's `Please insert an Xbox disc...` prompt rather than in a menu looks like. If the base
is the device root, no file has ever existed at the composed path and the loader takes its own `FAILED` branch
(`0x00508d88`) without touching a level. The two are distinguishable only by the path the loader logs
(`[VL:LOADGAME]: Loading save game: %s`, `0x00508f10`), which no frame carries. The cheap next read is the base
directory itself: the `System\` literal at `0x004c9790` is stored at `0x00149603`, inside no committed function -- the
setter that would answer this is the one piece of the chain still unread.

<!-- generated:save-path -->
**The chain, read out of the image.** `0x001be350` is the helper the loader calls with the device name; the device it asks for is the file manager's, and that getter answers the empty string.

| step | address | what it is |
|---|---|---|
| singleton | `0x005c1a0c` | file manager, `[0x005bf514]` -> vtable `0x004d2fd0`, slot `+0x38` |
| device_getter | `0x00034210` | 33 bytes; constructs the empty literal at `0x004c85ac` |
| helper | `0x001be350` | device name -> `<device>\Profiles\`; compares at `0x00036d00` |
| join | `0x001be450` | `<helper> + profile + \`, called twice from the loader `0x00134d40` |

**The literals the composition is made of**, each read back at its address:

| literal | address | read back |
|---|---|---|
| `defaultdevice` | `0x00508cd4` | ok "defaultdevice" |
| `Profiles` | `0x00515650` | ok "Profiles" |
| `separator` | `0x004c8b94` | ok "\\" |
| `Auto` | `0x004dc798` | ok "Auto" |
| `TRAVEL_` | `0x00508f5c` | ok "TRAVEL_" |
| `join` | `0x004d491c` | ok "%s%s" |
| `empty` | `0x004c85ac` | ok "" |

**The device name**, at every site that pushes or stores it:

| site | kind | where |
|---|---|---|
| `0x0013482d` | push | `FUN_001347d0` |
| `0x00134dc6` | push | `FUN_00134d40` |
| `0x00134e2c` | push | `FUN_00134d40` |
| `0x001363d4` | push | `FUN_001363a0` |
| `0x0013640d` | mov | `FUN_001363a0` |
| `0x00136770` | push | `FUN_00136740` |
| `0x001367ab` | mov | `FUN_00136740` |
| `0x0013874b` | push | no committed function spans it |
| `0x001387b1` | push | no committed function spans it |
| `0x00139c40` | push | `Engine_UnGame` |
| `0x001be384` | push | `FUN_001be350` |
| `0x001be75c` | push | `FUN_001be720` |
| `0x001be80c` | push | `FUN_001be7d0` |
| `0x001beaab` | push | `FUN_001bea60` |
| `0x001beae6` | mov | `FUN_001bea60` |
| `0x001bec9d` | push | `FUN_001bebb0` |
| `0x001befbd` | push | `FUN_001bef40` |
| `0x001beff8` | mov | `FUN_001bef40` |
| `0x001bf131` | push | `FUN_001bf0b0` |
| `0x001bf16c` | mov | `FUN_001bf0b0` |
| `0x001bf67c` | push | `FUN_001bf660` |
| `0x001bf6ba` | mov | `FUN_001bf660` |
| `0x001bfbce` | push | `FUN_001bf7b0` |
| `0x001bfc0b` | mov | `FUN_001bf7b0` |
| `0x001bfd4f` | push | `FUN_001bfd00` |
| `0x001bfd8a` | mov | `FUN_001bfd00` |

**What the loader composes against what the disc holds** (`\Profiles\<profile>\<name>`):

| file | on the disc | bytes | loader composes |
|---|---|---|---|
| `C_M02A_Drillsite.sgs` | `System/Profiles/DefaultProfile/C_M02A_Drillsite.sgs` | 44927 | `\Profiles\DefaultProfile\C_M02A_Drillsite.sgs` |
| `C_M02A_Firstsymbol.sgs` | `System/Profiles/DefaultProfile/C_M02A_Firstsymbol.sgs` | 42291 | `\Profiles\DefaultProfile\C_M02A_Firstsymbol.sgs` |
| `C_M02B_Secondpositioningdevice.sgs` | `System/Profiles/DefaultProfile/C_M02B_Secondpositioningdevice.sgs` | 38643 | `\Profiles\DefaultProfile\C_M02B_Secondpositioningdevice.sgs` |
| `C_M02B_Secondsymbol.sgs` | `System/Profiles/DefaultProfile/C_M02B_Secondsymbol.sgs` | 39247 | `\Profiles\DefaultProfile\C_M02B_Secondsymbol.sgs` |
| `C_M02C_TeopalPyramid.sgs` | `System/Profiles/DefaultProfile/C_M02C_TeopalPyramid.sgs` | 47410 | `\Profiles\DefaultProfile\C_M02C_TeopalPyramid.sgs` |
| `C_M04a_VanatagePoint2.sgs` | `System/Profiles/DefaultProfile/C_M04a_VanatagePoint2.sgs` | 42256 | `\Profiles\DefaultProfile\C_M04a_VanatagePoint2.sgs` |
| `C_M04b_Ruins approach.sgs` | `System/Profiles/DefaultProfile/C_M04b_Ruins approach.sgs` | 30965 | `\Profiles\DefaultProfile\C_M04b_Ruins approach.sgs` |
| `C_M10B_EnteringtheDepot..sgs` | `System/Profiles/DefaultProfile/C_M10B_EnteringtheDepot..sgs` | 47813 | `\Profiles\DefaultProfile\C_M10B_EnteringtheDepot..sgs` |
| `C_M12A_Mission 12 - Checkpoint 1.sgs` | `System/Profiles/DefaultProfile/C_M12A_Mission 12 - Checkpoint 1.sgs` | 57154 | `\Profiles\DefaultProfile\C_M12A_Mission 12 - Checkpoint 1.sgs` |
| `DefaultProfile.sgp` | `System/Profiles/DefaultProfile/DefaultProfile.sgp` | 8902 | `\Profiles\DefaultProfile\DefaultProfile.sgp` |
| `lbcddnyzq.sgp` | `System/Profiles/lbcddnyzq/lbcddnyzq.sgp` | 347 | `\Profiles\lbcddnyzq\lbcddnyzq.sgp` |
| `sean.sgp` | `System/Profiles/sean/sean.sgp` | 347 | `\Profiles\sean\sean.sgp` |

**The profiles the disc carries** -- the loader's middle segment:

| profile | on the disc | loader composes |
|---|---|---|
| `DefaultProfile` | `System/Profiles/DefaultProfile` | `\Profiles\DefaultProfile\` |
| `lbcddnyzq` | `System/Profiles/lbcddnyzq` | `\Profiles\lbcddnyzq\` |
| `sean` | `System/Profiles/sean` | `\Profiles\sean\` |

**Save-directory vocabulary.** The ini's configured save directory, the directories that exist, and how often each word appears in the image's wide strings:

| ini | SavePath | resolves to |
|---|---|---|
| `Default.ini` | `..\Save` | `[Core.System] SavePath` -> `Save`: not on the disc |
| `UW.ini` | `../Save` | `[Core.System] SavePath` -> `Save`: not on the disc |
| `UnrealEngine2.ini` | `..\Save` | `[Core.System] SavePath` -> `Save`: not on the disc |
| question | count | detail |
|---|---|---|
| save | 0 | directories named Save/Saves: none |
| word | wide literals | where |
|---|---|---|
| `Profiles` | 2 | wide literals containing it: 0x004ffef0, 0x00515650 |
| `DefaultProfile` | 0 | wide literals containing it: none |
| `Saves` | 1 | wide literals containing it: 0x00523374 |
| `System\` | 4 | wide literals containing it: 0x004c9790, 0x004d0ff8, 0x004eab18, 0x004eab98 |
| `\System` | 4 | wide literals containing it: 0x004c8ccc, 0x004d0ff8, 0x004eab18, 0x004eab98 |
| `backslash` | 45 | of 5920 wide literals of 4+ characters carry a `\` |
<!-- /generated:save-path -->

## What the build's own path strings name (2026-11-14)

The section above ends by naming one address as the next read -- the `System\\` literal at `0x004c9790`, "stored at
`0x00149603`, inside no committed function". That address is a byte late, the isolation is not there, and the question
it was meant to answer has no answer of that shape: the file manager the save chain asks has no base directory at all.

**`0x004c9790` is pushed from eight sites, and every one is inside a mapped function** -- `0x0001d221`
(`FUN_0001d090`), `0x00027382` (`FUN_00027320`), `0x000c09c0` (`Engine_UExportCacheCommandlet`), `0x000f22e8` and
`0x000f247e` (`Core_UnClass`), `0x00149602` (`execGetMapName`, `0x00149460` plus 2,223 bytes), `0x00149ec6`
(`FUN_00149d20`) and `0x002fe75e` (`FUN_002fe5f0`). It is the prefix a map or package name takes when the file has to
be found under the game's `System\\` directory: a caller-side convention, not a directory the loader inherits.

**The object the save chain asks is an error file manager.** The slot census
(`inventory/file_manager_vtable.csv`) reads `[0x005bf514]` -> `0x005c1a0c` -> vtable `0x004d2fd0`: **25 slots**, the
run ending at the zero dword at `+0x64`, where the class's own wide-string pool begins. Sixteen of the 25 bodies are
eight bytes or shorter -- `ret 4`, `xor eax,eax; ret N`, `mov eax,1; ret` -- and two of the three that do work are
named by the map itself: `FFileManagerError_CreateFileReader` (`+0x04`) and `FFileManagerError_CreateFileWriter`
(`+0x08`), whose bodies log `Called FFileManagerError::CreateFileReader` and answer 0. Slot `+0x0c` is six bytes
answering `-1`, and the config loader takes that negative branch: `0x0028a13` calls `[edx+0x0c]` on the same singleton
and `0x0028a18` jumps away on it, which is why a `Tweak_` ini is never found.

**No slot answers a directory, and the one path-shaped slot returns its argument unchanged.** `+0x38`
(`0x00034210`, 33 bytes) constructs the empty string (#514). `+0x3c` (`0x00034240`, 531 bytes) tests its argument for
`L':'` and for a leading `L'\'` or `L'/'` and, on a hit, constructs the FString as it stands through `FUN_0001b260`
-- which is an FString constructor and nothing else. The `+0x38` getter is consulted only on the else arm, for a path
that is *not* rooted, and there it strips a `..\\`, `../`, `.\\`, `./` prefix. The loader composes
`\\Profiles\\<profile>\\<name>`, which starts with a separator, so it takes the verbatim arm: the composed string
reaches the package loader (`0x00073660` -> `Core_UnObj` with flags `0x1088`) exactly as composed, and there is no
base directory anywhere in that chain.

**The one absolute directory the build hard-codes is `D:\\System\\`** (`0x004d0ff8`, one site, `0x00288fb` inside
`0x00287d0`): a `Tweak_<name>` ini becomes `D:\\System\\<name>` with the underscores rewritten as separators (`0x5f`
-> `0x5c`), and `D:\\default.xbe` (`0x004d1144`) sits beside it. Every *relative* path the build composes
(`..\\System\\LIPSincData\\Controllers\\`, `..\\Saves\\`, `..\\Movies\\`) assumes the process runs with `System\\` as
its working directory -- precisely the segment the composed `\\Profiles\\` lacks.

**Two literals qualify the picture rather than contradict it.** The bare word `System` (`0x004d0e90`) is the
FName the class table is full of and carries 496 sites, so it is vocabulary rather than a path. And `Z:\\System`
(`0x004c8ccc`, four sites, all inside `0x0001a3b0`) belongs to the *other* file-manager class: `0x0001a3b0` is the
constructor that installs vtable `0x004c8db8` -- the table whose `+0x04` is a 1,082-byte `CreateFileReader` rather
than a stub. So the build does carry a real file manager; the save chain simply does not ask it.

**What that settles, and the next read.** The base-directory branch of the question closes negative: nothing prepends
`System\\`, so "the base is the game's `System\\`" is refuted and the loader's path is rootless wherever it goes. What
remains is whether the package loader itself resolves a root-relative name; that answer lives in the reader behind
`0x00073660`, one read away, and it is the next measurement rather than another boot.

    python3 tools/path-literals.py --xbe $DUMP/default.xbe -o inventory/path_literals.csv
    python3 tools/path-literals.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/path-literals.py --check       # 27 assertions, no dump

<!-- generated:path-literals -->
**The sweep.** 137 path-shaped wide literals in the image's data sections, 117 of them named by at least one instruction -- 801 sites in all, plus the 20 no instruction names.

**Every literal that names a save, profile or system directory**, with the instructions that carry it (at most six addresses per literal, at most four containing functions):

| literal | address | sites | at | in |
|---|---|---|---|---|
| `Z:\System` | `0x004c8ccc` | 4 | 0x0001a6d1 0x0001a6e6 0x0001a708 0x0001a713 | FUN_0001a3b0 |
| `System\` | `0x004c9790` | 8 | 0x0001d221 0x00027382 0x000c09c0 0x000f22e8 0x000f247e 0x00149602 | FUN_0001d090 FUN_00027320 Engine_UExportCacheCommandlet Core_UnClass |
| `System` | `0x004d0e90` | 496 | 0x00026efe 0x00029f9c 0x000313d0 0x00033d70 0x000349c0 0x000356d0 | FUN_00026ea0 no committed function XboxAudio_XboxAudioSubsystem XboxAudio_XHVVoiceSubsystem |
| `System/` | `0x004d0ea0` | 1 | 0x000273c3 | FUN_00027320 |
| `D:\System\` | `0x004d0ff8` | 1 | 0x000288fb | FUN_000287d0 |
| `..\System\LIPSincData\Controllers\` | `0x004eab18` | 4 | 0x000ba371 0x000ba3af 0x000ba880 0x000ba8b9 | LIPSincInitGame LIPSincInitLevel |
| `..\System\LIPSincData\Controllers\*.lad` | `0x004eab98` | 1 | 0x000ba2ef | LIPSincInitGame |
| `../System/` | `0x004f8298` | 1 | 0x000ed2ed | FUN_000ed2c0 |
| `defaultdevice` | `0x00508cd4` | 26 | 0x0013482c 0x00134dc5 0x00134e2b 0x001363d3 0x0013640c 0x0013676f | FUN_001347d0 FUN_00134d40 FUN_001363a0 FUN_00136740 |
| `Profiles` | `0x00515650` | 1 | 0x001be3bf | FUN_001be350 |
| `..\Saves\` | `0x00523374` | 1 | 0x002989f9 | FUN_00298980 |

**The file-manager table the save module's singleton carries** (`[0x005bf514]` -> `0x005c1a0c` -> table `0x004d2fd0`): every slot, and the wide literals its body names.

| slot | target | name | size | literals |
|---|---|---|---|---|
| `0x00` | `0x00031e70` | FUN_00031e70 | 3 | - |
| `0x04` | `0x000341a0` | FFileManagerError_CreateFileReader | 24 | `Called FFileManagerError::CreateFileReader` |
| `0x08` | `0x000341c0` | FFileManagerError_CreateFileWriter | 24 | `Called FFileManagerError::CreateFileWriter` |
| `0x0c` | `0x000341e0` | FUN_000341e0 | 6 | - |
| `0x10` | `0x00018bc0` | FUN_00018bc0 | 5 | - |
| `0x14` | `0x00018b70` | FUN_00018b70 | 5 | - |
| `0x18` | `0x000341f0` | FUN_000341f0 | 8 | - |
| `0x1c` | `0x00167f40` | FUN_00167f40 | 5 | - |
| `0x20` | `0x00034200` | - | - | - |
| `0x24` | `0x00018b70` | FUN_00018b70 | 5 | - |
| `0x28` | `0x00018bb0` | FUN_00018bb0 | 5 | - |
| `0x2c` | `0x00018bc0` | FUN_00018bc0 | 5 | - |
| `0x30` | `0x00034570` | FUN_00034570 | 22 | - |
| `0x34` | `0x00018b70` | FUN_00018b70 | 5 | - |
| `0x38` | `0x00034210` | FUN_00034210 | 33 | - |
| `0x3c` | `0x00034240` | FUN_00034240 | 531 | `..\`, `../` |
| `0x40` | `0x00018bb0` | FUN_00018bb0 | 5 | - |
| `0x44` | `0x00018b60` | - | - | - |
| `0x48` | `0x00018b70` | FUN_00018b70 | 5 | - |
| `0x4c` | `0x002783d0` | FUN_002783d0 | 6 | - |
| `0x50` | `0x00018b80` | - | - | - |
| `0x54` | `0x00018b90` | FUN_00018b90 | 22 | - |
| `0x58` | `0x00018bb0` | FUN_00018bb0 | 5 | - |
| `0x5c` | `0x00018bc0` | FUN_00018bc0 | 5 | - |
| `0x60` | `0x00018bd0` | FUN_00018bd0 | 5 | - |
<!-- /generated:path-literals -->

## What the file-manager table is asked for (2026-11-14)

The census above answers what the table *says*. This one answers what the image *asks it for*: `tools/fmgr-dispatch.py`
walks every `call DWORD PTR [reg+disp]` in the image and keeps the sites whose receiver is that object -- one link
back for `mov edx,[ecx]` immediately followed by the call, two for the `mov ecx,ds:0x5bf514; mov edx,[ecx]` pair --
and stops a walk at any call, jump or return between the two ends rather than guessing through it.

**157 sites reach the object, and they spread over 17 of the 25 slots.** Eight slots are called from nowhere at all
(`+0x20`, `+0x24`, `+0x40`, `+0x44`, `+0x50`, `+0x58`, `+0x5c`, `+0x60`). The busiest are `+0x0c` (28 sites, a body
that answers `-1`), `+0x30` (28, the only 22-byte body that does work), `+0x04` (**20 sites, the `CreateFileReader`
stub**) and `+0x10` (19, a body answering 0).

**120 of the 157 sites land on a body that answers a constant.** Summed over the slots whose whole body is one of
`ret N`, `xor eax,eax; ret N`, `or eax,-1; ret 4` or `mov eax,1; ret`: the reader (20) and writer (10) stubs that log
and answer 0, `+0x0c` (28) answering `-1`, `+0x10` (19) answering 0, `+0x28` (15), `+0x1c` (8), `+0x14` (7), `+0x18`
(4), `+0x34` (4), `+0x2c` (2) and one each at `+0x00`, `+0x48` and `+0x4c`. The 37 that reach real code are `+0x30`
(28), `+0x54` (5), `+0x38` (3, the device getter) and `+0x3c` (1).

**The save module is one of the 20.** `0x0013499b`, inside `FUN_00134960` of the `0x00134xxx` save/profile family,
dispatches slot `+0x04` -- the body that logs `Called FFileManagerError::CreateFileReader` and answers 0. So the
module does ask for a reader, and what it gets back is NULL whenever this object is the live one.

**The resolver slot is called exactly once, and not by the save chain.** `+0x3c` (`0x00034240`) has one site,
`0x002931dd` in `FUN_00293100`; the loader's composed path never passes through it -- it goes to `Core_UnObj`
(`0x00073660`), whose own sites are `+0x10` and `+0x1c`. So #515's statement that a root-relative path is returned
verbatim by that slot is true of the slot and irrelevant to this chain: nothing normalises the loader's path.

**Which object is live is the question this leaves.** The `.data` initializer at `[0x005bf514]` is `0x005c1a0c`, an
object whose first dword is the error table. The only store into that global in the whole `.text` is at `0x00169e3`
-- four instructions before the same function logs `Starting Engine` (`0x004d1124`, pushed at `0x00169f1`) -- and it
writes `ecx`, which came from `lea ecx,[ebp-0xb0]` at `0x00169d2`. That is an address in the calling function's own
frame, not a heap object, so what the build installs there is decided by that local's constructor; no instruction in
the image stores the error table's address as an immediate. Which file manager the engine actually holds is therefore
the next read, and it is a store census of one local, not another boot.

    python3 tools/fmgr-dispatch.py --xbe $DUMP/default.xbe -o inventory/fmgr_dispatch.csv
    python3 tools/fmgr-dispatch.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/fmgr-dispatch.py --check       # 16 assertions, no dump

<!-- generated:fmgr-dispatch -->
**The dispatch census.** 157 virtual calls reach the file-manager object through its own vtable (`ds:0x005bf514`), spread over 17 of the table's 25 slots. The busiest are `+0x0c` (28 sites), `+0x30` (28 sites), `+0x04` (20 sites), `+0x10` (19 sites).

**Per slot: what the entry answers, and how many sites call it.**

| slot | target | name | size | answers | sites | at | in |
|---|---|---|---|---|---|---|---|
| `0x00` | `0x00031e70` | FUN_00031e70 | 3 | `ret    0x4` | 1 | 0x0005aa87 | Core_UnMisc |
| `0x04` | `0x000341a0` | FFileManagerError_CreateFileReader | 24 | `logs "Called FFileManagerError::CreateFileReader"` | 20 | 0x000121df 0x000167e1 0x0001c01b 0x00051ff5 0x00052965 0x0005967f | FUN_00012110 Core_UnObjBas no committed function FUN_00059650 |
| `0x08` | `0x000341c0` | FFileManagerError_CreateFileWriter | 24 | `logs "Called FFileManagerError::CreateFileWriter"` | 10 | 0x00053298 0x00057106 0x0005a49d 0x0005a938 0x00072ac6 0x0008070e | FUN_00053190 no committed function FUN_0005a020 FUN_00070cb0 |
| `0x0c` | `0x000341e0` | FUN_000341e0 | 6 | `or     eax,0xffffffff ret    0x4` | 28 | 0x00028a13 0x0002e2db 0x0004e099 0x000572a6 0x0005a113 0x0005a38a | FUN_000287d0 XboxAudio_XboxAudioSubsystem FUN_0004de60 FUN_00057270 |
| `0x10` | `0x00018bc0` | FUN_00018bc0 | 5 | `xor    eax,eax ret    0xc` | 19 | 0x00029eae 0x0005abce 0x0006f36f 0x0006f45e 0x0007095f 0x00070a4f | no committed function Core_UnMisc Core_UnObj Core_UnObjBas |
| `0x14` | `0x00018b70` | FUN_00018b70 | 5 | `xor    eax,eax ret    0x4` | 7 | 0x0003529d 0x000352ed 0x00035555 0x000355c2 0x000c0d55 0x000ee343 | Core_UExporter Engine_UExportCacheCommandlet FUN_000ee2c0 FUN_00295570 |
| `0x18` | `0x000341f0` | FUN_000341f0 | 8 | `mov    eax,0x1 ret    0x1c` | 4 | 0x0011d28d 0x0011f38f 0x002d6615 0x002d6ab6 | FUN_0011ccd0 no committed function FUN_002d64a0 FUN_002d68e0 |
| `0x1c` | `0x00167f40` | FUN_00167f40 | 5 | `xor    eax,eax ret    0x14` | 8 | 0x0006f446 0x00070a35 0x00080232 0x000806eb 0x0011f352 0x0011f4bf | Core_UnObj Core_UnObjBas FUN_000801e0 no committed function |
| `0x20` | `0x00034200` | - | - | `xor    eax,eax xor    edx,edx ret    0x4` | 0 | - | - |
| `0x24` | `0x00018b70` | FUN_00018b70 | 5 | `xor    eax,eax ret    0x4` | 0 | - | - |
| `0x28` | `0x00018bb0` | FUN_00018bb0 | 5 | `xor    eax,eax ret    0x8` | 15 | 0x000803bf 0x000fa64e 0x0011eab7 0x00138a5c 0x0015efda 0x0015fc5a | no committed function Engine_UnCamera FUN_00138680 FUN_0015ef40 |
| `0x2c` | `0x00018bc0` | FUN_00018bc0 | 5 | `xor    eax,eax ret    0xc` | 2 | 0x001be975 0x002932a5 | FUN_001be920 FUN_00293220 |
| `0x30` | `0x00034570` | FUN_00034570 | 22 | `push   ecx mov    eax,DWORD PTR [esp+0x8] xor    ecx,ecx` | 28 | 0x0005a6ed 0x00068c23 0x00068fe4 0x0007f32d 0x0007f34b 0x000b1268 | FUN_0005a020 FUN_00068b20 FUN_0007ef20 FUN_000b0f20 |
| `0x34` | `0x00018b70` | FUN_00018b70 | 5 | `xor    eax,eax ret    0x4` | 4 | 0x0029304d 0x002930b6 0x002933de 0x00293401 | no committed function FUN_00293300 |
| `0x38` | `0x00034210` | FUN_00034210 | 33 | `push   ecx push   esi mov    esi,DWORD PTR [esp+0xc]` | 3 | 0x001be3a7 0x00293039 0x00293061 | FUN_001be350 no committed function |
| `0x3c` | `0x00034240` | FUN_00034240 | 531 | `push   0xffffffff push   0x3f4bb9 mov    eax,fs:0x0` | 1 | 0x002931dd | FUN_00293100 |
| `0x40` | `0x00018bb0` | FUN_00018bb0 | 5 | `xor    eax,eax ret    0x8` | 0 | - | - |
| `0x44` | `0x00018b60` | - | - | `jmp    0x7e800` | 0 | - | - |
| `0x48` | `0x00018b70` | FUN_00018b70 | 5 | `xor    eax,eax ret    0x4` | 1 | 0x00294810 | FUN_00294760 |
| `0x4c` | `0x002783d0` | FUN_002783d0 | 6 | `mov    eax,0x1 ret` | 1 | 0x0029270c | no committed function |
| `0x50` | `0x00018b80` | - | - | `xor    eax,eax ret    0x1c` | 0 | - | - |
| `0x54` | `0x00018b90` | FUN_00018b90 | 22 | `mov    edx,DWORD PTR [esp+0x4] mov    eax,DWORD PTR [ecx] push   edx` | 5 | 0x001be306 0x001be318 0x001bea11 0x001bf889 0x001bfaf2 | no committed function FUN_001be9c0 FUN_001bf7b0 |
| `0x58` | `0x00018bb0` | FUN_00018bb0 | 5 | `xor    eax,eax ret    0x8` | 0 | - | - |
| `0x5c` | `0x00018bc0` | FUN_00018bc0 | 5 | `xor    eax,eax ret    0xc` | 0 | - | - |
| `0x60` | `0x00018bd0` | FUN_00018bd0 | 5 | `xor    eax,eax ret    0x10` | 0 | - | - |
<!-- /generated:fmgr-dispatch -->

## Which object the file manager's global actually holds (2026-11-14)

The table above is `FFileManagerError`'s, and the question both earlier censuses leave is whether the build ever runs a
different file manager. `tools/fmgr-live-object.py` answers it from the identity of the singleton and from the
instructions that write the pointer, not from the `.data` initializer.

**The global is one of six.** `0x005bf500`..`0x005bf514` hold pointers into a single run of statics at
`0x005c1a08`..`0x005c1a1c`, four bytes apart: each object is one dword, its vtable, so none of them carries state. The
file manager's object is `0x005c1a0c`; the neighbour at `-4` is the allocator's, whose table's first slot is
`appMallocBeforeInit`. A row of memberless statics is a row of *fallback* implementations, not of subsystems the engine
built.

**Nothing in the image constructs one of them.** No instruction names any of the six tables as an immediate (the
`table .text` column is 0 on every row), so all six exist only as statics: a class whose constructor ran would store its
own table's address in code.

**The pointer is named by 194 reads and two writes, and only the first of them installs nothing.** This counts accesses,
not calls: the dispatch census above counts 157 virtual calls through the object. One write is `0x00169e3` in `Core_UnObjBas`,
four instructions before the same function logs `Starting Engine`: it stores `lea ecx,[ebp-0xb0]`, a frame dword whose
only other reference inside that 18,359-byte function is a zero store at `0x0012927`, so the pointer's target has no
table and cannot answer a dispatch. The other is `0x0005a9b2` in `Core_UnMisc` (`0x0005a990`), which stores
`[esp+0x28]` -- its own incoming argument -- so there the value is whatever that function's caller passed.

**What the two writers mean, corrected by the caller census below.** `0x00169e3`'s value is a frame dword whose only
other reference inside its function is a zero store, so it installs no built object. `0x0005a9b2`'s value comes from
`Core_UnMisc`'s caller, and there is exactly one: `0x00012b7d` in `Core_UnObjBas`. The argument is the caller's own
local -- `[ebp-0x954]`, pushed at `0x12b4c` -- and the same call re-points three of the neighbouring globals from its
other arguments, so the initialiser installs a *set* of pointers and the file manager's is the one that lives on the
stack. The `origin` column in the generated table follows that step row by row. Which class the frame object is, is the
next read.

    python3 tools/fmgr-live-object.py --xbe $DUMP/default.xbe -o inventory/fmgr_singletons.csv
    python3 tools/fmgr-live-object.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/fmgr-live-object.py --check       # 25 assertions, no dump

<!-- generated:fmgr-live-object -->
**The file-manager pointer is one of 6 consecutive globals.** `0x005bf500`..`0x005bf514` hold pointers into the same run of statics, 4 bytes apart -- one dword per object, the vtable, so none of them carries state and none of them is a subsystem object the engine built. The table behind `[0x005bf514]` names `FUN_00031e70 FFileManagerError_CreateFileReader FFileManagerError_CreateFileWriter`; the global beside it at -4 names `appMallocBeforeInit FUN_000340e0 FUN_00034100`.

**No instruction in the image names any of these 6 tables as an immediate** (the `table .text` column is 0 on every row), so nothing constructs one of these objects: they exist only as statics, and the dispatch census that reads this table describes the fallback implementation rather than a file layer the build assembled.

**Per global: the object it holds, its vtable, and every instruction that names it.**

| global | object | gap | vtable | names | table .text | table other | sites | reads | writes | value | source | slot | origin |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `0x005bf500` | `0x005c1a14` | 4 | `0x004d2e3c` | FUN_000ea7f0 FUN_00020f00 | 0 | 1 | 0x0001136d 0x000113a3 0x00011d80 0x00012410 0x00012420 0x00012431 | 1518 | 1 | `0x0005aa58 ecx` | `0x0005aa58 incoming` | - | 0x0005aa58 arg4 0x005bf3ec in Core_UnMisc @0x00012b7d |
| `0x005bf504` | `0x005c1a10` | 4 | `0x004d30e8` | FUN_000ea7f0 FUN_00020f00 | 0 | 1 | 0x00012a36 0x000166dd 0x000181f0 0x00018330 0x00018c60 0x00018d2f | 336 | 1 | `0x0005aa65 edx` | `0x0005aa65 incoming` | - | 0x0005aa65 arg5 0x005bf3f0 in Core_UnMisc @0x00012b7d |
| `0x005bf508` | `0x005c1a1c` | - | `0x004d2e4c` | FUN_000ea7f0 FUN_00020f00 FUN_000357a0 | 0 | 1 | 0x000121ca 0x000167c8 0x0001c012 0x0001c056 0x000275cf 0x0003bea9 | 60 | 0 | - | - | - | - |
| `0x005bf50c` | `0x005c1a18` | 4 | `0x004d2e44` | FUN_00020f00 FUN_000ea7f0 FUN_000357a0 | 0 | 1 | 0x0005327f | 1 | 0 | - | - | - | - |
| `0x005bf510` | `0x005c1a08` | 4 | `0x004d2eb0` | appMallocBeforeInit FUN_000340e0 FUN_00034100 | 0 | 1 | 0x00012746 0x00016d85 0x00016dca 0x00016e80 0x00016ea0 0x00016fd0 | 2445 | 2 | `0x00012746 0x59ed68 ; 0x0005aa70 ecx` | `0x00012746 immediate ; 0x0005aa70 incoming` | - | 0x0005aa70 arg3 0x0059ed68 in Core_UnMisc @0x00012b7d |
| `0x005bf514` | `0x005c1a0c` | 4 | `0x004d2fd0` | FUN_00031e70 FFileManagerError_CreateFileReader FFileManagerError_CreateFileWriter | 0 | 1 | 0x000121cf 0x000167cd 0x000169e3 0x0001c009 0x0001c04d 0x000284f5 | 194 | 2 | `0x000169e3 ecx ; 0x0005a9b2 eax` | `0x000169e3 frame ebp-b0 ; 0x0005a9b2 incoming` | 0x000169e3: 0x00012927 write 0x000169d2 read | 0x0005a9b2 arg7 [ebp-0x954] in Core_UnMisc @0x00012b7d |

**The access census.** `[0x005bf514]` is named by 194 read(s) and 2 write(s) in the image: `0x000169e3 frame ebp-b0`; `0x0005a9b2 incoming`. A value whose last writer is a frame slot is followed into that slot's own references inside the containing function -- `0x000169e3: 0x00012927 write 0x000169d2 read` -- because a slot that is never given a table cannot answer a dispatch, and then the write installs no second file manager.

**The value is followed out of the callee, and it is the caller's own frame.** `origin` takes the `incoming` source one step further: the slot `[esp+N]` the callee reads is argument `N/4` less every push and `sub esp` made since its entry, and each `E8` site that calls the callee is decoded back to its push run, so the argument is named by the value the caller actually hands over. `0x0005a9b2 arg7 [ebp-0x954] in Core_UnMisc @0x00012b7d` -- a pointer into a caller frame, pushed there and stored before the callee does anything else, so the global is live from that instruction on and answers a dispatch as an object.

<!-- /generated:fmgr-live-object -->

**Which class the initialiser builds there.** Following the frame local one step further answers it: the first
instruction that takes `[ebp-0x954]` as a receiver is `0x128ff` (`lea ecx` then `call 0x1a3b0`), and that constructor
writes its table into the object at `0x1a3c2` -- `mov DWORD PTR [edi],0x4c8db8`. So the file manager the game hands
itself is an object with table `0x4c8db8`, a table the class census does not carry (its only dword reference in the image
is that one store, so it is a native C++ object rather than one of the 492 script class tables), and the constructor
also builds a string `Z:\Cache` beside it at `+0x4`.

Compared against the static error object's table (`0x4d2fd0`) slot by slot, `0x4c8db8` overrides **13 of 25** slots and
keeps 12 (mostly the trivially-short ones). The two the save chain asks about are among the overrides: `+0x04`
(`CreateFileReader`) is `0x0001d290` and `+0x08` (`CreateFileWriter`) `0x0001d6d0`, against the error manager's
log-and-return-0 stubs -- so the earlier "asks for a reader and gets NULL" describes the fallback, not this object. The
device-path resolver `+0x38` is also overridden (`0x0001c4f0` against the empty-string body `0x00034210`), so the
rootless `\Profiles\<profile>\` path above is a statement about the *static* object only.

    python3 tools/fmgr-live-object.py --live $DUMP/default.xbe --live-out inventory/fmgr_live_vtable.csv
    # -> live table 0x004c8db8 built by 0x0001a3b0, static 0x004d2fd0 -- 12 same, 13 differ

**Which object the engine phase runs with, and why the frame is not the problem.** #519's open question -- is the
frame object still live when the loader runs -- is answered by the containing function rather than by another table.
`0x0012620` is `main`: it has one direct caller (`0x33f2ff`, in the CRT start-up and outside every mapped function) and
one `ret` (`0x16cfe`), and its body prints `Startup time %f`, `MEMORY: %d KByte of physical memory in use`,
`Map linearization complete`, `Starting Engine`, `Launching map [%s]`, `Entering Loop` and `Engine->Init: %f`. The frame
it builds in its prologue is therefore the session's frame, and a pointer into it is not a dangling object; the `.data`
initializer (`0x005c1a0c`, table `0x004d2fd0`) describes only the state before `main` runs.

It also installs **twice**. `0x0005a9b2` (in `Core_UnMisc`, reached from `main`'s call at `0x12b7d`) installs the
`[ebp-0x954]` object of #519 -- table `0x004c8db8` -- and `0x169e3` re-points the global at `[ebp-0xb0]`, a second frame
slot, zeroed at `0x12927` and handed to `call 0x6c750` with `ecx` still holding it, with `Starting Engine` as the next
literal printed.

**That call is not a constructor, so the second slot is never built.** `0x6c750` writes nothing through `ecx` at all
(`builder stores` is `none` on its row) and reads the object array at `0x006c2188` with its count at `0x006c218c`: it is
a purge over the loaded objects (326 bytes, ending `ret` at `0x6c895`), not a build. The only write `[ebp-0xb0]` ever
receives is the zero at `0x12927`, so `0x169e3` points the global at an object that does not exist -- which reads as a
teardown of the file layer where the phase column puts it next to `Starting Engine`, not as the engine's install.
The order is not a decoding artifact: the region is fall-through code (`0x1670b` and `0x16739` branch into it at
`0x16742`, and no branch jumps over it), so the sequence stands and the *reading* is what stays open. What is certain is
that the object #519 measured is not the one installed last.

The next instrument for it is the object array itself: `0x77210` is the lookup the purge calls per element, and the
class of the object a reload leaves behind is in the `[+0x24]`/`[+0x498]` fields this body tests rather than in the
vtable of either file-manager table.

What this does settle is the direction of the global: **the load path's own bodies never read it.** `0x00134d40` (the
loader) and `0x00149370` (`GameInfo.LoadSaveGame`) name `[0x005bf514]` zero times; two functions in the loader's own
neighbourhood (`0x001347d0`, `0x00134960`) do, so the reader is one call deeper. The writer `0x00138680` reads it once
and the serializer `0x0006f4b0` three times; `main` itself reads it once, at `0x167cd`, and dispatches `+0x04`
(`CreateFileReader`) through it -- the live object answers a reader call inside `main`'s own body.

    python3 tools/fmgr-installs.py --xbe $DUMP/default.xbe -o inventory/fmgr_installs.csv
    python3 tools/fmgr-installs.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/fmgr-installs.py --check       # 24 assertions, no dump

<!-- generated:fmgr-installs -->
**The file-manager global has 2 store(s) in the image, and they are not the same kind of object.** The rows below are the image's own initializer and every store, in the order the build performs them, with the frame slot each value points at, the call that builds that slot, the table that call stores at `[this]`, and the literal the build prints next -- the phase the store belongs to. `at` orders them: an install inside a callee is placed at the call site that enters it, one inside the function the CRT calls at its own address. The static row's `reads` cell is the count of instructions in the image that name the global.

| store | at | in | fn size | rets | callers | kind | value | slot | builder | table | builder stores | builder globals | phase | slot refs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `image` | image | - | - | - | - | data-initializer | `0x005c1a0c` | - | - | 0x004d2fd0 | - | - | pre-main | - |
| `0x0005a9b2` | 0x00012b7d call | Core_UnMisc | 3181 | - | 0x00012b7d | incoming | `eax` | - | - | - | - | - | - | - |
| `0x000169e3` | 0x000169e3 own body | Core_UnObjBas | 18359 | 0x00016cfe | 0x0033f2ff | frame | `ecx` | ebp-b0 | 0x000169d2 -> call 0x0006c750 | none | none | 0x006c218c 0x006c2188 | 0x169f1 Starting Engine | 0x00012927 write 0x000169d2 read |

**What the columns decide.** A `frame` value means the object lives in the containing function's own frame, so whether it can be dispatched through depends on that frame outliving the reader: the `rets` column is the function's whole exit set, and a store that precedes every one of them leaves the frame alive only as long as the function does. The `callers` column says how that function is entered -- one site outside every mapped function is the CRT's call into `main`. `table` is `none` when the call the slot is handed to stores no immediate at `[this]`, which is the difference between an install and a pointer to scratch, and `builder stores` is the `[ecx]`-relative write census of the call the slot was handed to: a constructor writes its table there, a function that only walks a global array (`builder globals`) does not. A `-` in `rets` is a body whose decoded sweep has no `ret` at all -- it leaves through a jump, so its exit set is not this column's to state.
<!-- /generated:fmgr-installs -->


## What the array at `0x006c2188` holds, and why the purge cannot pick a different element (U5)

`#521` read `main`'s call at `0x169e9` into `0x6c750` and found a 326-byte body that walks an array at `0x006c2188`,
calls `0x77210` on each element, and dispatches on the result. The question left open was *which element the file
layer gets* -- with the suspicion that a reload could leave a different object in that slot than the one the vtable
census found. Two measurements close it, and both are in the image alone:

**The per-element call is an identity.** `0x77210` (239 bytes) takes one object and returns that same object on every
path: one `ret`, and the instruction that writes `eax` before it is `mov eax,edi`, where `edi` is loaded once from
`[esp+0xc]` and never rewritten. It does other work on the way -- it walks the object's `[+0x24]` chain looking for a
cached object it resolves from the literal at `0x4c793c` (`Core`), and when the walk fails it builds a string and logs
the literal at `0x4d43dc` (`Cast of %s to %s failed`) -- but the failure arm answers the argument too. So no element
can be substituted by that call, and "which element does the file layer get" has no second answer to find.

**The array is a container registry, and its elements are containers.** The three dwords at `0x006c2188`,
`0x006c218c` and `0x006c2190` are one `TArray` -- data, count, capacity -- named by 48 instructions in the image, and
the body that clears it zeroes the count and the capacity together (`0x00069c38`, `0x00069c3e`) rather than only the
count. Two sites push into it through the capacity check (`0x0005283e` and `0x0005311e`, each preceded by a grow at
`0x00052805`/`0x000530e5`), and both are in the same unnamed function pair that also resets the array. The purge's own
field map says what the elements are for: a filter field at `+0x28` compared to the caller's second argument, a record
array at `+0x7c`/`+0x80` walked by `0x1c`, a second at `+0x88`/`+0x8c` walked by `0x24`, an object pointer inside each
`0x24` record at `+0x1c`, that object's class at `+0x24`, and one byte of the class at `+0x498` tested for its sign.
Those are import and export tables with a linker root, i.e. the loader objects a package is loaded through -- not a
slot that holds a file manager.

**The `[+0x498]` byte is a class flag, not object data.** It is read off the *class* of an exported object, one byte
wide (`mov cl,BYTE PTR [eax+0x498]; test cl,cl; js`), so the sign test reads bit 7 of the first byte of the class's
flag dword. That is why the earlier reading could not find the object a reload leaves behind in either file-manager
table: the field belongs to the class, and the object is whatever the record names.

<!-- generated:linker-purge -->
**The array at `0x006c2188` is one `TArray` -- data, count, capacity -- and 48 instructions name one of its three dwords:** 34 read(s), 6 write(s), 8 address(es) taken. Every write in this census is the array's own bookkeeping (the count at `0x00052815`/`0x000530f5`, the capacity at `0x00052834`/`0x00053114`, both zeroed together at `0x00069c38`): an element goes in through the data pointer after that capacity check, so this census sees the registry, not what is registered.

| at | in | what | instruction |
|---|---|---|---|
| `0x00051b48` | FUN_00051ad0 | address 0x006c2188 | `mov ecx,0x006c2188` |
| `0x00051ec2` | Core_UnTemplate | read 0x006c2188 | `mov edx,ds:0x006c2188` |
| `0x00051f2a` | Core_UnTemplate | address 0x006c2188 | `mov ecx,0x006c2188` |
| `0x0005282e` | - | address 0x006c2188 | `mov ecx,0x006c2188` |
| `0x0005283e` | - | read 0x006c2188 | `mov eax,ds:0x006c2188` |
| `0x0005310e` | - | address 0x006c2188 | `mov ecx,0x006c2188` |
| `0x0005311e` | - | read 0x006c2188 | `mov eax,ds:0x006c2188` |
| `0x000635a6` | FUN_000635a0 | read 0x006c2188 | `mov ecx,ds:0x006c2188` |
| `0x00066431` | Core_UnObj | read 0x006c2188 | `mov eax,ds:0x006c2188` |
| `0x00066612` | Core_UnObj | read 0x006c2188 | `mov ecx,ds:0x006c2188` |
| `0x00069c32` | Core_UnObj | address 0x006c2188 | `mov ecx,0x006c2188` |
| `0x0006c772` | FUN_0006c750 | read 0x006c2188 | `mov ecx,ds:0x006c2188` |
| `0x0006c862` | FUN_0006c750 | read 0x006c2188 | `mov ecx,ds:0x006c2188` |
| `0x00072272` | FUN_00070cb0 | read 0x006c2188 | `mov ecx,ds:0x006c2188` |
| `0x00072bce` | Core_UnObj | read 0x006c2188 | `mov edx,ds:0x006c2188` |
| `0x00072e50` | Core_UnObj | read 0x006c2188 | `mov edx,ds:0x006c2188` |
| `0x00148257` | FUN_00148250 | address 0x006c2188 | `push 0x006c2188` |
| `0x00413cf1` | - | address 0x006c2188 | `mov ecx,0x006c2188` |
| `0x00418d51` | FUN_00418d50 | address 0x006c2188 | `mov ecx,0x006c2188` |
| `0x0003e45f` | FUN_0003e3b0 | read 0x006c218c | `mov ecx,ds:0x006c218c` |
| `0x0003e4bd` | FUN_0003e3b0 | read 0x006c218c | `mov ecx,ds:0x006c218c` |
| `0x00051ea5` | Core_UnTemplate | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x00051ee8` | Core_UnTemplate | read 0x006c218c | `cmp esi,ds:0x006c218c` |
| `0x00051f07` | Core_UnTemplate | read 0x006c218c | `cmp edi,ds:0x006c218c` |
| `0x00051f36` | Core_UnTemplate | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x0005201c` | - | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x00052067` | - | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x00052805` | - | read 0x006c218c | `mov ecx,ds:0x006c218c` |
| `0x00052815` | - | write 0x006c218c | `mov ds:0x006c218c,ecx` |
| `0x0005298c` | - | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x000529d7` | - | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x000530e5` | - | read 0x006c218c | `mov ecx,ds:0x006c218c` |
| `0x000530f5` | - | write 0x006c218c | `mov ds:0x006c218c,ecx` |
| `0x00066421` | Core_UnObj | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x0006645a` | Core_UnObj | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x00066602` | Core_UnObj | read 0x006c218c | `cmp esi,ds:0x006c218c` |
| `0x00069c38` | Core_UnObj | write 0x006c218c | `mov ds:0x006c218c,esi` |
| `0x0006c752` | FUN_0006c750 | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x0006c850` | FUN_0006c750 | read 0x006c218c | `mov esi,ds:0x006c218c` |
| `0x0007224d` | FUN_00070cb0 | read 0x006c218c | `mov eax,ds:0x006c218c` |
| `0x0007234a` | FUN_00070cb0 | read 0x006c218c | `mov ecx,ds:0x006c218c` |
| `0x00072bc2` | Core_UnObj | read 0x006c218c | `mov esi,ds:0x006c218c` |
| `0x00072e4a` | Core_UnObj | read 0x006c218c | `mov esi,ds:0x006c218c` |
| `0x0005280a` | - | read 0x006c2190 | `mov eax,ds:0x006c2190` |
| `0x00052834` | - | write 0x006c2190 | `mov ds:0x006c2190,edx` |
| `0x000530ea` | - | read 0x006c2190 | `mov eax,ds:0x006c2190` |
| `0x00053114` | - | write 0x006c2190 | `mov ds:0x006c2190,edx` |
| `0x00069c3e` | Core_UnObj | write 0x006c2190 | `mov ds:0x006c2190,esi` |

**The per-element call cannot substitute an object.** `0x77210` answers its own argument on every path -- 1 ret; before it: mov    eax,edi. So the question the last reading left open (which element the file layer gets) has no second answer to find: the purge can only ever see the element it took out of the array, and the only thing that call changes is whether it logs the literal at `0x4d43dc` first.

| at | in | what | evidence |
|---|---|---|---|
| `0x000772fe` | FUN_00077210 | returns its argument | `1 ret; before it: mov    eax,edi` |
| `0x00077212` | FUN_00077210 | argument | `1 write(s) to edi: mov    edi,DWORD PTR [esp+0xc]` |

**What the purge reads.** The elements are containers with two record arrays, not a slot that holds a file manager: the body walks `+0x7c`/`+0x80` by 0x1c and `+0x88`/`+0x8c` by 0x24, takes each export record's object at `+0x1c`, and reads one byte at `+0x498` of that object's class -- a signed byte, so the last gate is a bit in the class's flags, not data on the object.

| offset | in | role | width | instruction |
|---|---|---|---|---|
| `0x00c` | FUN_0006c750 | dispatch | dword | `call   DWORD PTR [edx+0xc] -- vtable slot called with 1 when the record's object is dropped` |
| `0x014` | FUN_0006c750 | first-record-object | dword | `mov    ecx,DWORD PTR [ecx+eax*1+0x14] -- object pointer inside a 0x1c record` |
| `0x018` | FUN_0006c750 | first-record-index | dword | `mov    DWORD PTR [ecx+eax*1+0x18],0xffffffff -- index field set to -1 when the record is dropped` |
| `0x01c` | FUN_0006c750 | second-record-object | dword | `lea    eax,[edx+edi*1+0x1c] -- object pointer inside a 0x24 record` |
| `0x024` | FUN_0006c750 | record-object-class | dword | `mov    eax,DWORD PTR [eax+0x24] -- class of the object that record names` |
| `0x028` | FUN_0006c750 | filter | dword | `cmp    DWORD PTR [esi+0x28],edi -- compared to the caller's second argument on both walks` |
| `0x07c` | FUN_0006c750 | first-record-array | dword | `mov    ecx,DWORD PTR [esi+0x7c] -- data pointer of the 0x1c-stride walk` |
| `0x080` | FUN_0006c750 | first-record-count | dword | `mov    eax,DWORD PTR [esi+0x80] -- count of the 0x1c-stride walk` |
| `0x088` | FUN_0006c750 | second-record-array | dword | `mov    edx,DWORD PTR [esi+0x88] -- data pointer of the 0x24-stride walk` |
| `0x08c` | FUN_0006c750 | second-record-count | dword | `mov    eax,DWORD PTR [esi+0x8c] -- count of the 0x24-stride walk` |
| `0x498` | FUN_0006c750 | class-flag-byte | byte | `mov    cl,BYTE PTR [eax+0x498] -- one byte of that class, tested for its sign` |

**`+0x498` is a class displacement the image uses 93 times, and only 4 of them are byte reads.** The purge's is one of the byte ones, so the flag it tests is the top bit of the first byte of the class's flag dword -- the same dword other bodies test whole (`test dword [class+0x498],0x...`).

<!-- /generated:linker-purge -->

    python3 tools/linker-purge.py --xbe $DUMP/default.xbe -o inventory/linker_purge.csv
    python3 tools/linker-purge.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/linker-purge.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/linker-purge.py --check        # 20 assertions, synthetic instructions, no dump

**What this leaves.** The `+0x498` bit was measured as a *format* (one byte of the class's flag dword, tested
for its sign) and not yet *named*; the next section names it -- the word is `UClass`'s class flags, the gate's
bit is `0x80`, and the dword's top bit is not reachable from that byte. That is a different question from the
array, and it does not block the save chain: the purge is a teardown that runs before `Starting Engine`, and the
file-manager global it was suspected of selecting from is set by the store at `0x169e3` from `main`'s own frame
slot, not from an element of this array.


## What the class flag word at `+0x498` holds

The purge's last gate reads one byte off a class, so the question `#522` left open is what that word is and which
bit its sign test sees. It is `UClass`'s class-flag dword: the class constructor inherits a masked subset of its
super's word into it, the registration overload sets the two compiler bits on every script class built from the
registration's data, and the rest of the image only ever reads single bits out of it. The mask is this build's own
constant, and the shipped packages let it be *tested* rather than assumed -- a bit the constructor inherits is
almost never lost between a class and its super, while the bits it leaves out are lost by the hundred.

<!-- generated:class-flag-word -->
**The word at `+0x498` is `UClass`'s class-flag dword, and the constructor names the bits it inherits: `0x0e0f86ec`.** `Core_UnClass` (`0x00039520`) reads the super's word through `[this+0x28]`, `and`s it with that immediate and `or`s it into its own -- so a class starts with every inheritable bit its parent had, and with none of the others. `FUN_0003af60`, the registration overload every script class is built from, then `or`s `0x12` (bits 1 and 4) into the same word; both of those are *outside* the mask, which is what a compiler/loader pair of bits looks like.

| at | in | what | instruction |
|---|---|---|---|
| `0x00039641` | Core_UnClass | inherit mask 0x0e0f86ec | `mov eax,[esi+0x28]; mov eax,[eax+0x498]; and eax,0x0e0f86ec; or ecx,eax; mov [esi+0x498],ecx` |
| `0x0003af96` | FUN_0003af60 | registration sets 0x12 | `or ecx,0x12; mov [esi+0x498],ecx` |
| `0x0006c803` | FUN_0006c750 | purge byte gates 0x80 | `mov cl,[eax+0x498]; test cl,cl; js -- byte 0 of the dword, sign bit` |

**The shipped packages agree with the mask, and the disagreement is per bit.** Across 2319 classes and 2318 class/super pairs, the bits the constructor leaves out are dropped between a class and its super by the hundred (`0x1` 827 times, `0x800` 160 times, `0x100` 101 times), while the in-mask bits are dropped only on 28 class(es): `0x200` 25, `0x4` 1, `0x2000000` 1, `0x4000000` 1. `0x200` is the value the reader itself spells twice, `NoUserCreate` and `Placeable`, which is why it is the one that moves most.

**The purge's byte is `0x80`, not the dword's top bit.** A byte load at `+0x498` addresses the least-significant byte of the word, and `test cl,cl; js` tests that byte's sign, so the only bit the gate can see is bit 7 -- `0x80`. `0x80000000` is not reachable from this instruction at all, whatever the earlier reading assumed about "the top bit of the first byte".

| bit | name | in the inherit mask | classes | added by a class | dropped by a class | exemplar |
|---:|---|---:|---:|---:|---:|---|
| 0x01 | Abstract |  | 121 | 46 | 827 | `Actor` |
| 0x02 | Compiled |  | 2316 | 1 | 3 | `ACTION_ActivateAstralSight` |
| 0x04 | Config | yes | 1575 | 52 | 1 | `AIController` |
| 0x08 | Transient | yes | 42 | 15 | 0 | `BufferedTCPLink` |
| 0x10 | Parsed |  | 2316 | 1 | 3 | `ACTION_ActivateAstralSight` |
| 0x20 | Localized | yes | 1049 | 71 | 0 | `ACTION_ActivateAstralSight` |
| 0x40 | SafeReplace | yes | 13 | 2 | 0 | `BackBufferTexture` |
| **`0x80`** | RuntimeStatic | yes | 1 | 1 | 0 | `UPreviewMapListClient` |
| 0x100 | NoExport |  | 45 | 5 | 101 | `BackBufferTexture` |
| 0x200 | NoUserCreate/Placeable | yes | 607 | 90 | 25 | `AIScript` |
| 0x400 | PerObjectConfig | yes | 3 | 3 | 0 | `MaplistRecord` |
| 0x800 | NativeReplication |  | 21 | 8 | 160 | `Actor` |
| 0x1000 | EditInlineNew |  | 707 | 44 | 3 | `ACTION_ActivateAstralSight` |
| 0x2000 | CollapseCategories |  | 355 | 23 | 1 | `ACTION_ActivateAstralSight` |
| 0x4000 | ExportStructs |  | 8 | 8 | 24 | `AmbientSound` |
| 0x8000 | unnamed | yes | 1 | 1 | 0 | `UPreviewMapListClient` |
| 0x10000 | unnamed | yes | 3 | 3 | 0 | `UPreviewMapListClient` |
| 0x20000 | unnamed | yes | 2 | 2 | 0 | `UPreviewMapListClient` |
| 0x40000 | unnamed | yes | 2 | 2 | 0 | `UPreviewMapListClient` |
| 0x80000 | unnamed | yes | 0 | 0 | 0 |  |
| 0x100000 | unnamed |  | 1 | 1 | 0 | `UPreviewRootWindow` |
| 0x200000 | Instanced |  | 237 | 2 | 0 | `ChapterList` |
| 0x400000 | HideDropDown |  | 2 | 2 | 1 | `GameInfo` |
| 0x800000 | CacheExempt |  | 5 | 5 | 2 | `CrosshairPack` |
| 0x1000000 | ParseConfig |  | 1 | 1 | 1 | `UPreviewSkinnedDialogClientWindow` |
| 0x2000000 | Cacheable | yes | 218 | 8 | 1 | `APC` |
| 0x4000000 | Exported | yes | 591 | 28 | 1 | `AIController` |
| 0x8000000 | unnamed | yes | 13 | 3 | 0 | `AIMSG_Cloak` |
| 0x10000000 | Intrinsic |  | 1 | 1 | 0 | `UPreviewSkinnedButton` |
| 0x20000000 | NativeOnly |  | 0 | 0 | 0 |  |
| 0x40000000 | PerObjectLocalized |  | 3 | 3 | 1 | `UPreviewRootWindow` |
| 0x80000000 | HasCrossLevelRefs |  | 3 | 2 | 0 | `UPreviewMapListClient` |

**The image names the displacement 109 times: 84 sit inside a mapped function and 84 of those are instruction boundaries; 25 are outside the symbol map.** 11 are byte-wide, and 31 carry one immediate -- the readers. A reader's immediate is the clearest evidence for what a bit means: `Core_UnTemplate` tests `0x20` (a localised template), `Core_UnObj` tests `0x4` and `0x8`, `Engine_UnLevAct` tests `0x1`.

| mask | name | at | in |
|---:|---|---|---|
| `0x1` | Abstract | `0x0006c96b` | Core_UnObj |
| `0x1` | Abstract | `0x0015975f` | Engine_UnLevAct |
| `0x1` | Abstract | `0x002840a6` | FUN_002840a0 |
| `0x4` | Config | `0x0006a2dd` | Core_UnObj |
| `0x4` | Config | `0x0006a293` | FUN_0006a260 |
| `0x8` | Transient | `0x0006cd5f` | Core_UnObj |
| `0x20` | Localized | `0x000c6add` | Core_UnTemplate |
| `0x20` | Localized | `0x00064f04` | FUN_00064f00 |
| `0x8000` | unnamed | `0x00067641` | FUN_00067450 |
| `0x10000` | unnamed | `0x0010b553` | Engine_UnChan |
| `0x10000` | unnamed | `0x00046fb8` | Native_0 |
| `0x10000` | unnamed | `0x00047033` | Native_1 |
| `0x20000` | unnamed | `0x0010a5ef`, `0x0010ae4b` | Engine_UnChan |
| `0x40000` | unnamed | `0x000630e2` | FUN_000630b0 |
| `0x80000` | unnamed | `0x0010ac92` | Engine_UnChan |
| `0x80000` | unnamed | `0x00062fd6` | FUN_00062fc0 |
| `0x80000` | unnamed | `0x0006301c` | FUN_00062ff0 |
| `0x80000` | unnamed | `0x0006482c` | FUN_00064770 |
| `0x80000` | unnamed | `0x00212b82` | execPlaySound |
| `0x200000` | Instanced | `0x00078742` | Core_UnProp |
| `0x1000000` | ParseConfig | `0x0003aaba` | Core_UnClass |
| `0x4000000` | Exported | `0x0006ca8b` | Core_UnObj |
| `0x4000000` | Exported | `0x001598ad` | Engine_UnLevAct |
| `0x4000000` | Exported | `0x000490a1` | FUN_00048e90 |
| `0x4000000` | Exported | `0x00062535` | FUN_00062520 |
| `0x4000000` | Exported | `0x00073d3a` | FUN_00073cf0 |
| `0x4000000` | Exported | `0x00076660` | FUN_000765c0 |
| `0x8000000` | unnamed | `0x0006ce14` | Core_UnObj |

<!-- /generated:class-flag-word -->

**What this leaves.** The gate's bit is `0x80` and exactly one shipped class carries it, so over the 18 packages
the purge's last test can select only that class's objects -- which is consistent with a teardown that has nothing
per-package to do here. Whether that class is loaded when the purge runs is a runtime question, and the two bits
the mask and the readers cannot name (`0x8000`, `0x10000`..`0x80000`) are the lowest-cost next read: the registration
data of a class that carries one of them names it from the package side.


## What each class-flag bit's readers do with the answer (U5)

The word at `+0x498` is `UClass`'s class flags and its inherit mask is `0x0e0f86ec`, but five of the bits the
mask passes down have no name in the pinned reader's table. The readers cannot supply a name; they do say what
the answer is *used for*, which is the part a rebuilt package has to reproduce. `tools/class-flag-readers.py`
decodes every masked test of `[x+0x498]` in the image, the branch that consumes the flags, and what each edge of
that branch does -- so each bit gets the answers its readers take.

* **`0x8000` is the object walk's skip bit.** Its one reader is the recursive collector at `0x00067641`
  inside `0x00067450`: the set edge jumps past the object *and* past everything it would have collected, the
  clear edge re-enters the collector. One shipped class carries it (`UPreviewMapListClient`), so over the 18
  packages the walk skips that class's objects and no others.
* **`0x10000` and `0x20000` are read on a property element's class**, in the two native frames
  (`0x00046fb8`, `0x00047033`) and in `Engine_UnChan` (`0x0010b553`, `0x0010a5ef`, `0x0010ae4b`). The edges
  differ in whether the element is handed to a virtual (`+0x94`/`+0xa4` on the property, `+0xb8` on the frame)
  or passed over, and in the replication body at `0x0010a3e0` the difference is literal: the clear edge
  zero-fills the element, the set edge keeps it and `and`s out the property's own mask (`& ~piVar[0x1b]`).
* **`0x40000` is a resolver's answer bit.** The name-hash walk at `0x000630b0` returns the object it found only
  when the bit is set (`0x000630e2`: set edge `returns`, clear edge `zeroes`), so a class without it is
  invisible to that lookup even while its name is in the table.
* **`0x80000` is required by five readers and set by no shipped class.** The two FindObject-shaped bodies
  (`0x00062fd6` needs it and answers 0 otherwise, `0x0006301c` accepts only a hit that carries it), the hash
  walk at `0x0006482c`, `Engine_UnChan` at `0x0010ac92` and `execPlaySound` at `0x00212b82` all test it, and
  none of the 2,319 shipped classes has it -- the constructor's inherit is the only composer of a mask and the
  registration adds `0x12`, so over the shipped corpus those five lookups answer "nothing" every time. That is
  a constant, not a behaviour, and it is the cheapest thing for a rebuild to get wrong: a file written with a
  word the loader can compose would make them answer differently.
* **`0x8000000` is the one unnamed bit the packages answer for.** Its single reader is `Core_UnObj` at
  `0x0006ce14`, and its 13 carriers are the AI message classes and the `MOVE_ORDER_*` family -- all scripted
  subclasses of `AIMessage`/`MoveOrder`, which is what a modifier the *script* compiler owns looks like rather
  than a serializer flag.

**What this buys.** A rebuilt class's word is not cosmetic: it selects which arm of the package serialiser and
the channel's replication loop runs, whether the object walk sees the object, and whether four resolvers answer
at all. The bits that matter are carried by three UPreview windows and thirteen AI/move-order classes, so the
rebuild has to reproduce them from those classes' own declarations rather than from a default.

<!-- generated:class-flag-readers -->
**13 of the 32 bits are read somewhere in the image; 18 are carried by a shipped class and read nowhere, and 1 is read and carried by no class.** Every masked test of `[x+0x498]` in the code sections is in `inventory/class_flag_readers.csv` -- one row per site with the operand width, the branch that consumes the flags and what each edge does (`zeroes` = it clears `eax`, `returns` = it leaves the function, `calls` = it calls out); this table aggregates them per bit. The `set`/`clear` columns say which edge the bit's value selects, so a reader whose set edge `zeroes` is a reader that needs the bit **clear**.

| bit | name in the reader | in mask | classes carrying | readers | set edge | clear edge |
|---:|---|---:|---:|---:|---|---|
| `0x1` | Abstract |  | 121 class(es), e.g. Actor | 3 | continues 2, returns 1 | branches 1, calls 2 |
| `0x4` | Config | yes | 1575 class(es), e.g. AIController | 2 | branches 1, continues 1 | branches 2 |
| `0x8` | Transient | yes | 42 class(es), e.g. BufferedTCPLink | 1 | continues 1 | continues 1 |
| `0x20` | Localized | yes | 1049 class(es), e.g. ACTION_ActivateAstralSight | 2 | branches 1, calls 1 | branches 1, returns 1 |
| `0x8000` | unnamed | yes | 1 class(es), e.g. UPreviewMapListClient | 1 | branches 1 | branches 1 |
| `0x10000` | unnamed | yes | 3 class(es), e.g. UPreviewMapListClient | 3 | branches 3 | branches 1, calls 2 |
| `0x20000` | unnamed | yes | 2 class(es), e.g. UPreviewMapListClient | 2 | continues 2 | calls 1, continues 1 |
| `0x40000` | unnamed | yes | 2 class(es), e.g. UPreviewMapListClient | 1 | returns 1 | zeroes 1 |
| `0x80000` | unnamed | yes | 0 class(es) | 5 | branches 2, calls 1, returns 2 | branches 1, calls 2, zeroes 2 |
| `0x200000` | Instanced |  | 237 class(es), e.g. ChapterList | 1 | calls 1 | calls 1 |
| `0x1000000` | ParseConfig |  | 1 class(es), e.g. UPreviewSkinnedDialogClientWindow | 1 | continues 1 | branches 1 |
| `0x4000000` | Exported | yes | 591 class(es), e.g. AIController | 6 | branches 4, calls 1, continues 1 | branches 2, continues 1, returns 3 |
| `0x8000000` | unnamed | yes | 13 class(es), e.g. AIMSG_Cloak | 1 | calls 1 | branches 1 |

**A bit the image reads and no packaged class carries has a constant answer.** `0x80000` has 5 reader(s) and no carrier among the 2319 shipped classes: the constructor copies the super's word under a mask and the registration adds `0x12`, so a bit outside both can only arrive with a class the packages do not hold, and every shipped object reads as clear.

**3 store(s) to `+0x498` sit inside a mapped function and 1 outside the symbol map.** The constructor composes a mask (`and` with `0x0e0f86ec`) and the registration `or`s `0x12`; every other store writes a whole word the constructor already built, so no reader can observe a bit that the constructor does not inherit.<!-- /generated:class-flag-readers -->

## How a bit can reach the class flag word, and where `0x80000` comes from (U5)

`#523`/`#525` left this open: five readers test `0x80000`, no shipped class carries it, and the next
measurement was *"the one store to `+0x498` outside the symbol map"*. The census below replaced that one
store with **every** instruction in the code sections whose displacement is `0x498` -- and answers the
question the other way round: the store named there is dead code, and the bit arrives at a class through the
registration, which does not filter the flags word its caller hands it.

<!-- generated:class-flag-stores -->
**136 instructions in the code sections name the word at a `0x498` displacement, and 13 of them write it.** The scan is by displacement, so it also covers the forms a `modrm`-only pattern misses (a `[reg+index*4+0x498]` operand, a two-byte `0f` opcode, a read-modify-write `or [x+0x498],imm`); each row in `inventory/class_flag_stores.csv` carries the instruction text and the proof that an instruction starts there. 3 of the writes compose a word -- a mask over the super's own word, or an immediate `or`ed into an argument -- 1 copy a word unchanged, 2 are read-modify-writes of the same field, 7 store a constant and 0 leave the source unresolved. The displacement alone does not make a field the class flag word, so the census keeps every one of them and the table says which body installs a class table.

| at | body | boundary | class table | what the write stores | introduced |
|---|---|---|---|---|---:|
| `0x0003965b` | `Core_UnClass` | map | - | inherit-load (super's own word) | `0xe0f86ec` |
| `0x0003af99` | `FUN_0003af60` | map | `0x004d4870` | argument (slot 0x14) | `0x12` |
| `0x0003c49d` | unmapped | pad | `0x004d4870` | argument (slot 0x14) | `0x12` |
| `0x0006ce62` | `Core_UnObj` | map | - | argument (slot 0xc) | `0x0` |
| `0x000e69e7` | unmapped | pad | - | read-modify-write | `0x0` |
| `0x001b721f` | `FUN_001b6f90` | map | - | read-modify-write | `0x0` |
| `0x002345b1` | `Core_UnObjBas` | map | - | immediate | `0x3` |
| `0x002348a8` | `Core_UnObjBas` | map | - | immediate | `0x4` |
| `0x00235e06` | `Core_UnObjBas` | map | - | immediate | `0x7` |
| `0x0023694a` | `Core_UnObjBas` | map | - | immediate | `0xc` |
| `0x00236adb` | `Core_UnObjBas` | map | - | immediate | `0xd` |
| `0x00236c4f` | `Core_UnObjBas` | map | - | immediate | `0xf` |
| `0x00236e24` | `Core_UnObjBas` | map | - | immediate | `0x10` |

**The only write that filters is the constructor's inherit.** It `and`s the super's word with `0x0e0f86ec` before the store, so it can drop a bit and never add one; every bit of a class's word is either one of the two compiler bits `0x12` or a bit some caller handed the composition.

**The writes that add a bit add only `0x12` and pass the caller's word through.** `0x0003af99`, `0x0003c49d` stores `argument | 0x12`: the rest of the word is not this code's choice, so a bit outside `0x0e0f86ec | 0x12` is reachable exactly when some caller carries it -- the composition does not close the bit, the shipped data does.

**The composing body outside the symbol map has no reference anywhere in the image.** Its start is found by the image's own padding -- a run of `int3` immediately behind `0x0003c430`, which no function table has -- and it is a twin of the registration: the same class table `0x004d4870`, the same `+0x49c` zero, the same `or 0x12` into an argument, and a different base constructor. It has **0 direct `E8` call sites over every code section and 0 dword references over every section**, so nothing in this image can enter it: the store the open question nominated as the route for `0x80000` is dead code, not a route.

| body | boundary | direct calls | dword references |
|---|---|---:|---:|
| `0x00039520` | map | 0 | 1 |
| `0x0003af60` | map | 493 | 0 |
| `0x0003c430` | pad | 0 | 0 |
| `0x0006c8a0` | map | 52 | 0 |
| `0x000e6940` | pad | 0 | 1 |
| `0x001b6f90` | map | 0 | 13 |
| `0x00231a10` | map | 0 | 1 |

**What the reachable composition is handed for its flags argument.** 317 of its 545 call sites push a word this decoder resolves -- the fourth argument, which is the one the registration `or`s `0x12` into -- and the words are: `0x4` x189, `0x8` x28, `0xc` x23, `0x804` x21, `0x1` x15, `0x8000` x11, `0x40` x10, `0xd` x5, `0x9` x4, `0x5` x2, `0x29` x1, `0x80` x1, `0x2040` x1, `0x8001` x1. The set covers the bits the pinned reader cannot name (`0x8000`, `0x18000`, `0x28000`, `0x40000`, `0x80000`), so a class built by this code carries far more than the script corpus declares.

**`0x80000` is set by the image itself, at `0x0003b0fc` (`FUN_0003af60`).** Those call sites pass `0x80000` as the flags word, so the class object built there carries `0x80012` -- and the class is not one of the 2,319 the packages ship, which is why the corpus reports no carrier: this build's `Core.u` carries 7 script classes and the rest of the engine's are native. The bit the five readers test is therefore live, and reachable exactly the way this census says -- by a caller's word passing through the registration.

**So `0x80000` reaches a class at runtime, and the store the open question nominated is not how.** The word is the caller's: the inherit can only drop a bit, the registration only adds `0x12`, and the out-of-map store is dead code -- but 1 registration site(s) pass `0x80000` itself, so a class built at runtime carries `0x80012`. A class's flags are a property of the code that registers it, not of the loader.
<!-- /generated:class-flag-stores -->

**What this settles for the rebuild.** A class's flag word is `(caller's word | 0x12)` at registration or
`(super's word & 0x0e0f86ec)` at construction -- nothing else writes it. So a rebuilt package's classes carry
exactly what their own `ClassFlags` declare plus the two compiler bits, and the engine classes the image
registers itself (which the shipped packages do not list) are where the unnamed bits live. That is also why
the corpus could report `0x80000` with no carrier without the bit being unreachable.

## Not measured

* Which of the nine files is which mission — the committed schema aggregates across files
  (`inventory/sgs_schema.csv` drops the per-file rows), so the per-mission view needs a re-parse.
* Reference resolution on load, above.
* Whether a load rebuilds the level and then applies the snapshot, or restores it wholesale; that
  distinction decides how much of the map parser (5.2) the rebuild needs.
