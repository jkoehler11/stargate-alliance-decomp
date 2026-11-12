# The class tree measured from the constructors

`inventory/xbe_class_table_names.csv` names **466 of the 492** per-class tables in `.rdata` from the
class literal a registration site pushes (`tools/class-table-names.py`). The 26 it leaves unnamed are
the engine's own base classes — `Object`, `Engine`, `Player`, `Texture`, `RenderDevice`, … — whose
registration is not a generated `ucpp` stub, so there is no stub body for a table store to sit in
(`docs/UNKNOWNS.md` U8).

This page is the second instrument: the subclass relation the constructors themselves encode, and what
it can and cannot name. `tools/class-tree-edges.py`; it reads `inventory/class_constructors.csv`,
`inventory/xbe_class_table_names.csv`, `inventory/xbe_class_tables.csv` and `inventory/classes.csv`,
so it needs no image and no boot for the tree half.

    python3 tools/class-tree-edges.py                 # summary and the 26 unnamed tables
    python3 tools/class-tree-edges.py --xbe DUMP/.../default.xbe   # + the registration-site join
    python3 tools/class-tree-edges.py --check         # synthetic self-test, no dump, writes nothing

## Which sites dispatch slots 19 and 20 (PLAN 4.3)

`inventory/class_table_slot_pairs.csv` settled what the two slots *hold*: `0x00020f00` (a one-byte `ret`)
twice in 327 tables, `0x000c4f60`/`0x000c6d00` in the 165 under `Actor`. `tools/vcall-dispatch.py`
(`make vcall-dispatch`, `inventory/vcall_dispatch_sites.csv`) asks the other half — who calls them — by
decoding every `call [reg+disp]` form in the image and keeping the displacements those slots sit at
(`19*4 = 0x4c`, `20*4 = 0x50`). A virtual call is a byte pattern, not a resolved call, so a row is a
*candidate* dispatch: the displacement is not unique to a class table and only a typed receiver proves one.

| measurement | value |
|---|---|
| executable sections scanned | 9 |
| candidate sites, all sections | 141 |
| **`.text`, the game's own code** | **106** |
| … slot 19 (`+0x4c`) / slot 20 (`+0x50`) | 31 / 75 |
| … by register | edx 37, eax 30, ecx 15, edi 12, ebx 8, esi 4 (never `ebp`) |
| … inside a named function | 14, in 9 bodies |
| the seven library sections | 35 (slot 19 x2, slot 20 x33) |
| `.data` / `.rdata` (flagged executable) | 8 / 2 |

The library rows are the control, and they are why `+0x50` is the noisy displacement: `D3DX`, `XONLINE`,
`XNET`, `XGRPH`, `D3D` and `XPP` are statically linked Microsoft code, no class table of this build is
dispatched there, and 33 of their 35 sit at `+0x50`. Among the `.text` sites, every one that lands in a named
body lands in the object layer: `Core_UnObjBas` 5, `Core_UnTemplate` 2, `Core_UnClass`, `Core_UnObj`,
`Native_65`, two `MasterServerUplink` destructors, plus offsets into `Engine_UnActCol` and
`IpDrv_InternetLink`. That matches the bodies read from the other side — they take `[this+0xa4]`, then
`[that+0xf4]`, and call a virtual on `this` at `+0xc`/`+0x8` — so the pair is part of the object/class layer,
not of gameplay code. The census cannot name the virtual; it says where to boot.

The earlier "82 sites" is not reproduced: a decode of every `FF /2` displacement form over `.text` gives 106,
and that figure is superseded here.

### What the boot measured, and what it did not

An execution breakpoint on each body (`tools/xemu/bp-args.py --arm-after 0`) armed `OK` at a **loaded** `M00`
and recorded **0 stops in 30 s**, so the pair does not dispatch while a level idles. The arm that would sit
*before* a level load is the one that matters for an `Actor`-installed pair, and `bp-args` cannot take it: at
the title the guest is in the kernel (`EIP=0xfffffe5c`, `0x80065996`, `0x80024a97` ten seconds apart) and the
tool refuses a kernel-context arm. A forced arm needs a read-back of the patched byte — the stub's `OK` is not
evidence that the `0xCC` landed in the mapping the game executes — and two further boots were lost to the
desktop answering `no xemu window on :0` while `xdotool search --onlyvisible --name .` listed 0 of 9 windows.
So the guest half is **unfinished, not negative**.

**Measured 2026-11-11: the forced arm's read-back says the patch never reaches the game's mapping.** `bp-args.py
--force` now reads every armed byte back through the same context and keeps it in the record (`armed_bytes`). Armed at
the title (`CPL 0`, `EIP=0x80071009`), both bodies answered the stub `OK`, and the bytes read back `f6` at
`0x000c4f60` and `56` at `0x000c6d00` -- the XBE's own bytes at both addresses, unchanged. A `Z0` set while the CPU
idles in the kernel therefore patches a mapping the game does not execute in, and the breakpoint can never fire: the
stub's `OK` is exactly the thing that cannot tell. The route this page proposed -- force the arm before the level
loads -- is retired for this instrument; what is left is to sample the front end until it is in game context and arm
there, because the halt loop (`0x80024a97`, `0xfffffe5c`, `0x80065996`) makes it a matter of timing rather than of
patching.

