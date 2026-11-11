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