**Measured 2026-11-11, the sampling route is taken and the ring was never the question.** `--wait-user` gates on the
image's address range, not `CPL 3`: this build runs the game at `CS=0x8` (`CPL 0`), 88 samples in one boot and not one
of them in ring 3, and with the range gate both boots armed on their first sample. The `0xCC` read-back cannot see the
arm -- a control breakpoint that fired on `0x002bb740` read its own `8b` back -- so the arm is proven by that control
and the forced-arm negative above is withdrawn. With the arm proven, both bodies recorded **0 stops** over a window of
420 s spanning the map-list select, the M00 load and 180 s of gameplay: the pair is a true zero in those windows, and
what is left is a window that was not covered (level unload/travel, a checkpoint load, multiplayer) --
both of those are closed below, and by a boot, not by another scan.

`tools/vcall-stops.py` is the read half: it turns a `bp-args` record into (slot, caller, receiver class) rows —
caller by exact start, by containment in a sized body, or nearest-below; receiver from the `ecx+0x0` dump,
whose first dword is the class table that `inventory/xbe_class_table_names.csv` names. It writes
`inventory/vcall_slot_stops.csv` only when a run records a hit; `--check` drives all three caller resolutions
and an unnamed table with no boot.

## The edge

Every class constructor installs its own table (`mov dword ptr [this], <table>`) and, *before* that
store, calls its parent's constructor. `inventory/class_constructors.csv` already records that call in
`call_before_store`; invert it and each table gets the set of tables whose constructor calls it — the
direct subclasses that exist as tables.

That is a measurement, not a guess, when it agrees with the script tree. Over the named population,
restricted to the tables that have a measured child at all (`make` numbers below are the committed
inventories; the tool recomputes all of them):

| measurement | value |
|---|---|
| tables | 492 |
| named by the registration literal | 466 |
| unnamed | 26 |
| named tables with a measured child set | 391 |
| … whose child set equals the class's direct subclasses in `classes.csv` | **375** |
| … that disagree | **15** |
| … whose class has no subclass in the image (a leaf) | 76 |

375 of 391 is the claim this instrument is worth: the constructor call edges reproduce the script
class tree for 96% of the classes that have subclasses at all. The 15 disagreements are listed by the same run against the CSVs and decomposed in the next
section (`--conflicts`): most of them are tables whose class has no script row at all, and one
contradicts a name.

## What it names among the 26, and why it is not all of them

**Zero of the 26 are named by the exact-set rule.** The rule asks for a class whose direct subclasses
*are* the measured child set, and it fails in three ways, each measured:

* **23 have no named child at all** — `0x004c78c0` (31 slots, nothing calls its constructor),
  `0x004c7a10`, `0x004c7ac8`, `0x004c7c78`, `0x004cfd78`, `0x004d0228`, `0x004d2400`, `0x004d34d8`,
  `0x004eaea8`, `0x004fa390`, `0x004fcfe0`, `0x00517860`, `0x005178d8` and the rest. A leaf class is
  invisible to a relation whose only evidence is who calls it: `WaterTexture`'s table has 58 slots and
  `FireTexture`'s 59, and neither is called by any constructor measured.
* **3 have a measured child set that is only partly named**: `0x004c9348` (children `0x004c93d0`,
  `0x004d03c8`, `PackageMapLevel`), `0x004d1468` (15 children: `0x004d17a8` plus `ArrayProperty`,
  `BoolProperty`, `ByteProperty`, `ClassProperty`, `DelegateProperty`, `Enum`, `FixedArrayProperty`,
  `FloatProperty`, `IntProperty`, `MapProperty`, `NameProperty`, `ObjectProperty`, `PointerProperty`,
  `StrProperty`) and `0x004fc4f0` (`Level`, `NetPendingLevel`, `DemoPlayPendingLevel`, `0x004fc588`).
  The rule refuses a partly-named set rather than scoring it, so it reports
  `unnamed-children:?0x4d17a8` instead of a name.
* **the three that are readable by hand are the three above**, and they are consistent with the class
  their children imply: `0x004d1468`'s children are 14 of the fifteen property classes plus the one
  unnamed table between them — `Property`; `0x004fc4f0`'s are `Level`, `NetPendingLevel` and
  `DemoPlayPendingLevel` — `LevelBase`; `0x004c9348`'s include the table whose only child is
  `XboxViewport` (`0x004c93d0`, `Viewport`) and the one whose children are the three net connections
  (`0x004d03c8`, `NetConnection`) — `Player`. **This is the tree naming a table, not the registration
  literal**: the constructor edge is independent evidence for three of the 26, and it agrees with the
  name `registration-sites.py` asserted for each of them.

## The registration-site join, and the shared constructor it finds

`--xbe` adds the other direction: for each registration site the class map leaves unnamed (27 rows —
the 26 plus the `NetConnection` site whose window holds two literals), follow the `push imm32` pointers
in the 96 bytes before the call and see whether one resolves to a table-installing constructor, directly
or through a `jmp` thunk.

**25 of the 27 push the same pointer: `0x00034650`, a thunk to `0x00062ce0`.** `tools/ctor-argument.py`
reads what that body is: `MOV EAX,ECX; MOV [EAX],0x004dd2a8; RET` -- nine bytes, no class argument, one
fixed dword. It is a **default constructor**, not a class constructor, so the argument the literal path
was going to link through names no class at all. 24 sites reach it as a shared default and `Download`'s
site pushes it behind a second candidate that ties; `Object`, `Engine`, `Player`, `NetDriver`, `LevelBase`,
`RenderDevice` and nineteen more are described identically by it. And the dword it stores is not an
identity either: table `0x004dd2a8` (named `VertexStreamBase`) is carried by **51 classes / 550 objects**
in the loaded level, so object memory pairs it with classes that are not its name.

**The other two resolve to the registration site that follows them, and one earlier sentence here was
wrong.** `Texture` (`0x0027e889`) and `FractalTexture` (`0x002e1489`) push `0x0027f070` -> `0x0027e3b0`,
`WaterTexture`'s constructor, 0x57 bytes from `Texture`'s own site: the mixed-window caveat.
`FireTexture` (`0x002e8468`) pushes `0x00018120` -> `0x002e81f0`, and that body stores `0x004cfc88` =
**`IceTexture`**, not `FireTexture`; the `0x004cfd78` this page quoted before is the row the class map
records as *"no registration names this table"*. So **not one of the 27 sites pushes a constructor that
installs its own class's table** -- the literal path cannot name any of the 26. That same test is what
validates the rows that do resolve: of all 493 sites the pushed constructor installs the site's own class
table in **459**, and disagrees in 26, every one of them a shared or neighbouring site, so the 466 names
the class map does carry do not rest on a coincidence of windows.

## The 15 conflicts, decomposed

`--conflicts` names the 15 tables whose measured child set is not the script's direct subclass set
and says which way each one differs. The 15 are four groups, and only the third is evidence against
a name:

| cause | tables | what it is |
|---|---|---|
| `no-script-row` | 11 | the table's own class has no row in `inventory/classes.csv` — a native C++ class (`AudioSubsystem`, `Struct`, `State`, `PackageMap`, `PackageMapLevel`, `Linker`, `LinkerLoad`, `LinkerSave`, `Primitive`, `LodMesh`, `VertexStreamBase`), so there is no script super to compare against and its child set is measured-only. 95 of the 466 names are in this group; 84 of those have no measured child, so they land in the 76 blanks. |
| `descendant-edge` | 2 | every extra child is a descendant: `Vehicle` (7) and `PlayerController` (1). |
| `foreign-child` | 1 | an extra child is in the script tree and is *not* a descendant: `WaterTexture` (5). |
| `native-child` | 1 | every extra child is a native class with no script row: `Commandlet` (6). |

### The descendant edge is a skipped level, not an inlined parent

`call_before_store` is the **first** `E8` before the table store (`class-ctors.py` stops at the
first), so the edge names the constructor the body actually calls — and in these two families the
body calls an ancestor, not the parent. Measured in the image (`SG1_DUMP=$HOME/nfs/sg1-dump/...`,
bytes from the ctor to the store + 0x40):

* `StargateVehicle` (`super SVehicle`) ctor `0x00023080` calls `Vehicle`'s ctor `0x00021f10` at
  `+0x003` and nothing else, while `SVehicle`'s ctor `0x00022020` **exists out of line** (it calls
  `0x00021f10` at `+0x003` too). So the level is skipped by the generated stub, not inlined away:
  `Teltak` and `AlkeshBomber` (`super StargateCopter`) likewise call `0x00021f10`.
* `StargatePlayer` (`super UnrealPlayer`) ctor `0x00026a70` calls `PlayerController`'s ctor
  `0x00026870`, and `UnrealPlayer`'s own ctor `0x00026990` is out of line and calls the same.
  `Camera` (`super PlayerController`) calls `0x00026870` as it should.

The instrument loses a level on those classes and nothing else: the extra child is a descendant, so
the parent is still pinned to a unique table. That is why the verdict names the class and not the
parent, and why 375 of 391 still hold.

### `WaterTexture` is the one conflict that contradicts a name

`0x004cfba0` carries the name `WaterTexture`, and the tree says that cannot be right:

* its five callers are `TextureEditable`, `Cubemap`, `MovieTexture`, `BackBufferTexture` (each
  `super Texture`) and `IceTexture` (`super FractalTexture`) — none of them descends from
  `WaterTexture`;
* `WaterTexture`'s own three script subclasses (`FluidTexture`, `WaveTexture`, `WetTexture`) call
  `0x002e8260` instead, which stores the **unnamed** table `0x004cfe68`, and whose child set is
  exactly those three;
* `0x002e8260` calls `0x0027e3b0` at `+0x006` before its store — the constructor that installs
  `0x004cfba0` — so `0x004cfe68` is a child of `0x004cfba0`, and `0x004cfba0`'s ctor calls
  `0x00169980` (table `0x004c7958`), the material-side base.

Consistent reading: **`0x004cfba0` is `Texture` and `0x004cfe68` is `WaterTexture`**, with
`FractalTexture` having no out-of-line constructor at all (which is why both `IceTexture` and
`WaterTexture` chain past it to `Texture`). That is also the neighbourhood the registration-site
join already flags: `Texture`'s site `0x0027e889` and `FractalTexture`'s `0x002e1489` push the same
thunk `0x0027f070`, and `WaterTexture`'s site `0x0027e8e0` sits 0x57 bytes after `Texture`'s, so
the literal window can attach the neighbour's name to the table the thunk installs. Which side is
wrong is not settled from the file: it needs one object read (`UClass` name vs `+0x00` table,
`docs/UNKNOWNS.md` U8), and until then this page reports the contradiction rather than renaming an
artifact entry the other tools read.

### The read that would settle it is now a tool, and this run could not take it

`tools/xemu/class-tables-live.py` is the object read U8 asked for, and it needs **no literal, no window and no
constructor tree**: it walks `GObjObjects` in a booted guest, reads each object's `+0x00` table and its `+0x24`
`UClass`, resolves the class object's `+0x20` `FName` through `GNames`, and writes one `table_va,class,objects` row
per table -- so a table is named by the objects that *carry* it rather than by the site that installed it. Every
class has a default object in the array, so a table in use always gets a row, and a table whose rows disagree names
a hierarchy the static name disagrees with, which is exactly this `WaterTexture` question. `--table` prints the
pair (`0x004cfba0`, `0x004cfe68`), and `--selftest` runs the same walk over a synthetic world, so the instrument is
checkable without a boot.

**The read landed on 2026-11-10, and it names `0x004cfba0` `Texture`.** A boot through `drive.py --gdb=12641
--linger=1800` (title screen, 42,152 objects) walked **42,135 of 42,152** object headers over 255 pages and paired
them with **179 class tables / 312 rows**, now `inventory/live_class_tables.csv`:

* **`0x004cfba0`: 454 objects, every one of class `Texture`, nothing else.** An object's `+0x00` is its own
  class's table or an ancestor's, so a `Texture` instance cannot carry a table only its descendant
  `WaterTexture` installs. The literal row `0x004cfba0,WaterTexture` is the neighbour's push, exactly where this
  page predicted it -- `WaterTexture`'s own registration site sits 0x57 bytes after `Texture`'s.
* **`0x004cfe68` carries no object at the title**, and neither does `IceTexture`'s `0x004cfc88`: absence there is a
  property of texture classes with no instance in the packages the title loads, not a hole in the walk. So the
  pair's second half is *not* named by this read, and the reading that makes both instruments agree is still the
  tree's: `0x004cfba0` = `Texture`, `0x004cfe68` = `WaterTexture`.
* `tools/class-table-reconcile.py` scores the whole read against the literal artifact: **139 agree**, **12 name an
  ancestor of the object's class** (the literal names the installer, the read names the instance --
  `Triggers`/`SGC_SecurityReader`), **26 tables are carried by more than one class**, **one is inverted** (this
  row) and **one is not a class at all** (`0x004e1ce8`: the literal read `new`, the objects say `System`).
* The artifact keeps its row: it is `registration-sites.py`'s output and its `--check` reproduces it. This page and
  `inventory/live_class_tables.csv` are the tiebreaker, and `foreign-child: WaterTexture (5)` in the table above is
  an artifact error rather than a tree disagreement.

    python3 tools/xemu/class-tables-live.py --port 12641 --xbe $SG1_DUMP/default.xbe \
        --csv inventory/live_class_tables.csv --table 0x004cfba0 --table 0x004cfe68
    python3 tools/class-table-reconcile.py

### The read at a loaded level: the same verdict on 613 objects, and the pair's half stays empty

A second boot, this time **in M00** (`Continue` -> `QUICK SELECT MAP` -> ten `down` -> `A`, `--gdb=12391 --linger=1800`),
reads **48,646 objects / 48,631 headers (15 unreadable) / 346 pages** and pairs them with **189 tables / 376 rows**
(`inventory/live_class_tables_m00.csv`). `class-table-reconcile.py --live` scores that read **agree 142, installer 13,
shared 32, inverted 1, unknown-class 1**, and the one inverted row is this one again: **`0x004cfba0` carries 613 objects,
every one of class `Texture`** (up from 454 at the title).

* `0x004cfe68` **still carries no object** at a loaded level, and neither does `IceTexture`'s `0x004cfc88`, so the
  texture family's second half is named by the constructor tree alone and this read can never confirm it: the classes
  that install those tables have no instance in the packages the game loads at the title or in M00.
* **None of the 26 unnamed tables is carried either**, and **277 of the 466 named ones are absent too** -- 189 of 492
  tables get a row. The read's silence is a statement about which packages are loaded, not about a table's name.
* **Ten tables appear only at M00** (`Pickup` with four pickup classes, `Pawn`/`GoaAutoTurretMK3Pawn`,
  `AIController`/`GoaAutoTurretMK2Controller`, `Projector`/`DynamicProjector`, `GUIScrollTextBox`/`SGScrollTextBox`,
  `InventorySpot`, `MaterialSwitch`, `SubActionCameraEffect`, `SubActionTrigger`, `SubActionFade`) and every one of
  them agrees with the literal artifact, so a loaded level adds coverage without moving a verdict.

## The installer's callers: five more names, two clashes, and the reason the rest stay open

A class table is installed by a function -- `inventory/class_constructors.csv` records it as
`function_va`/`store_va` -- and MSVC makes every subclass's constructor and destructor *call* its
parent's before installing its own. So the direct callers of an installer are the classes one level
below it, and an unnamed installer's table is the deepest class that is a proper ancestor of every
named caller. `tools/class-table-parents.py` (`make class-table-parents`,
`inventory/class_table_parents.csv`, 492 rows) reads that edge: one `E8 rel32` pass over the code
section, `symbols/xbox_functions.csv` for the caller names, `inventory/classes.csv` for the supers,
1,978 caller sites in all.

* **The installer-name edge reproduces the whole map: 466 of 466 named tables `agrees`, 0 adjacent,
  0 differs.** Every named table's installer is itself a `ctor_X`/`dtor_X` symbol, so the same edge
  that `class-tree-edges.py` inverts is what the registration artifact encodes -- and a rule that
  cannot reproduce a name it already knows is not evidence for one it does not.
* **Five unnamed tables the callers name** (`unnamed->proposed`): `0x004c7958` `Material`
  (35 of 37 callers), `0x004c7a10` `RenderedMaterial` (7/10), `0x004c7ac8` `BitmapMaterial` (3/5),
  `0x004c7c78` `CameraEffect` (10/11), `0x005040d0` `Engine` (1/3, from `ctor_GameEngine`). The
  three material tables form the exact chain `classes.csv` gives -- `BitmapMaterial :
  RenderedMaterial : Material` -- which the registration never said, and each of the five names has
  a registration site whose literal is that class (`Engine` at `0x00121989`, `Material`, ...) whose
  constructor pointer is the shared default and so installs no table of its own.
* **Two proposals clash with a name the registration already gives another table, and that is the
  finding**: `0x004cfe68` -> `WaterTexture` where `0x004cfba0` is committed `WaterTexture`, and
  `0x004d0228` -> `IceTexture` where `0x004cfc88` is committed `IceTexture`. The live read already
  says `0x004cfba0` carries **613 objects, every one of class `Texture`**, so the clash corroborates
  the inversion this page recorded above rather than contradicting it. Both rows keep the verdict
  `name-clash:<other table>`: the artifact reports, it does not re-name.
* **Seventeen tables are constrained, not named.** Their installers are called by named classes whose
  own rows are absent from `inventory/classes.csv` -- `Struct`/`Const`/`Enum` (16 callers,
  `0x004d1468`), the fifteen property classes (13, `0x004d17a8`), `TcpNetDriver`/`TcpNetDriverXbox`/
  `DemoRecDriver` (`0x004d0318`), `ActorChannel`/`ControlChannel`/`FileChannel`/`VoiceChannel`
  (`0x004fa390`), `Level`/`NetPendingLevel`/`DemoPlayPendingLevel` (`0x004fc4f0`),
  `D3DRenderDevice`/`NullRenderDevice` (`0x004eaea8`), `IndexBuffer`/`SkinVertexBuffer`
  (`0x00517860`), `VertexBuffer`/`VertexStream*` (`0x005178d8`), ... These are the engine's own base
  classes: `classes.csv` is built from the 18 packages and `Core.u` holds 7 class exports, so
  `Field`, `Property`, `NetDriver`, `Channel`, `VertexBuffer` have **no row to read a super from**.
  The artifact keeps the caller set in `named_callers`, which is the constraint: the table is a
  common base class of exactly those names.
* **Two have no named caller at all** (`0x004c9348`, `0x004cfd78`): the class objects behind them are
  constructed from unnamed code, so this edge is silent rather than negative.

## Status

The constructor tree is a real second instrument (375/391 agreement) and it confirms three of the 26
names independently of the registration literal. **It does not close U8's 26**: 23 are leaves with no
callers, and the site join is closed the other way -- 25 of 27 push a nine-byte default constructor that
stores one dword shared by 51 classes, and the other two push their neighbour's constructor. What would
settle the rest is an argument the registration passes that is not the constructor:
* the **fourth and fifth pushes** of a site (`0x004d0e90`, a `.rdata` pointer, and a string VA) are the
  same in kind at every site and unread; `docs/NATIVE_CLASSES.md` records them as arguments 10 and 12
  whose meaning is not established;
* a **breakpoint on the registration call** each row of `inventory/registration_sites.csv` names, reading
  the constructed `UClass`'s `+0x00` table field as it is filled -- the read of the *object array* is
  exhausted (no object carries any of the 26 tables at the title or in `M00`), but a class object is
  built at registration even when no instance is ever spawned.

Neither is a base-class-table problem any more: the tables are located, sized and slotted
(`docs/NATIVE_VM.md`); this page adds only which of them the tree can name.

The installer's-callers edge above is the third instrument on these 26 tables and the strongest one
for the engine base classes: it reproduces all 466 names it can check, names five more, and records
the seventeen it cannot name as a constraint instead of a guess.

## The class object's constructor field is not carried (2026-11-10)

`tools/xemu/class-ctor-field.py` was written for the route this page ended on: a `UClass` object exists per
registered class even when nothing instantiates it, so its constructor field should name the table the class
installs. In a booted **M00** the object array holds **1,059** objects carrying the `Class` table
`0x004d4870`; none of their bodies holds any of the **1,011** constructors/destructors in
`inventory/class_constructors.csv`, so the read reproduces **0 of the 466** names the literal already has and
cannot name the nineteen. The negative is about the field: the build keeps the constructor in the
registration code, not in the `UClass` instance. So this page's status stands as written -- the constructor
tree (375/391) and the installer's callers (466/466, five named, nineteen constrained) are the two edges that
work, and the engine's own base classes need an engine-side `classes.csv` or a registration breakpoint.
`docs/UNKNOWNS.md` U8.

## The two residuals, from the registration's arguments and from the slots (2026-11-11)

`tools/class-table-residual.py` (`make class-table-residual`) answers U8's two open halves with the image alone,
and both answers come out narrower than the questions assumed. Its artifacts are `inventory/registration_arguments.csv`
(one row per registration site) and `inventory/class_table_slot_pairs.csv` (one row per class table).

**No registration call carries a table.** All **493** sites in `inventory/registration_sites.csv` call the same
function, `0x0003af60`. The tool decodes every `68 imm32` push in the 96 bytes before each call and classifies the
immediate by what it is -- a UTF-16 literal the image carries, a known function start, a class table, or an unmapped
immediate: **493 of 493 push at least one string** (median 8 pushes, range 7-10), **493 push the flags word
`0x04084004`**, 3 push a function start, **0 push a class table**. The strings are the class's own name and its
outer (package) name. For `Property`'s site at `0x000778d9` the window reads `push 0x624d0` (a one-byte `ret`),
`push 0x34650` (the null-check thunk onto `0x62ce0`), `push 0x4084004`, `push "System"`, `push "Property"` and
`push 0x6c` -- so the class table arrives through the constructor the site passes, never as an argument. PLAN 4.3's
"the site's fourth/fifth pushes" is therefore read and dead as a naming route, which is why the 26 tables stay
unnamed rather than mis-named.

**The decode is validated by a second artifact, not by itself.** `registration_sites.csv` mines a `literals` column
per site with a different pass; the tool's string pushes equal that column on **493 of 493** sites, in order, so an
argument list quoted here is the call's, not a window artefact.

**The 27 shared-default sites are the engine base classes, and they name themselves too.** In address order:
`Exporter`, `Factory`, `Field`, `Object`, `Subsystem`, `Language`, `Property`, `VoiceSubsystem`, `Viewport`,
`CameraEffect`, `Client`, `Channel`, `RenderDevice`, `Engine`, `LevelBase`, `Material`, `BitmapMaterial`,
`RenderedMaterial`, `NetDriver`, `PendingLevel`, `Player`, `RenderResource`, `VertexStreamBase`, `Texture`,
`FractalTexture`, `WaterTexture` -- all outered to `System` -- and the site `0x0010d6a9` the census leaves unnamed
pushes `NetConnection` outered to `User`. Their tables are a separate question from their names: the name is on the
site, the table is not.

**Slots 19 and 20 hold exactly two implementations, and the split is the `Actor` subtree.** `--slots` reads the pair
out of all 492 tables: **two distinct pairs and no third** -- `0x00020f00` twice (a one-byte `ret`) in **327**
tables, and `0x000c4f60` / `0x000c6d00` (40 and 69 bytes) in **165**. Every one of the 165 is under `Actor` by the
script super chain, `Actor` itself among them, and **all 26 unnamed tables are in the 327**, so the division is the
`Actor` subtree against everything else -- not, as the earlier reading had it, "real bodies in 165 of 492" without
saying which 165 or how many kinds of body.

**What the two bodies are, as far as the image says.** Both test `[this+0x2dc] & 1`; on a set bit they take
`[this+0xa4]`, then that object's `[+0xf4]`, and call a virtual on the result with `this` as its argument (`+0xc` in
the 40-byte body, `+0x8` in the second, which first empties a 4-byte-stride array at `+0x100` by pushing 4 to
`0x00055ed0` and zeroing `+4`/`+8` of it). Either null field returns immediately. That is a delegating notification
through two pointer fields; the *name* needs those fields' names, which is property data the packages do not carry.

**The dispatch-site route is counted and is not the next instrument.** U8 proposed "a call site that dispatches
`+0x4c`/`+0x50` through a known object". `.text` holds **82** such `call [reg+0x4c]`/`[reg+0x50]` sites, but the
displacement is not unique to this table -- `[+0x4c]` is used on string- and archive-shaped objects as well -- so each
site needs its receiver typed before the slot means anything, and the receiver typing is the same missing field name.
What would settle it is a breakpoint at `0x000c4f60` and `0x000c6d00` in a booted guest, reading which object calls
them and from which `this`: the two bodies are 40 and 69 bytes, so the two calls are cheap to stop on.

**Measured 2026-11-12: the unload window is covered, and the pair does not dispatch in it.** A completed unload is
reachable in one command: `RestartLevel` (an `exec function` on `PlayerController`, `ClientTravel("?restart", ...)` in
standalone play) typed into a loaded `M00` -- `(> restartlevel_` read off the frame, `ret` submitted -- took the level
down and back up with the guest alive (`Loading... M00` at +5 s, the HUD at +20 s, the tutorial modal at +45 s). With
the pair armed through `bp-args.py --wait-user --control-self` over a 380 s window spanning the select, the load,
gameplay, the teardown and 200 s of the reloaded level, **0 stops** landed at `0x000c4f60` or `0x000c6d00` while the
control stopped at once on the sampled `EIP` (`0x003ed669`). A full actor teardown therefore does not run either body,
which rules out the reading that the pair is a per-actor lifecycle virtual (`Destroy` and its kin): that would fire
thousands of times over one level restart. `tools/xemu/restart-level.sh` is the runner.


## What loads the receiver at the 106 pattern hits (2026-11-12)

`inventory/vcall_dispatch_sites.csv` counted 106 `.text` hits for the two displacements slots 19 and 20 sit at
(`+0x4c`, `+0x50`) and said out loud that a displacement is not unique to this table. `tools/vcall-receiver.py`
(`inventory/vcall_receiver_sites.csv`, 106 rows) reads the instruction that loads the register each hit
dispatches through, and the first thing that measures is how many of the hits are call sites at all:

| measurement | value |
|---|---|
| `.text` pattern hits | 106 (slot 19: 31, slot 20: 75) |
| not an instruction boundary | **51** (slot 19: 0, slot 20: 51) |
| real dispatch sites | **55** (slot 19: 31, slot 20: 24) |
| receiver = `[obj]`, the object's own first dword | **53** |
| receiver = a stack local (`mov reg,[ebp]`) | 2 |
| receiver unread | **0** |
| read from the function's own start (`aligned`) | 55 of 55 |

**A `0xFF` at `+0x4c`/`+0x50` is often a displacement byte, not an opcode.** At `0x002d0d6e` the bytes are
`8d 95 e8 fb ff ff 52 50` -- `lea edx,[ebp-0x418]`, `push edx`, `push eax` -- and the `ff` the census read as
`call [edx+0x50]` is the last byte of that `lea`'s displacement. **51 of the 106 hits are that shape and all 51 are
slot 20**: every slot-19 hit (31) is a call, so `+0x4c` is the specific displacement and `+0x50` the noisy one --
the asymmetry the seven library sections showed, now measured on this pair.

**Two decoders, because the narrow one is wrong in both directions.** The first pass accepted a boundary only when
an instruction *ended* at the site behind a chain of instruction ends, and that test is neither necessary nor
sufficient. At `0x002d113c` the `ff` is the last byte of `lea ecx,[ebp-0x15c]`, and the `lea`'s own bytes decode as
`add [ebp-0x15b73],ecx`, which lands on the site with a chain of wrong-length decodes behind it -- so 8 displacement
bytes were accepted (the 8 rows the first pass could not read beyond); in 15 other functions a byte the narrow
decoder does not know breaks the chain in front of a real call -- so 15 calls were rejected. `objdump -D -b binary
-m i386 -M intel` over each site's function start settles both, and the verdict is kept per row (`oracle`):
**55 of the 106 hits are an instruction start where a `call DWORD PTR [reg+0x4c|0x50]` sits**, and the other 51 are
inside a `lea`'s displacement. The control is the 40 rows the narrow sweep already read: the oracle picks the same
defining instruction in **40 of 40**.

**Every real site is a vtable dispatch on the object's own first dword.** 53 of the 55 load the receiver with a
two-byte `mov reg,[reg2]` -- `eax,[ecx]` 12, `edx,[ecx]` 12, `edx,[esi]` 8, `eax,[esi]` 6, `edx,[eax]` 6,
`eax,[edi]` 3, `ecx,[eax]` 2, `edx,[ebx]` 2, `eax,[ebx]` and `edx,[edi]` 1 each -- so the call is
`call [[obj]+0x4c]`: slot 19 or 20 of the vtable the object points at. Two more read the receiver from `[ebp]` (a
stack local). By base register the object pointer comes from `ecx` 24, `esi` 14, `eax` 8, `edi` 4, `ebx` 3 and
`ebp` 2; no site loads it from an absolute global and none reads a table named in
`inventory/class_table_slot_pairs.csv`, which is what makes slots 19/20 a C++ virtual pair of the 165 `Actor`-subtree
tables rather than a separate `UClass` dispatch path.

**What the read costs, honestly.** All 55 rows decode from the function's own start, so the receiver is the last
write to that register on the way in and **`unread` is empty**: the 8 rows the first pass kept as `unread` were
displacement bytes, not a decoder gap, and the backward walk the first write-up described as carrying 66 rows never
supplied a receiver at all. The 24 rows that load through `ecx` are the member-function `this` only if nothing wrote
`ecx` first, so which register the *object pointer itself* came from (`this`, an argument, a field, a local) is the
next static read -- and the class an arbitrary `[obj]` belongs to at run time is the one a stock disc cannot answer
(the multiplayer/dedicated path never runs; U8).

**Where the object pointer itself comes from.** The receiver load names the object, not its origin, so the same
oracle listing is walked one hop further (`origin`, `origin_va`, `origin_chain`) to the instruction that produced
the register holding the pointer. Of the 55 sites: **28 read the object from a field of another object**
(`mov ecx,[esi+0x4c]`, `[esi+0x48]`, `[esi+0x2c]`, `[eax+0x220]`, `[esi+0x1c]`, `[esi+0x310]` -- six different
offsets, so not one shared object graph), **14 from a stack slot or a `pop`** (7 `mov reg,[esp+..]`, 5 `pop`, 2
`[ebp-0x40]`), **4 from a call's return value**, **3 from an absolute `.data` address** (`0x5fbed4` twice, at the
last dword of its section; `0x5bf514` once, whose pointer `0x5c1a0c` is not a class table), **3 are copies of
`this`** (chains `esi<-ecx` whose `ecx` is never written), **2 are the stack-slot receiver itself** and **1 comes
from an argument** (`[ebp+0x18]`). So the pair is mostly dispatched on an object *another object holds*, not on the
caller's own `this`: a vtable-level virtual that the 165 `Actor`-subtree tables and the classes reached through
these fields both carry.

## What holds the object: the field offsets, and the table that cannot name their owner

`inventory/vcall_receiver_sites.csv` says 28 of the 55 real slot-19/20 sites read the object out of a *field* of
another object. The field is only a displacement until something reads it as one, so `tools/vcall-field-owner.py`
(`make vcall-field-owner`, `inventory/vcall_field_owners.csv`, 28 rows) decodes the member load the origin points at
and records the offset, the base register and every static route that could name the owner's class:

| offset | sites | base register | reading |
|---|---:|---|---|
| `+0x4c` | 12 | `esi` 11, `ebx` 1 | the function's own object |
| `+0x220` | 8 | `eax` 8 | an object passed in as an argument |
| `+0x48` | 3 | `esi` 3 | the function's own object |
| `+0x1c` | 2 | `esi` 2 | the function's own object |
| `+0x2c` | 2 | `esi` 1, `eax` 1 | own object / argument |
| `+0x310` | 1 | `esi` 1 | the function's own object |

**28 of 28 decode, and every one is a plain `mov reg,[base+disp]`** -- 0 indexed (SIB) loads, 0 absolute ones, 0
unread -- so a field site is never a container walk or a static. Two populations, not six: **20 read from the
function's own `this`** (base `esi`/`ebx`, the chain rooted at an `ecx` nothing writes) and **8 read from an object
that arrives as an argument** (`eax` is never written before the read), and those 8 *are* the whole `+0x220` group.
All eight sit in small functions with exactly one direct `E8` caller each, and all eight callers fall in
`0x335007`-`0x337318`: one family, one offset, one object graph -- which the earlier "six different offsets, so not
one shared object graph" reading understated by counting offsets instead of owners.

**No table names the owner, and that is measured, not assumed.** A class table holds virtuals only, so this tool
tries two routes and records both verdicts per row: `table` (the containing function is itself a slot target of one
of the 492 tables, whose class is then named) and `caller` (every direct caller is a slot target of a single named
class, so the object handed to it is that class). Both answer **0 of 28**: not one of the 23 containing functions is
a table slot, and of the 20 sites whose function has any direct caller, no caller is a slot target either. The
containing functions are plain member functions, so the image carries no class for them at all, and the object
handed to them is typed by nothing in `.text`.

**A shared displacement is not a shared field.** `+0x4c` is also the byte offset of the slot-19 displacement, and 12
of the 28 field reads are at `+0x4c`; the same holds for the 51 displacement bytes the earlier census had to reject,
all of them slot 20. One number, two unrelated meanings -- which is why a census keyed on the displacement alone
conflates a vtable slot with a member.

**What is left is a layout, not another census.** `field_offset` resolves to a property name only against a class
layout, and the property stream (`inventory/class_properties.csv`) carries tag names, kinds and types but **no byte
offsets**, so its 11,497 rows cannot be joined to these six numbers. The two routes that would settle it are one
class layout read the hard way -- a `Serialize` override (slot 11, `0x392b0`) stores and loads its fields at fixed
offsets -- or a live object's vtable pointer out of a RAM dump, the way `inventory/hud_console_link.csv` names
`StargateHUD+0x398` as `Console`.

## The live heap: the six offsets belong to 149 classes, and `+0x220` is dominated by a class's function

The static half above ends on a layout, and a boot can read one: `GObjObjects` holds every `UObject`, an object's
`+0x00` is its class's table, `+0x24` its `UClass` and that class object's `+0x20` its `FName`
(`tools/xemu/names.py`, measured by `class-tables-live.py`). `tools/xemu/field-objects.py` walks the array, reads the
six offsets across the whole live heap, and reports per offset which class holds **another live object** there and
what class it holds (`inventory/field_object_owners.csv`, 779 rows). Measured 2026-11-12 at the title screen:
**42,128 of 42,137** array entries are plausible objects, every class header read (0 unreadable), 294 blocks /
19.3 MB / 998 RSP calls.

| offset | sites | owner classes | held classes | largest single population |
|---|---:|---:|---:|---|
| `+0x2c` | 28,591 | 78 | 28 | `Function` holds `Function` 7,637 of 8,510 |
| `+0x4c` | 19,240 | 37 | 27 | `FloatProperty` holds `FloatProperty` 2,036 of 4,402 |
| `+0x310` | 9,770 | 58 | 27 | `Function` holds `LinkerLoad` 7,904 of 8,496 |
| `+0x48` | 765 | 58 | 45 | `Package` holds `LinkerLoad` 71 of 332 |
| `+0x220` | 714 | 38 | 35 | `Class` holds `Function` 533 of 1,004 |
| `+0x1c` | **0** | 0 | 0 | none |

**A shared displacement is not a shared field, and the heap says so with numbers.** The six offsets resolve to
**149 distinct owner classes**, and inside one offset two meanings sit side by side: at `+0x2c` a `Shader` holds a
`Texture` in 107 of 107 objects and a `ColorModifier` holds one in 116 of 116 -- a material's texture slot -- while a
`FloatProperty` holds another `FloatProperty` in 2,026 of 4,402, a sibling link in a property list and not a shader's
property. So the 28 sites' "six offsets" are six *displacements* that appear in many unrelated layouts; a class
layout is what turns one into a name, and this artifact names classes, not properties.

**`+0x220` has a dominant population, and it is a class object holding a function.** 539 of the offset's 714 sites
hold a `Function`, and 533 of those are `Class` -- a `UClass`, whose `+0x220` is a function pointer, at 533 of the
1,004 live class objects. Behind it come a GUI widget chain that is **complete** (`GUIButton` 10 of 10, and nine
more `GUI*` page and widget classes at 1 of 1 or 2 of 2) and `AnimNotify_Script` holding a
`LinkerLoad` in 45 of 63. Every other row of the 71 is one or two objects out of thousands, which is what two
unrelated fields sharing a displacement looks like. The eight `[eax+0x220]` sites read that slot off an object that
arrives as an argument, so the class carrying it is one of **38 candidates**, headed by `Class` at eight times the
next population.

**`+0x1c` is the negative the static half predicted**: not one live object at the title screen holds a live `UObject`
at `+0x1c`, so the two `+0x1c` sites read a dword that is either not an object pointer or is only filled later
(a mission's actor classes are not in this walk). The two `+0x2c` sites, by contrast, sit in a displacement that 78
classes populate.

**What this artifact cannot say.** A title-screen heap is not a level's: 42,128 objects with no mission loaded and
34 blocks unmapped, so the classes a mission instantiates are absent. And "the same displacement in two classes" is
not "the same property" -- each row is a candidate, which is why the eight-site group is reported as 38 classes
rather than one name. The next static read is which of them the callers at `0x335007`-`0x337318` pass.
