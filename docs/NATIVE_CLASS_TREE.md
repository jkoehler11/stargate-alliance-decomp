# The class tree measured from the constructors

`inventory/xbe_class_table_names.csv` names **478 of the 492** per-class tables in `.rdata`: from the
class literal a registration site pushes (`tools/class-table-names.py`), from the constructor tree
(`name_rule` `tree-paired`), and — for four tables — from the base a ladder's slots name
(`name_rule` `slot-file-token`: `0x004c93d0` `Viewport`, `0x004d0318` `NetDriver`, `0x004d03c8`
`NetConnection`, `0x004fa390` `Channel`, all four applied 2026-11-21). The 14 it leaves unnamed are the
engine's own base classes — `Player`, `RenderDevice`, `Download`, … — whose registration is
not a generated `ucpp` stub, so there is no stub body for a table store to sit in (`docs/UNKNOWNS.md`
U8). Of the 24 tables the literal path cannot reach, six are paired by a child-super rule calibrated on
the names the literal does carry and **applied to the map**, five carry a family hint, and the last 13
stay open (`inventory/class_table_name_pairs.csv`, the 2026-11-15 notes in `PLAN.md`).

This page is the second instrument: the subclass relation the constructors themselves encode, and what
it can and cannot name. `tools/class-tree-edges.py`; it reads `inventory/class_constructors.csv`,
`inventory/xbe_class_table_names.csv`, `inventory/xbe_class_tables.csv` and `inventory/classes.csv`,
so it needs no image and no boot for the tree half.

    python3 tools/class-tree-edges.py                 # summary and the 14 unnamed tables
    python3 tools/class-tree-edges.py --xbe DUMP/.../default.xbe   # + the registration-site join
    python3 tools/class-tree-edges.py --xbe DUMP/.../default.xbe --all-sites   # ... over all 493 sites
    python3 tools/class-tree-edges.py --check         # synthetic self-test, no dump, writes nothing
    python3 tools/class-tree-edges.py --check-doc     # the count block above, recomputed from the CSVs

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

That is a measurement, not a guess, when it agrees with the script tree. The block below is generated
from the four committed inventories by `tools/class-tree-edges.py --write-doc`, and `make ci` runs the
matching `--check-doc`, so it is the current numbers rather than the ones the first run printed:

<!-- generated:class-tree -->
| measurement | value |
|---|---|
| tables | 492 |
| named by the registration literal | 489 |
| unnamed | 3 |
| named tables with a measured child set | 408 |
| … whose child set equals the class's direct subclasses in `classes.csv` | **385** |
| … that disagree | **23** |
| … whose class has no subclass in the image (a leaf) | 81 |

| conflict cause | tables |
|---|---|
| `no-script-row` | 17 |
| `descendant-edge` | 5 |
| `native-child` | 1 |
| `foreign-child` | 0 |
| `missing-only` | 0 |

| the unnamed tables, by what the tree can say | tables |
|---|---|
| named exactly by their child set | 0 |
| ambiguous (two classes match) | 0 |
| partly named child set | 0 |
| unconstrained (no class's subclass set matches) | 3 |
<!-- /generated:class-tree -->

The claim this instrument is worth is the ratio of the two bold rows: the constructor call edges
reproduce the script subclass set exactly for all but 16 of the classes that have a measurable child
set in the image. The 16 are listed by the same run against the CSVs and decomposed in the next
section (`--conflicts`), and **the composition is the finding**: 10 are native C++ classes with no row
in `classes.csv` at all, so there is nothing to compare them against; 5 are an edge that skipped a
level; 1 is a native child. None of them is evidence against a name any more — the one that was
(`WaterTexture`) was this page's own inversion of a pair, and the class map has since been corrected.
The fifth skipped level is `Material`, and it appeared with its name rather than before it: naming
`0x004c7958` is what put its 32 measured children into the comparison (`--conflicts` says every extra
child is a descendant, which is how `RenderedMaterial`'s inlined constructor looks).

## What it names among the 18, and why it is not all of them

**The six the tree paired are in the map now**, so what is left to explain is the 18 that are still
addresses. The nearest the tree comes to a seventh is `0x004c78c0`, and reaching it needs the six to be
read back as evidence — the round the dated section below measures and rejects. The 18 fall into two
measured groups:

* **15 are unconstrained**: their measured child set matches no class's direct subclass set. For
  eight of them the tree measures no child at all — `0x004c78c0` (31 slots, and no class constructor
  calls its installer), `0x004d2400`, `0x004d34d8`, `0x004eaea8`, `0x004fa390`, `0x004fcfe0`,
  `0x00517860`, `0x005178d8` — and a table nothing calls is invisible to a relation whose only evidence
  is who calls it. The others *are* called, by classes whose own super is
  a native base with no row in `classes.csv`, so the set is right and there is no class to match it to:
  `0x004c93d0` (`XboxViewport`), `0x004c94b0`
  (`XboxClient`), `0x004d0318` (the three drivers), `0x004d03c8` (the three connections), `0x004d17a8`
  (six property classes), `0x004d3af0` (`TextBufferFactory`) and `0x004fc588`
  (`DemoPlayPendingLevel`) -- seven tables. This is the difference between the two instruments on this page:
  the tree needs *both* edges to name a table, and the installer's-callers rule below needs only the
  second.
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
  literal**: the constructor edge is independent evidence for three of the original 24, and it agrees
  with the name `registration-sites.py` asserted for each of them.

## The registration-site join, and the shared constructor it finds

`--xbe` adds the other direction: for each registration site the class map leaves unnamed (25 rows —
the 24 plus the `NetConnection` site whose window holds two literals), follow the `push imm32` pointers
in the 96 bytes before the call and see whether one resolves to a table-installing constructor, directly
or through a `jmp` thunk. `--xbe --all-sites` runs the same join over **every** site, which is what
makes the caveat measurable instead of anecdotal: of all 493 sites, the pushed constructor installs the
site's own class table in **464**, another table in **27** (a shared default, a neighbouring
registration, or a class whose table its parent's constructor installs) and **2** have no constructor
pointer in the window at all.

**24 of the 25 push the same pointer: `0x00034650`, a thunk to `0x00062ce0`.** `tools/ctor-argument.py`
reads what that body is: `MOV EAX,ECX; MOV [EAX],0x004dd2a8; RET` -- nine bytes, no class argument, one
fixed dword. It is a **default constructor**, not a class constructor, so the argument the literal path
was going to link through names no class at all. 23 sites reach it as a shared default and `Download`'s
site pushes it behind a second candidate that ties; `Object`, `Engine`, `Player`, `NetDriver`, `LevelBase`,
`RenderDevice` and nineteen more are described identically by it. The one site that does not is
`WaterTexture`'s, below. And the dword it stores is not an
identity either: table `0x004dd2a8` (named `Object` since the hierarchy join, `VertexStreamBase` before it)
is carried by **51 classes / 550 objects** in the loaded level, so object memory pairs it with classes that
are not its name.

**The one that does not is `WaterTexture`'s site, and it resolves to its neighbour.** `0x002e17d9`
pushes `0x0027f070` -> `0x0027e3b0`, the constructor that installs `0x004cfba0` — from a site that sits
0x57 bytes after `Texture`'s own, which is the mixed-window caveat in one row. So **not one of the 25
unnamed sites pushes a constructor that installs its own class's table**: the literal path cannot name
any of the 24, and the pair `0x004cfba0`/`0x004cfe68` is where that shows up as a name rather than as a
gap. The same test is what validates the rows that *do* resolve, and it is worth quoting as one number
because it is the reason the 468 names are evidence: of all 493 sites, the pushed constructor installs
the site's own class table in 464 and a different one in 27, so a name the map carries is not a
coincidence of windows — and where the two differ, the difference is a rule the map already records in
its `name_rule` column (`hierarchy-ancestor`, `base-store-ancestor`, `unshared-thunk`).

## The 16 conflicts, decomposed

`--conflicts` names the 16 tables whose measured child set is not the script's direct subclass set
and says which way each one differs. The 16 are three groups, and as of the corrected class map none
of them is evidence against a name:

| cause | tables | what it is |
|---|---|---|
| `no-script-row` | 10 | the table's own class has no row in `inventory/classes.csv` — a native C++ class (`AudioSubsystem`, `Struct`, `State`, `PackageMap`, `PackageMapLevel`, `Linker`, `LinkerLoad`, `LinkerSave`, `Primitive`, `LodMesh`), so there is no script super to compare against and its child set is measured-only. 94 of the names are in this group; the ones with no measured child land in the block's `no subclass in the image` row. |
| `descendant-edge` | 5 | every extra child is a descendant, so the edge skipped a level: `Vehicle` (7), `PlayerController` (1), `Texture` (3), `Object` (309), `Material` (32). |
| `native-child` | 1 | every extra child is a native class with no script row: `Commandlet` (6). |

`Object` is the extreme case and the reason the cause is named rather than counted as an error: 308
named tables call its constructor, because every class's constructor chain that has no intermediate
out-of-line body calls `Object`'s. The `foreign-child` group is now **empty**, and that is the one
verdict that moved: its single member was `WaterTexture`, whose cause was the class map's inversion
rather than the tree's reading.

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
parent, and why the agrees row of the block still holds at 376 of 392. `Texture` joined this group when
the map stopped calling it `WaterTexture` (below): its extra children `FireTexture` and `IceTexture`
chain past `FractalTexture`, which has no out-of-line constructor, so they call `Texture`'s directly.

### The one conflict that contradicted a name was the artifact, and the map no longer carries it

`0x004cfba0` used to carry the name `WaterTexture`, and the tree said that could not be right. The
reading this page derived from it — `0x004cfba0` = `Texture`, `0x004cfe68` = `WaterTexture` — is what
the artifacts now say, so the subsection is kept as the record of how a contradiction is retired
rather than as an open question:

* **the class map agrees with the tree today.** `0x004cfba0` is `Texture` (`name_rule`
  `hierarchy-ancestor`: three registration sites share one constructor thunk, so the literal is the
  common ancestor of the group), `0x004cfc88` is `FractalTexture` (`base-store-ancestor`), `0x004cfd78`
  is `FireTexture` (`unshared-thunk`), and `0x004cfe68` is paired `WaterTexture` by
  `inventory/class_table_name_pairs.csv` on its child set — the three script subclasses
  `FluidTexture`, `WaveTexture` and `WetTexture`.
* **the live object read decided it.** `0x004cfba0` carries 454 objects at the title and 613 in `M00`,
  every one of class `Texture`; an object's `+0x00` is its own class's table or an ancestor's, so a
  `Texture` instance cannot carry a table only its descendant installs (`inventory/live_class_tables.csv`).
* **the tree agrees by structure.** `0x002e8260` is the body that installs `0x004cfe68`, and it calls
  `0x0027e3b0` (which installs `0x004cfba0`) at `+0x006` before its own store, so `0x004cfe68` is a
  child of `0x004cfba0`; `FractalTexture` has no out-of-line constructor at all, which is why both
  `IceTexture` and `WaterTexture` chain past it to `Texture`.
* **the registration window is why the map was wrong, and is still visible.** `WaterTexture`'s site
  `0x002e17d9` sits 0x57 bytes after `Texture`'s and pushes the same thunk `0x0027f070`, so the literal
  window attaches the neighbour's name to the table that thunk installs. It is now the only one of the
  25 unnamed sites that pushes a constructor installing *some* class's table, and the class it names is
  its neighbour's.
* **the residual was one row in a third instrument, and it is closed.** `inventory/class_table_parents.csv`
  read `0x004cfba0` as `adjacent -> WaterTexture` and `0x004cfc88` as `adjacent -> IceTexture` and
  `0x004dd2a8` as `differs -> VertexStreamBase` for one run -- not because that edge disagreed with the
  tree, but because the `ctor_`/`dtor_` symbols it reads its names from still carried the names the
  pre-#533 map gave those three tables. With the symbols corrected (see *Three rows the rule could not
  reproduce were three constructor symbols*) all three rows `agrees`, so the two instruments now
  reproduce the same names instead of the page having to choose.

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
* `tools/class-table-reconcile.py` scores the whole read against the literal artifact. Its tally is the block at the
  end of this section, generated from the committed CSVs: the verdicts worth naming are **12 tables whose literal
  names an ancestor of the object's class** (the literal names the installer, the read names the instance --
  `Triggers`/`SGC_SecurityReader`), **26 carried by more than one class**, and **one that is not a class at all**
  (`0x004e1ce8`: the literal read `new`, the objects say `System`).
* **There is no inverted row any more.** The class map now carries the pair this page derived, so `0x004cfba0`
  agrees with its objects instead of contradicting them — the `foreign-child` conflict it used to produce is gone
  from the tree's table above as well. That is what a retired contradiction looks like in this artifact: the count
  moves from `agree 139 / inverted 1` to `agree 140 / inverted 0`, and the row disappears from the report.

    python3 tools/xemu/class-tables-live.py --port 12641 --xbe $SG1_DUMP/default.xbe \
        --csv inventory/live_class_tables.csv --table 0x004cfba0 --table 0x004cfe68
    python3 tools/class-table-reconcile.py

### The read at a loaded level: the same verdict on 613 objects, and the pair's half stays empty

A second boot, this time **in M00** (`Continue` -> `QUICK SELECT MAP` -> ten `down` -> `A`, `--gdb=12391 --linger=1800`),
reads **48,646 objects / 48,631 headers (15 unreadable) / 346 pages** and pairs them with **189 tables / 376 rows**
(`inventory/live_class_tables_m00.csv`). `class-table-reconcile.py --live inventory/live_class_tables_m00.csv`
scores that read in the same block, and its one-time inverted row is now an **agree**: **`0x004cfba0` carries 613
objects, every one of class `Texture`** (up from 454 at the title), which is the name the class map carries too.

* `0x004cfe68` **still carries no object** at a loaded level, and neither does `IceTexture`'s `0x004cfc88`, so the
  texture family's second half is named by the constructor tree alone and this read can never confirm it: the classes
  that install those tables have no instance in the packages the game loads at the title or in M00.
* **None of the 24 unnamed tables is carried either**, and **279 of the 468 named ones are absent too** -- 189 of 492
  tables get a row. The read's silence is a statement about which packages are loaded, not about a table's name.
* **Ten tables appear only at M00** (`Pickup` with four pickup classes, `Pawn`/`GoaAutoTurretMK3Pawn`,
  `AIController`/`GoaAutoTurretMK2Controller`, `Projector`/`DynamicProjector`, `GUIScrollTextBox`/`SGScrollTextBox`,
  `InventorySpot`, `MaterialSwitch`, `SubActionCameraEffect`, `SubActionTrigger`, `SubActionFade`) and every one of
  them agrees with the literal artifact, so a loaded level adds coverage without moving a verdict.

Both boots scored by `tools/class-table-reconcile.py` (`--write-doc` renders this block, `--check-doc` gates it in
`make ci`; the check reads the two committed CSVs and writes nothing):

<!-- generated:class-table-reconcile -->
| the read | tables | rows | `agree` | `installer` | `shared` | `inverted` | `unrelated` | `static-unnamed` | `unknown-class` |
|---|---|---|---|---|---|---|---|---|---|
| the title read (`inventory/live_class_tables.csv`) | 179 | 312 | 140 | 12 | 26 | 0 | 0 | 0 | 1 |
| the loaded level `M00` (`inventory/live_class_tables_m00.csv`) | 189 | 376 | 143 | 13 | 32 | 0 | 0 | 0 | 1 |
| **both** | — | — | **283** | **25** | **58** | **0** | **0** | **0** | **2** |
<!-- /generated:class-table-reconcile -->

## The installer's callers: five more names, two clashes, and the reason the rest stay open

A class table is installed by a function -- `inventory/class_constructors.csv` records it as
`function_va`/`store_va` -- and MSVC makes every subclass's constructor and destructor *call* its
parent's before installing its own. So the direct callers of an installer are the classes one level
below it, and an unnamed installer's table is the deepest class that is a proper ancestor of every
named caller. `tools/class-table-parents.py` (`make class-table-parents`,
`inventory/class_table_parents.csv`, 492 rows) reads that edge: one `E8 rel32` pass over the code
section, `symbols/xbox_functions.csv` for the caller names, `inventory/classes.csv` for the supers,
1,978 caller sites in all.

<!-- generated:class-table-parents -->
| measurement | value |
|---|---|
| tables | 492 |
| named in `inventory/xbe_class_table_names.csv` | 489 |
| unnamed | 3 |
| `agrees` -- the installer's symbol names the committed class | **474** |
| `adjacent` -- the symbol names a parent or child | 0 |
| `differs` -- the symbol names an unrelated class | 0 |
| `unnamed->proposed` | 0 |
| `constrained` -- the callers share no named ancestor | 17 |
| `name-clash` -- the proposal is another table's name | 0 |
| `no-caller` -- no `E8` site reaches the installer | 1 |

| table | committed | the rule's answer | basis | verdict |
|---|---|---|---|---|
| `0x4c78c0` | Subsystem | - | `parent-of-callers` | `constrained` |
| `0x4c93d0` | Viewport | - | `parent-of-callers` | `constrained` |
| `0x4c94b0` | Client | - | `parent-of-callers` | `constrained` |
| `0x4d0318` | NetDriver | - | `parent-of-callers` | `constrained` |
| `0x4d03c8` | NetConnection | - | `parent-of-callers` | `constrained` |
| `0x4d17a8` | Property | - | `parent-of-callers` | `constrained` |
| `0x4d2400` | VoiceSubsystem | - | `parent-of-callers` | `constrained` |
| `0x4d34d8` | Exporter | - | `parent-of-callers` | `constrained` |
| `0x4d3af0` | Factory | - | `parent-of-callers` | `constrained` |
| `0x4eaea8` | RenderDevice | - | `parent-of-callers` | `constrained` |
| `0x4fa390` | Channel | - | `parent-of-callers` | `constrained` |
| `0x4fc588` | PendingLevel | - | `parent-of-callers` | `constrained` |
| `0x4fcfe0` | Download | - | `parent-of-callers` | `constrained` |
| `0x517860` | RenderResource | - | `parent-of-callers` | `constrained` |
| `0x5178d8` | VertexStreamBase | - | `parent-of-callers` | `constrained` |
<!-- /generated:class-table-parents -->

* **The installer-name edge reproduces the whole map it resolves: all 474 of the 478 named tables
  `agrees`, 0 `adjacent`, 0 `differs`.** The counts are the block above, rendered from `inventory/class_table_parents.csv`;
  they read 464 / 2 / 1 for one run because three `ctor_`/`dtor_` symbols still carried the names the
  pre-#533 map gave their tables, which is the section *Three rows the rule could not reproduce were
  three constructor symbols* below. Every named table's installer is itself a `ctor_X`/`dtor_X`
  symbol, so the same edge that `class-tree-edges.py` inverts is what the registration artifact
  encodes -- and a rule that cannot reproduce a name it already knows is not evidence for one it does
  not.
* **Six unnamed tables the callers name** (`unnamed->proposed`): `0x004c7958` `Material`
  (35 of 37 callers), `0x004c7a10` `RenderedMaterial` (7/10), `0x004c7ac8` `BitmapMaterial` (3/5),
  `0x004c7c78` `CameraEffect` (10/11), `0x004cfe68` `WaterTexture` (3/6, the three texture subclasses),
  `0x005040d0` `Engine` (1/3, from `ctor_GameEngine`). The
  three material tables form the exact chain `classes.csv` gives -- `BitmapMaterial :
  RenderedMaterial : Material` -- which the registration never said, and each of the six names has
  a registration site whose literal is that class (`Engine` at `0x00121989`, `Material`, ...) whose
  constructor pointer is the shared default and so installs no table of its own.
* **Two proposals read as `name-clash` for one run, and the clash was a stale `class_committed`
  column, not the image** (regenerated below): `0x004cfe68` -> `WaterTexture` where
  the artifact had `0x004cfba0` `WaterTexture` (the map says `Texture`), and `0x004d0228` -> `IceTexture`
  where it had `0x004cfc88` `IceTexture` (the map says `FractalTexture`). The live read already
  says `0x004cfba0` carries **613 objects, every one of class `Texture`**, so the clash corroborated
  the inversion this page records above rather than contradicting it. The column has been rebuilt
  against the map since, and both rows are `unnamed->proposed` now: the artifact reports, it does not
  re-name.
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

## The 68 classes no shipped `.u` declares are six native packages (2026-11-20)

A `<Class>_vt<slot>` name is built from a class, and `tools/module-split.py` turns it into a module through
the class's package in `inventory/classes.csv`. **273 of the 538 such rows name one of 68 classes no
shipped package declares** -- `BoolProperty`, `ActorChannel`, `ControlChannel`, `D3DRenderDevice`,
`TcpipConnection`, the mesh and vertex-stream classes -- because this build links those packages in rather
than shipping their `.u`, so the class census has no row to read.

`tools/native-class-packages.py` reads the edge that survives: `inventory/registration_sites.csv` resolves
all 493 `Class`-constructor call sites to a class literal, every one of the 68 classes has a site, and the
body holding that site is one translation unit of exactly one native package. Every class's sites agree --
68 of 68 unanimous, each through an assert the body carries, none through address locality alone -- and the
packages are the six the image links: `Engine` 39, `Core` 19, `IpDrv` 5, `XboxAudio` 2, `XboxDrv` 2,
`D3DDrv` 1. `inventory/function_modules.csv` now names a module for all 538 `<Class>_vt<slot>` rows.

<!-- generated:native-class-packages -->
| the 68 native classes' package | classes | `<Class>_vt<slot>` rows |
| --- | --- | --- |
| `Engine` | 51 | 188 |
| `Core` | 26 | 127 |
| `IpDrv` | 5 | 16 |
| `XboxAudio` | 2 | 9 |
| `XboxDrv` | 2 | 24 |
| `D3DDrv` | 1 | 10 |
| **total** | **87** | **374** |

87 class(es) resolve, 0 conflict, 0 land in no attributed body, 14 have no site. The rule is the registration site's body, and unanimity across a class's sites is the check.
<!-- /generated:native-class-packages -->

## Status

The constructor tree is a real second instrument (376/392 agreement) and it confirms four of the 24
names independently of the registration literal: three by a partly-named child set, and `0x004cfe68`
exactly. **It does not close U8's 24**: 20 are unconstrained — eleven because no constructor calls them
and nine because their children's super is a native base with no script row — and the site join is
closed the other way: 24 of the 25 push a nine-byte default constructor that stores one dword shared by
51 classes, and the 25th pushes its neighbour's constructor. What would
settle the rest is an argument the registration passes that is not the constructor:
* the **fourth and fifth pushes** of a site (`0x004d0e90`, a `.rdata` pointer, and a string VA) are the
  same in kind at every site and unread; `docs/NATIVE_CLASSES.md` records them as arguments 10 and 12
  whose meaning is not established;
* a **breakpoint on the registration call** each row of `inventory/registration_sites.csv` names, reading
  the constructed `UClass`'s `+0x00` table field as it is filled -- the read of the *object array* is
  exhausted (no object carries any of the 24 tables at the title or in `M00`), but a class object is
  built at registration even when no instance is ever spawned.

Neither is a base-class-table problem any more: the tables are located, sized and slotted
(`docs/NATIVE_VM.md`); this page adds only which of them the tree can name.

The installer's-callers edge above is the third instrument on these 24 tables and the strongest one
for the engine base classes: it reproduces every name it can check (468 `agrees`, no `adjacent`, no
`differs`), names six more, and records the seventeen it cannot
name as a constraint instead of a guess.

## The class object's constructor field is not carried (2026-11-10)

`tools/xemu/class-ctor-field.py` was written for the route this page ended on: a `UClass` object exists per
registered class even when nothing instantiates it, so its constructor field should name the table the class
installs. In a booted **M00** the object array holds **1,059** objects carrying the `Class` table
`0x004d4870`; none of their bodies holds any of the **1,011** constructors/destructors in
`inventory/class_constructors.csv`, so the read reproduces **0 of the 466** names the literal already has and
cannot name the nineteen. The negative is about the field: the build keeps the constructor in the
registration code, not in the `UClass` instance. So this page's status stands as written -- the constructor
tree (376/392) and the installer's callers (468 agrees -- every name it can check -- six named,
seventeen constrained) are the two edges that work, and the engine's own base classes need an
engine-side `classes.csv` or a registration breakpoint.
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
| `+0x220` | 8 | `eax` 8 | the absolute global `.data:0x7b9ba0`, a singleton (see the last section) |
| `+0x48` | 3 | `esi` 3 | the function's own object |
| `+0x1c` | 2 | `esi` 2 | the function's own object |
| `+0x2c` | 2 | `esi` 1, `eax` 1 | own object / argument |
| `+0x310` | 1 | `esi` 1 | the function's own object |

**28 of 28 decode, and every one is a plain `mov reg,[base+disp]`** -- 0 indexed (SIB) loads, 0 absolute ones, 0
unread -- so a field site is never a container walk or a static. The base register is a second claim, and reading
the instruction that writes it (`base_origin` in the same artifact) splits the 28 three ways rather than two: **11
are the function's own `this`**, **8 load `eax` from the absolute global `.data:0x7b9ba0`**, **7 read the stack**
and **2 an incoming register**. The earlier "20 read `this`, 8 read an argument" was the *receiver* chain's answer
(`own_register`) read as the field's: a base that arrives on the stack is not `this`, and an `eax` with no chain is
not an argument until its writer is looked at. Those 8 are still exactly the whole `+0x220` group, they sit in small
functions with exactly one direct `E8` caller each, and all eight callers fall in `0x335007`-`0x337318`: one family,
one offset, one object graph -- which the earlier "six different offsets, so not one shared object graph" reading
understated by counting offsets instead of owners.

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
unrelated fields sharing a displacement looks like. The eight `[eax+0x220]` sites do **not** read that slot off an
object passed in as an argument: `eax` comes from the absolute global `.data:0x7b9ba0`, so those 38 candidates
describe a different field at the same displacement and **none of them owns these eight sites** -- the base-origin
read at the end of this page is what names the holder, and it is a singleton, not a class.

**`+0x1c` is the negative the static half predicted**: not one live object at the title screen holds a live `UObject`
at `+0x1c`, so the two `+0x1c` sites read a dword that is either not an object pointer or is only filled later
(a mission's actor classes are not in this walk). The two `+0x2c` sites, by contrast, sit in a displacement that 78
classes populate.

**What this artifact cannot say.** A title-screen heap is not a level's: 42,128 objects with no mission loaded and
34 blocks unmapped, so the classes a mission instantiates are absent. And "the same displacement in two classes" is
not "the same property" -- each row is a candidate, which is why the eight-site group is reported as 38 classes
rather than one name. The static read that settles it is the base register's own writer, not the callers: it puts
all eight sites on a `.data` singleton, below.


## The `+0x220` base is a `.data` singleton, and the helper it holds is not a UObject

`base_origin` in `inventory/vcall_field_owners.csv` follows the instruction that writes each member load's base
register through `tools/vcall-receiver.py`'s decoder, instead of trusting the register's name or the receiver chain.
**11 of the 28 field sites read `this`, 8 read an absolute global, 7 read the stack and 2 an incoming register**, and
the eight globals are the whole `+0x220` group, all naming one address: the `mov eax,ds:0x7b9ba0` at `0x334390`,
`0x335163`, `0x333864`, `0x336d3c`, `0x336f35`, `0x333fd3`, `0x33638f` and `0x3363b0`.

**The owner is a lazy singleton in `.data`, not a UObject.** `0x7b9ba0` has **one store and 177 `mov`-form loads**
in the whole image (190 dword references; the rest are pushes). The store is `0x331a6e` in `FUN_003319fc`, a
create-once path: `mov eax,[0x7b9ba0]; test eax,eax; je` down to `push 0x8003; push 0x224; call 0x33ace5` (operator
new) -> ctor `0x330b35` -> init `0x3315d4` -> `mov [0x7b9ba0],esi`, a counter increment at `[esi+0x190]`, and the
pointer returned through an out-parameter; when it already exists the same path only increments `+0x190` and returns
it. **`0x224` is the object's whole size, so `+0x220` is its last dword**, and the init fills it exactly once, at
`0x3316b7`: `push 0x20; call operator new; push esi; call 0x331381; mov [esi+0x220],eax` -- a 32-byte helper the
singleton owns.

**That helper is a native C++ interface.** Its constructor `0x331381` ends in `c7 00 70 f7 53 00`, i.e. vtable
`0x0053f770`, which sits **outside** the 492 packed class tables (`0x004c78c0`-`0x0053b168`): the class-table map has
no row for it and no `vtable-reach` class follows from it. The family dispatches three of its slots -- `+0x44` (17),
`+0x4c` (19) and `+0x50` (20) -- which is why the slot-19/20 census found them at all: the pair is dispatched here on
a singleton's helper, not on a `UObject`.

**What this settles, and what it does not.** U8 asked which of 38 classes the callers pass; the answer is that they
pass none, because the eight sites are one native module's singleton talking to its own interface, and the `+0x220`
row of the live-heap table is a different field that merely shares the displacement (`Class` holding a `Function`).
It does not name the module: every function in `0x334e00`-`0x337500` is unattributed, none of the family carries a
`__FILE__` assert, and the only outside edge is the XONLINE section's entry stubs (`0x42bf49`, `0x42bf54`, `0x42bf97`,
`0x42bfb8`, `0x42bfc3`, `0x42bfce`, each `mov ecx,[0x7ba330]` then a `jmp` back into `.text`) -- the family calls the
Xbox Live library, which does not say what the family is.

## The helper's table is 21 slots, and the five after it are a second class (2026-11-12)

`tools/native-vtable.py` (`--check` is a synthetic byte test, no dump) censuses a native vtable from
the image: extent, per-slot boundary evidence, body size and the dispatches inside each body. Run at
`0x0053f770` it reports **26 code pointers in the run but two tables** -- `0x53f770` with **21
slots** and `0x53f7c4` with **5** -- because `0x53f7c4` has an install store of its own: `mov
[eax],0x53f7c4` at `0x3313ce`, inside the body of slot 0 of the first table (a factory that
allocates `0x14` bytes at line `0x8019` and returns the adapter). A table cannot contain another
table's start, and the dwords alone cannot show the split, because **all 26 addresses are perfectly
good function starts**; "count code pointers until one is not code" over-reads the class by five
slots. The previous note's 26 is that over-read, corrected here.

**The boundary rule the previous note proposed would have named eight slots, not 21.** Each slot is
measured three independent ways: an exact function start in `symbols/xbox_functions.csv` (`map`), a
direct `E8 rel32` call target (`call`), and "the bytes ending one past it complete a terminator"
(`tail`: `ret`, `ret imm16`, `int3` fill, `jmp rel8`/`rel32`, `jmp [reg]`/`jmp [reg+disp]` with the
displacement inside the window). On the first table the scores are `map` **6 of 21**, `call` **0 of
21**, `tail` **21 of 21**; the sibling scores 2 / 0 / 5. A census that required a known function
start would leave three quarters of the table unnamed and the extent undefined, while the terminator
rule needs neither a map nor a decompiler and covers all of it. That is why the tool makes `tail`
its primary rule and reports `map`/`call` beside it.

**The bodies dispatch the pair U8 counts, through one field.** Slot 17 (`0x33122a`) and slot 18
(`0x33124e`) both do `ecx = [esi+0x1c]; eax = [ecx]; call [eax+0x4c]` -- slot 19 of the object the
interface holds at `+0x1c` -- and slot 20 (`0x33049a`) does `call [eax+0x44]` then `call
[eax+0x48]` (slots 17 and 18) on the same held object, with four arguments. Two rows of
`inventory/vcall_dispatch_sites.csv` (`0x331247`, `0x331279`) are exactly those `+0x4c` calls, so
U8's own population contains callers from inside this family, and **no body here dispatches
`+0x50`**: the "family dispatches `+0x44`, `+0x4c` and `+0x50`" of the previous note is true of the
slots they sit at, not of the displacements they call. The five-slot sibling is a forwarder by the
same measurement: its slots 1-4 call `[ecx]`, `[ecx+0x4]`, `[ecx+0x8]` and `[ecx+0xc]` on the object
at `+0x4`.

**What it does not do is name the module.** Not one of the 26 bodies references a string
(`0 of 26`, `__FILE__` asserts included), none of them is a direct call target, and the table is
therefore the only evidence of its own extent. `inventory/native_vtable_census.csv` holds the 26
rows; the naming question stays where U8 leaves it, and the module's name needs a signature or a
caller that asserts with a source path, not another boot.

## A run of code pointers is not a table, image-wide (2026-11-12)

The `0x53f770` case is not a curiosity: the linker packs every class's vtable back to back in
`.rdata`, so "count code pointers until one is not code" merges neighbours wherever two tables are
adjacent. `tools/native-vtable.py --block` (`inventory/native_vtable_merges.csv`) walks **every** run
of three or more code pointers in `.rdata` and splits each at its install stores: **1,007 runs**,
**704 table starts**, **468 runs that begin with a table**, and **109 runs holding two or more
tables** (the store-form fix in the last section moves these to **724 / 476 / 114**).

The merges are not marginal. `0x4c7958` is one run of 233 dwords that installs **six** tables
(`0x4c7958`, `0x4c7a10`, `0x4c7ac8`, `0x4c7b88`, `0x4c7c00`, `0x4c7c78`) -- the `Material` /
`RenderedMaterial` / `BitmapMaterial` / `CameraEffect` family the class-tree thread names one by
one; `0x4c9348` (`Player`) holds five; `0x4ca618` holds 797 dwords over six. Every one of
them reads as a single table if the run is taken as the unit.

**What this changes, and what it does not.** The 492 UObject class tables were named from their
install literals, so their *starts* are unaffected -- but their *slot counts* are read from the next
start, and 109 runs show that the next start is not always the next run. The same holds for every
native interface: 704 install-started tables in `.rdata` (724 once every store form is read), against the 492 the map holds, so a large
minority of them are native C++ vtables like the `+0x220` helper's, and for those the install store
is the only delimiter there is. `make native-vtable` writes both artifacts, so a run that looks like
one table can be checked against the stores that say otherwise.

## 724 tables, one literal dispatch, and no name from a call site (2026-11-12)

`tools/native-vtable.py --reach` (`inventory/native_vtable_reach.csv`) reads back the question the
two notes above leave open -- 724 install-started tables against 492 mapped class tables, so where
could the other 232 names come from? -- by sweeping `.text` once with `objdump` (8 s over the 4.2 MB
section) and classifying every instruction whose operand holds a table address or a table slot:
`install` (`mov [mem],imm32`), `load` (`mov reg,imm32`), `lea reg,[abs]`, `push`, `absolute`
(`call`/`jmp DWORD PTR ds:<slot>`) and `other`.

**722 of the 724 tables are named by their install store and by nothing else.** No table address is
pushed as an argument, and **no site dispatches through a register base** -- `mov reg,table` followed
by `call [reg+disp]` -- anywhere in `.text`: 0 of 724. A vtable here is only ever reached through an
object pointer the code loaded from memory, so a call site cannot be where a table's name comes from:
the caller never says which table it is calling. That closes the route the last note's next step
proposed ("a name could come from a caller rather than a literal") -- there is nothing to find at
dispatch sites, in the mapped 492 as much as in the native 232.

**One table is named by a call, and it is unmapped.** `0x00564dbc` (9 slots) is called absolutely at
slot 0 from `0x00385936` and `0x0038b29a` (`call DWORD PTR ds:0x564dbc`, the second preceded by
`mov [esp+0x1c],0x564dbc`), and its three install stores are `0x00385932`, `0x0038b1c4` and
`0x0038b296`. Two fixed calls through a known function pointer are the only place in the image where
a caller names a table at all, and it names an unnamed one -- so the census adds a caller edge to
`0x564dbc`, not a name.

**The only other non-install reference is a data descriptor.** `0x004d0d80` (25 slots) is loaded into
`ebx` at `0x00012918` inside `Core_UnObjBas` (`0x00012620`), together with `0x004d13e4` and
`0x9fe3c5a3`; the three are stored as a `{start, end, constant}` triplet at `[ebp-0xb0]`,
`[ebp-0xa8]`, `[ebp-0xa4]`, and nothing calls through `ebx`. The address is the start of a `.rdata`
block, which is how the same function also treats `0x4c85ac`.

**Install sites are the naming lever, and they cover 48 of the 232.** 48 unmapped tables are
installed by a body that also installs a mapped class's table, 36 of those bodies are named
`ctor_*`/`dtor_*`, and 32 of the 48 have two slots or fewer: the layout of a secondary base-class
table laid down by the derived class's own constructor. `0x4ddf70` (2 slots) sits beside `TextBuffer`'s
`0x4ddf78` in `dtor_TextBuffer`, `0x4c960c`/`0x4c9610` beside `0x4c9618` `XboxViewport`,
`0x4d0470`/`0x4d0474` beside `0x4d0480` `TcpipConnection`, `0x4da720` (22 slots) beside `0x4da778`
`LinkerLoad`. Those 48 can be reported from committed data as "<class>'s secondary table"; the other
**184** are installed only by bodies that install nothing mapped, and that is where the naming
problem actually lives.

**Install counts already separate the populations.** 444 of the 492 mapped tables are installed
exactly twice, which is what one constructor and one destructor of the class look like, against 114
unmapped tables installed once and a long tail: `0x4c7860` (22 slots) has 41 stores in 41 bodies,
`Core_UnObjBas`, `Core_UnObj`, `Core_UnClass` and `Engine_UnChan` among them, and `0x53d590` (4 slots)
18 stores across 18 sibling `FUN_00329c80`-`FUN_0032a7b0` factory bodies.

**The table count itself had to be fixed first: the store rule read three addressing forms short.**
`immediate_store` accepted `c7 /0` only for mod 00/01/10 **without a SIB byte**, so
`mov [ebp+disp8],imm32` (modrm `45`) and `mov [esp+disp8],imm32` (modrm `44 24`) were dropped, and
`0x4dd488`'s run of 132 dwords read as two tables (22 + 110) instead of six of 22. With
`store_prefix_len` decoding every form, the same walk reports **724 table starts** (not 704), **476
runs begin with a table** (not 468) and **114 runs hold two or more** (not 109); 28 rows of
`inventory/native_vtable_merges.csv` move, including `0x4c85b0` and `0x4c9a98` (25 and 22 slots),
which had no install store at all and now start tables. The two scans agree once both reach the whole
addressing space: `objdump`'s operand census and the byte-level store census count the same 1,503
stores for all 724 rows, which is the cross-check that found the gap.

## The native tables share no function with any class table (2026-11-12)

`tools/native-vtable.py --share` (`inventory/native_vtable_shared.csv`) asks whether the 232 unmapped
tables are *related* to the 492 mapped class tables at all: for every unmapped table it counts, against
every mapped table, the slot indexes that hold the same code pointer (`matched`) and the run of equal
pointers from slot 0 (`prefix`), which is what a shared base-class layout looks like.

**0 of 232.** No unmapped table shares three slot bodies with any of the 492 class tables, and none
shares a three-slot prefix -- the native vtables carry not one of a mapped class's virtual functions,
so they are a separate population rather than a UObject hierarchy the map failed to name. The
comparison masks every body shorter than 16 bytes first, and that mask is the measurement, not a
convenience: unmasked, two 60-slot tables (`0x4eaf68`, `0x51a810`) "share" six and four slots with
`AudioSubsystem`'s table, and each of them is the one-byte `ret` stub that 327 of the 492 tables also
hold at slots 19/20. A shared stub is not a shared function; masking it takes the image from two
tables with a mapped neighbour to none.

**This stacks with the install-site result above.** 48 of the 232 unmapped tables are installed by a
body that also installs a mapped class's table, and that names the *owner* of a secondary base table
-- but the slot bodies carry no mapped function, so nothing in the table itself says what its virtuals
do. Naming the 184 with no install neighbour and no shared body needs a signature or an asserting
caller, which is where U8 already stands, now with the static routes enumerated and closed.

## The 17 constrained tables are one call below a named layer (2026-11-14)

`inventory/class_table_parents.csv` names a table from the classes that call the function installing it,
and 17 of its rows come back `constrained`: those callers share no ancestor *in* the tree the rule
climbs, `inventory/classes.csv`. That tree is the packages' script declarations -- **0 of the 63 caller
tables those 17 rows name has a class row in it** -- because the callers are the engine's native-only
classes (`Channel`, `Property`, `NetDriver`, `VertexBuffer`, `AudioSubsystem`, `Level`, ...), which the
shipped `.u` files do not declare. The verdict is a gap in the tree, not an ambiguity in the image.

**The image carries the relation one level down.** A destructor installs its own table at `[this]` and
then calls its base destructor, so the first call in that body whose target stores a class table is the
parent's constructor. Following the null-check thunk in between (`mov ecx,[esp+4]; test ecx,ecx; je; jmp`,
whose symbol-map size stops nine bytes short of the `jmp`) gives a parent for **490 of the 492 tables**:
`Object` has none, and `GameInfo`'s installer is the other miss. Tool, artifact:
`tools/class-table-parent-edge.py`, `inventory/class_table_parent_edges.csv`.

- **It reproduces the tree it does not read.** 345 of the 490 edges are on tables whose class *and* whose
  parent both carry a script declaration, and the parent is `classes.csv`'s `super` in **345 of 345** --
  no edge contradicts the tree. 88 point at a table the registration does not name, 57 are on a class the
  script tree does not declare, and 2 have no edge.
- **The constructor is the wrong body for this read.** Preferring a constructor's first table-storing call
  names the *grand*parent on 13 tables (`StargateVehicle` -> `Vehicle`, where the script says `SVehicle`),
  so the destructor is read first and the constructor only when the destructor yields nothing -- the same
  skipped level the constructor tree already reports elsewhere.
- **Every one of the 17 gets a parent.** Nine are direct children of `Object`; the other eight sit under
  five tables: `0x4c78c0` (3: the net-driver, voice and render-device bases), `0x4c9348` (2: the viewport
  and client bases), `0x4d1468`, `0x4fc4f0` and `0x517860` (the vertex-buffer family). None is a root, so
  the caller rule had the structure right and only the names missing.
- **The children confirm it 48 of 63 times, and the 15 exceptions are one table deeper.** Of the 63 named
  caller tables the 17 rows list, 48 have their own parent edge back to the table. The 15 that do not are
  both rows that split into a two-level chain: `0x4d1468` parents `Const`, `Enum` and `Struct` while the
  13 property classes hang off `0x4d17a8`, whose own parent is `0x4d1468`; and `Level` hangs off
  `0x4fc4f0` while the two pending-level classes hang off `0x4fc588`, whose parent is `0x4fc4f0`.
- **Slot 1..n is measured out, so the join the plan proposed is not the instrument.** In
  `inventory/xbe_class_table_slots.csv`, 17 of the 143 slot columns hold one value on all 492 tables
  (`0x00062650`, `0x00062660`, `0x00062670` at 0-2, `0x00046d20` at 5, ...), slot 3 holds **492 distinct**
  values, and the rest hold two to 90. Every pair of tables therefore shares a prefix that stops at slot 3
  at the latest: the one column that differs on every table is the one a slot-to-slot join cannot use.

**What is left is a name, not a position.** The 17 sit inside the 24 tables the registration cannot name
(U8) and now carry a parent and a child set, so the next instrument is the 25 registration sites whose
literal the table link cannot pair: pairing literal to position replaces guessing a name.

## The tree pairs six of the 24, and the other reads do not exist (2026-11-15)

Twenty-five registration literals never reach a table: they push the shared default constructor
`0x00034650`, which stores the object root's table and no class argument (`docs/systems/CLASS_REGISTRATION.md`),
so the 24 tables their classes own stay addresses in `inventory/xbe_class_table_names.csv`.
`tools/class-table-name-pair.py` is the read U8 asked for -- literal to position -- and it decides the
question with two rules that are each measured on the 467 tables that are **already** named before being
applied to the 24:

| rule | what it reads | on the named 467 |
|---|---|---|
| `child-super` | a named child table's class declares a script `super` in `inventory/classes.csv`, so the parent table is that super's table | **85 of 85**, no counter-example |
| `parent-super` | the candidate whose own script super is the name of the table's *measured* parent (`class_table_parent_edges.csv`), where exactly one candidate has it | **29 of 29**, but it leaves one candidate on only 29 of 389 parented tables |
| `family` | every named child contains the candidate (`...Property`, `...Texture`) | 10 of 16, and **rejected as a namer**: it answers the family ancestor -- `Info` for `ZoneInfo`, `Emitter` for `ParticleEmitter`, `Texture` for `FractalTexture`, `Material` for `RenderedMaterial` |

**Six tables get a name, and five of the six are the ancestor's child rather than the ancestor** -- the
distinction the family rule gets wrong: `0x4c7958` `Material` (3 children declare `super Material`),
`0x4c7a10` `RenderedMaterial` (7 children, so a family fit of `Material` would have stopped one level
high), `0x4c7ac8` `BitmapMaterial` (3), `0x4c7c78` `CameraEffect` (10), `0x4cfe68` `WaterTexture` (3, and
its own script super `FractalTexture` is its measured parent), `0x5040d0` `Engine` (`GameEngine` declares
`super Engine`). `0x4cfe68` also **settles the `WaterTexture` name clash** in `class_table_parents.csv`:
that artifact gives `WaterTexture` to `0x4cfba0`, which the class map calls `Texture`, and this read puts
`WaterTexture` one table below it, where `WaterTexture : FractalTexture` says it belongs.

**Five more carry a family anchor and nothing decisive**: `0x4d17a8` `Property` (13 of 13 children are
`*Property`), `0x4eaea8` `RenderDevice`, `0x4fa390` `Channel`, `0x4fc588` `PendingLevel`, `0x4fcfe0`
`Download`. **Thirteen are open**, and three of those are open for a structural reason, not a missing
name: `0x4c93d0` (`XboxViewport`), `0x4c94b0` (`XboxClient`) and `0x5178d8` have one or five children but
no child whose class declares a super and no candidate above them.

`inventory/class_table_name_pairs.csv` is one row per table with the votes that produced the verdict, the
family anchor and the site the name came from, so a later read can disagree with a row rather than with a
page.

**Two routes the image does not offer, both measured this run.** (1) The installer body cannot name its
own table: all 26 bodies the artifact left unnamed before it was regenerated below (the 24 tables plus the
two #533 had already renamed) were disassembled and every absolute operand that lands in a data section
resolved to a string -- **0 carry a string at all**, which is what a 79-131-byte `mov [ecx],<table>; call <base>; ret`
should do. (2) The registration's size argument cannot be the key: `inventory/class_size_sources.csv`'s
own 24 `no-registration` rows are exactly these tables, so every size in the project's artifacts originates
at a registration site and the join would be circular.

What would settle the rest is a **runtime** read -- U8's `tools/xemu/class-tables-live.py` reads a loaded
`UClass`'s name field and needs a boot -- or an engine-side class list for `Engine`'s native classes,
which is the external data U8 has been blocked on since the script tree turned out not to declare them.

## The parents artifact was grading a stale column (2026-11-15)

`inventory/class_table_parents.csv` copies its `class_committed` column out of
`inventory/xbe_class_table_names.csv`, and nothing compared the two afterwards. When #533 re-named five
tables behind a better rule, the artifact kept the names it was generated with and its verdict column
graded the new map against them: **five rows, one revision stale** -- `0x4cfba0` `WaterTexture` (map
`Texture`), `0x4cfc88` `IceTexture` (map `FractalTexture`), `0x4cfd78` and `0x4d0228` blank (map
`FireTexture`, `IceTexture`) and `0x4dd2a8` `VertexStreamBase` (map `Object`). Both
`name-clash:<other table>` verdicts this page recorded were that column rather than a measurement of the
image, and so was the reading that the installer-name rule reproduces the map whole.

Regenerated against the re-named map, the verdicts were **464 `agrees`, 2 `adjacent`, 1 `differs`, 6
`unnamed->proposed`, 17 `constrained`, 2 `no-caller`**, with all three disagreements on a table #533
re-named. That is the second stale copy in the same chain, and the next section is what it was.

**The six proposals are the same six tables the tree pairs, with no extra row and no disagreement**:
`Material` (`0x4c7958`), `RenderedMaterial` (`0x4c7a10`), `BitmapMaterial` (`0x4c7ac8`), `CameraEffect`
(`0x4c7c78`), `WaterTexture` (`0x4cfe68`) and `Engine` (`0x5040d0`). Two instruments that share no input
-- the nearest common ancestor of the installers' callers, and the script supers of the child tables --
name the same six, which is the corroboration the pairing needed and a check on both.

**The gate that would have caught it is in `--check` now.** `committed_names_join` reads the committed
artifact against the class map it copies, all 492 rows, and fails naming every stale row; on the pre-fix
file it reports 5. It is a CSV join, so the tool keeps its no-dump, no-write `--check` contract.

## Three rows the rule could not reproduce were three constructor symbols (2026-11-15)

The 2 `adjacent` rows and the 1 `differs` row above were not a disagreement between the installer-callers
edge and the class map, and not the caller rule answering one level low: **all three were `own-installer`
rows, and the symbol they read was stale.** `tools/class-table-parents.py` names a table from its
installer's `ctor_X`/`dtor_X` symbol when that installer is already named -- and those names are the ones
`tools/class-ctors.py --apply` wrote, from the class map of the day. `--apply` names a `FUN_*` row and
never revisits a name it wrote, so when #533 re-named five tables the symbols kept the old classes:

| function | symbol | installs | map now says |
|---|---|---|---|
| `0x0027e3b0` | `ctor_WaterTexture` | `0x004cfba0` | `Texture` |
| `0x00025550` | `dtor_WaterTexture` | `0x004cfba0` | `Texture` |
| `0x00062ce0` | `ctor_VertexStreamBase` | `0x004dd2a8` | `Object` |
| `0x00025660` | `dtor_IceTexture` | `0x004cfc88` | `FractalTexture` |

**The measurement is a join of two committed CSVs** (`inventory/class_constructors.csv`'s
`function_va` -> `table_va`, and `inventory/xbe_class_table_names.csv`'s `table_va` -> `class`), run over
every symbol that carries a class name: **923 agree** with the one table their body installs, **4 are
stale**, **1 installs two tables**, **86 installers name no class**. The four are the three tables above
plus `dtor_WaterTexture`, which is the same table as `ctor_WaterTexture`.

**One row is correctly left alone, and it is the reason the rule needs a guard.**
`0x002e8370` `ctor_IceTexture` stores `0x004cfc88` **and** `0x004d0228`: a constructor installs its
parent's table before its own, so a two-store body names the *derived* class and the extra store is not
evidence against it. The class map already says as much -- `0x004cfc88`'s row is `name_rule
base-store-ancestor`, "2 classes (FireTexture, IceTexture) store it before their own table". So
`--fix-stale` renames only a **single-store** symbol whose table the map names otherwise, and `--check`
reports the two-store bodies instead of failing on them (`0x002e81f0` `ctor_FireTexture` is the second).

**The guard's exemption is wider than the shape that justified it.** `class-ctors.py --multi-store`
(CSVs and the symbol map only, no dump) lists **6** bodies that install more than one class table, and the
parent-then-own shape covers **2** of them: `0x002e81f0` `ctor_FireTexture` and `0x002e8370`
`ctor_IceTexture`, exactly the pair the class map cites for `0x004cfc88`. The other four are not one
class's constructor at all (the decompile below says what each of them is) -- `0x0024af30` (809 bytes) stores `BoolProperty`'s table four times at
+151/+206/+261/+316, 55 bytes apart in a straight line, then `ObjectProperty`, `BoolProperty`,
`ArrayProperty` and `StructProperty`; `0x0014e0e0` `Core_UnClass` (917 bytes) mixes `NameProperty`,
`StrProperty` and `StructProperty` the same way; `0x00110120` stores `PackageMapLevel` 822 bytes after an
unnamed table; and `0x002e8260` stores `0x004cfe68` -- the `WaterTexture` proposal -- immediately after
`0x004cfc88`. So the exemption is a measurement ("this body is not one class's installer"), not an
inference from the base-store shape, and the report is where the difference is visible.

**Corrected, the edge reproduces the map whole: 468 of 468 named tables `agrees`, 0 `adjacent`, 0
`differs`** (the generated block in *The installer's callers* above). The residual the previous note
called "a rule reading one level low" was this symbol column; the caller rule never had to answer at all.

**The gate is permanent and sits in the tool that wrote the names.** `class-ctors.py --check` now runs
`installer_names_join` over the three committed CSVs and fails naming each stale row (it reported these
four on the pre-fix tree), and `--fix-stale` -- in `make class-ctors` now -- corrects both
`symbols/xbox_functions.csv` and the `symbols/xbe_overrides.csv` row a future import would re-apply.
The verdict counts on this page are generated too: `tools/class-table-parents.py --write-doc` renders
the generated `class-table-parents` block from the committed artifact, `--check-doc` recomputes it with no
dump, and `make ci` gates it, because two hand-typed copies of the same tally had already drifted.

Tool (`tools/class-ctors.py`: `stale_names`, `apply_stale`, `--fix-stale`, the `--check` join and its
synthetic case; `tools/class-table-parents.py`: `--write-doc` / `--check-doc`) + the four renames and the
regenerated artifacts (including `inventory/function_modules.csv`, whose positional `run` column
re-indexes past a renamed row) + this page + `docs/UNKNOWNS.md` U8 + the `class-ctor-base` target, which
passed `--xbe` and a file to a tool that takes the path positionally and a directory.

## The bodies that store more than one table are three kinds of body (2026-11-15)

`--multi-store` reads three CSVs, so all it can say about a body is "this is not one class's
constructor". One Ghidra round reads the same six bodies from the image, and the four it could not place
are **two property factories, one constructor that also builds a child object, and one constructor whose
own table is the unnamed one** -- three kinds of body that a store count cannot separate.

| body | shape | stores | what each store is |
|---|---|---|---|
| `0x002e81f0` `ctor_FireTexture` | parent-then-own | 2 | `FractalTexture`, then its own |
| `0x002e8370` `ctor_IceTexture` | parent-then-own | 2 | `FractalTexture`, then its own |
| `0x002e8260` | parent-then-own | 2 | `FractalTexture`, then `0x4cfe68` -- its own |
| `0x0024af30` | property factory | 8 | one table per property it creates |
| `0x0014e0e0` `Core_UnClass` | property factory | 4 sites | one per property, one site inside a loop |
| `0x00110120` | constructor + child | 2 (+2 sub-tables) | its own, then `PackageMapLevel`'s |

**`0x0024af30` is 248 instructions that build eight properties and install no table of its own.** It
arrives with `this` in ECX, hangs everything on the class at `[this+0x24]`, and every one of the eight
stores is the same three steps: `call <factory>` returning the new object in `ESI`,
`mov [ESI],<property class table>`, `mov [ESI+0x6c],<offset in the owner>`. The four `BoolProperty`
stores are `UseSimpleLineCollision` (offset `0x13c`), `UseSimpleBoxCollision` (`0x140`), `UseVertexColor`
(`0x144`) and `UseSimpleKarmaCollision` (`0x148`), 55 bytes apart because the construction is inlined, not
because a table run is that long. The rest build a nested struct class `StaticMeshMaterial` (0x80 bytes,
`PropertiesSize` `0xc` written at `+0x40` and linked by the virtual call at `+0x90`) holding an
`ObjectProperty` `Material` at offset 0 and a `BoolProperty` `EnableCollision` at offset 4, an
`ArrayProperty` `Materials` at `0x114` whose inner is literally named `StructProperty0` and points back at
that struct. The tail is the object's defaults: `[this+0x13c]=0`, `[this+0x140]=1`, `[this+0x144]=0`,
`[this+0x148]=1` -- the offsets the properties were given, written into the class-default object, which is
what a native static constructor does.

**`0x0014e0e0` has the same shape with a loop in it.** It builds a struct class `Alias` (0x80) with a
`NameProperty` `Alias` and a `StrProperty` `Command`, a `StructProperty` `Aliases` of that type at `0x2c`,
then walks the `EInputKey` name table and creates **one property per key**, named from the entry at
`[entry+0x12]`, in a loop from `0x2ac` to `0xea0`. Four store sites, hundreds of objects at run time: a
multi-store count is a count of *sites*, never of tables a body owns. Its symbol `Core_UnClass` came from
an inlined `UnClass.h` assert (`Defaults.Num()==GetPropertiesSize()`, line `0x235`), not from the body
being in `UnClass.cpp`.

**`0x00110120` is a constructor that also builds a sub-object.** It writes `0x4d03c8` into `[this]` and
the two tables immediately below it in `.rdata` (`0x4d03bc`, `0x4d03b8`) into `[this+0x28]` and
`[this+0x2c]` -- one class with two base sub-objects, which a table census reads as three tables -- and
881 bytes in it constructs a `PackageMapLevel` (`call 0x6c8a0`, then `mov [esi],0x004fb9b8` and
`mov [esi+0x70],this`) and stores *that* object's table.

**`0x002e8260` is the shape the exemption was looking for and missed.** It calls the base constructor,
stores `FractalTexture`'s table, and then stores `0x4cfe68` -- parent-then-own exactly like
`ctor_FireTexture` and `ctor_IceTexture`, which the census had called `unnamed` only because the map has
no class for `0x4cfe68`. That is a second, structural route to the `WaterTexture` proposal the tree
section above reaches by ancestor votes: the body is the constructor `ctor_WaveTexture`,
`ctor_FluidTexture` and `ctor_WetTexture` all call as their base, and it installs a table of its own.

`--multi-store` now prints this as three lines instead of one: **2** parent-then-own with both tables
named, **1** more constructor-shaped with an unnamed own table, **2** that are not installers at all. What
it cannot print from CSVs is the receiver, so `0x00110120` still reads as constructor-shaped: the census's
`form` column describes the body, not the store.

**The silence this read started from is a tool gap, and it is closed.** Ghidra's analysis has no function
at `0x0024af30`, so `ExportAt.java` and `ExportLiterals.java` both wrote nothing for that address -- no
file and no error, which reads like a body that does not exist rather than a function never made. The new
`tools/ghidra/ghidra_scripts/DumpBody.java` takes the address from the symbol map, disassembles it,
creates the function, and prints the full listing with literals resolved plus the decompiled body; all six
bodies above were read with it.

**The callers name one factory, and the other is not a registration's body at all (2026-11-15).**
`tools/vtable-reach.py` on `0x0024af30` answers **no** `E8` site and **no** dword reference, and a byte-wise
scan of `.text` finds the only reference to it in the image: `PUSH 0x24af30` at `0x0024b6d1`, 65 bytes into
`Engine_UnStaticMesh` (`0x0024b690`). That body registers the class the same call literal names -- it asserts
`ReturnClass` at `Engine/Src/UnStaticMesh.cpp:0xf`, and its `ReturnClass` call at `0x0024b70c` pushes
`PUSH 0x1a0` (416, the object size `inventory/xbe_class_table_names.csv` carries for `0x0051dd28`),
`PUSH 0x51ddca` (`L"StaticMesh"`), and `PUSH 0x24af30` beside the destructor thunk `0x24f730` that the
registration census already follows. So the class whose property list `0x0024af30` builds is **`StaticMesh`**,
not `StaticMeshInstance`: the property set could only suggest a name, the pushed literal states it.

**`0x0014e0e0` has one caller and it is `UEngine::Init`.** The census finds a single `E8` site, `0x0013970e`
in `Engine_UnGame` (`0x00139030`, the 3,348-byte engine initialiser), and no row of
`inventory/class_registrations.csv` names the body -- so no caller slot answers for it. It fetches the class
object itself (cached in the global at `0x007ab62c`, written at `0x0014e177`) and hangs the property set on
it, and that set -- `Alias` (a struct class of `NameProperty` `Alias` + `StrProperty` `Command`), `Aliases`
at `0x2c`, one `RawKeys` element per `EInputKey` from `0x2ac` to `0xea0` -- is the engine `Input` class's
property list. So one factory is named by the registration that pushes it and the other only by the
properties it creates.

| body | route | receiver | evidence |
|---|---|---|---|
| `0x0024af30` | `registration-arg` | `StaticMesh` | pushed at `0x0024b6d1` beside `PUSH 0x1a0` and `L"StaticMesh"` |
| `0x002e81f0` `ctor_FireTexture` | `registration-thunk` | `FireTexture` | registration thunk `0x00018120` forwards here |
| `0x002e8370` `ctor_IceTexture` | `registration-thunk` | `IceTexture` | registration thunk `0x00018160` forwards here |
| `0x002e8260` | `base-call` | -- | base of `ctor_WaveTexture`, `ctor_FluidTexture`, `ctor_WetTexture` |
| `0x0014e0e0` `Core_UnClass` | `direct-call` | -- | one caller, `UEngine::Init` |
| `0x00110120` | `direct-call` | -- | three callers, none of them a registration |

**Three of the six are named and three are not, and the split is the finding.** A route names a class only
where a registration carries the body: as an argument of its `ReturnClass` call (one body), or as the
`thunk_target` the census already follows (two). The other three are reached by ordinary calls from bodies
that are not registrations, so their class is not in the call graph at all -- `0x002e8260`'s is the table it
installs (the unnamed `0x4cfe68`), `0x0014e0e0`'s is its property set, and `0x00110120`'s is the class whose
constructor it is, which its own store names. `inventory/class_ctor_receivers.csv` is that table, and it
leaves a blank instead of copying a guess into a column a later join would trust.

**Neither factory has a registration in its caller chain, and that is measured, not assumed (2026-11-15).**
Every caller's own symbol span is searched for a registration site (`site_va` in
`inventory/class_registrations.csv`), and the search is **negative for both bodies**: `0x0014e0e0`'s single
caller `UEngine::Init`, and `0x00110120`'s three callers `FUN_0011d460` (66 bytes), `FUN_002d2890` (407) and
`FUN_002d55f0` (434) -- and negative one hop further up as well, at `Engine_UnDemoRec`, `FUN_002d2bb0`,
`FUN_002d2cd0` and `FUN_002d5c70`. So no caller slot holds a class for either body: the blank `receiver`
column is an answer rather than an unread cell, and `inventory/class_ctor_receivers.csv`'s census carries the
count (`caller-regs=0`) so the negative is in the artifact and not only in this sentence.

**The first version of that join found eight registrations and every one of them was the units.** It read
`symbols/xbox_functions.csv`'s `size` column as hex, which turns a 66-byte wrapper into a 0x66 = 102-byte one
and a 407-byte body into 0x407 = 1031, so three small wrappers' spans overlapped the registration bodies that
*neighbour* them: `TcpipConnection`/`TcpNetDriver`, `TcpipConnectionXbox`/`TcpNetDriverXbox`, `DemoRecDriver`,
`Download`, `ChannelDownload`, `RenderDevice` -- "eight registrations in three modules behind a 66-byte
wrapper", which reads like a finding and is an arithmetic error. The column is decimal; the join now answers
none, and `--check` pins the other half of the rule (a site outside the caller's span is not counted).

Tool (`tools/class-ctors.py`: the `verdict` column, the unnamed-parent and factory lines, and
`--receivers`; `tools/ghidra/ghidra_scripts/DumpBody.java`) + `inventory/class_ctor_receivers.csv` (6
bodies + census) + this page + `docs/UNKNOWNS.md` U8 + the PLAN note. No boot: the image, the symbol map
and the committed registration census, and the decompiled bodies stay under `$WORK/propcluster/`.

## Six of the 24 are named by the tree, and one level is as far as it goes (2026-11-15)

**The pairing is applied, not only measured.** `tools/class-table-names.py` reads
`inventory/class_table_name_pairs.csv` and fills a row only where its `class` is empty, so the six
tables the child-super rule pairs carry a name and say where it came from: `0x4c7958` `Material`,
`0x4c7a10` `RenderedMaterial`, `0x4c7ac8` `BitmapMaterial`, `0x4c7c78` `CameraEffect`, `0x4cfe68`
`WaterTexture` and `0x5040d0` `Engine`, all with `name_rule` `tree-paired` and the vote that produced
them in `evidence`. The map reads **478 named of 492**, 14 unnamed, and every count on this page is
regenerated from it — `make class-names` from the image reproduces the artifact byte for byte, because
the pairs file is a committed input rather than a hand edit.

**The second instrument closes on all 474 it resolves.** `inventory/class_table_parents.csv` — the installer's
*callers*, which shares no input with the child supers — moves from 464 `agrees` and 6
`unnamed->proposed` to **474 `agrees`, 0 proposed** of the 478 named tables, with the 2 `adjacent` and 1
`differs` it already had untouched; the four names the 2026-11-21 round added are its `constrained` rows,
because a name no caller rule can derive is exactly what that rule cannot confirm. Six of six on the rows
that were open is a check on both rules at once.

**A second round is measured wrong, so the pairing is one level deep by construction.** The pair tool's
fixpoint lets a fresh label answer its children inside one run, which makes letting the six applied
names vote in the *next* run the obvious step. Iterating the two tools to a fixpoint (three rounds, each
a 3-second regeneration) answers: round two names `0x4c78c0` `Subsystem` on the single `Engine` vote, and
round three hands `Engine` to `0x004d0318`, `0x004d2400` and `0x004eaea8` — whose measured children are
the three net drivers, the voice subsystem and the render devices (`NetDriver`, `XHVVoiceSubsystem`,
`RenderDevice` territory), so at least three of the seven are wrong. The rule's own control degrades
with it: `parent-super` falls from **29 of 29 to 30 of 36**. So
`tools/class-table-name-pair.py` blanks its own output (`unnamed_by_image`) before any rule reads the
map — a name this tool derived is not evidence for the next one — which keeps the population at the 24
tables and re-renders the pairs CSV unchanged.

**`Material` is the one name of the six that added a conflict, and the cause is the skipped level.**
Naming `0x4c7958` is what put its **32 extra children** into the comparison, all of them descendants
(`PSAdditiveDistortion`, `PSBloomSelectMaterial`, and `RenderedMaterial`'s inlined base chain), which is
the `descendant-edge` cause this page already documents for `Vehicle`, `Texture` and `Object` — the 16
conflicts are 10 `no-script-row`, 5 `descendant-edge` and 1 `native-child`. The installer's-callers rule
agrees with the name all the same, so the row stands and the conflict is recorded as the tree's own
inlining artefact rather than as a doubt about the name.

Tool (`tools/class-table-names.py`: the `tree-paired` rule, `paired_names`, `apply_pairs` and three
`--check` assertions over a synthetic pair; `tools/class-table-name-pair.py`: `unnamed_by_image` and
three assertions, plus the cascade note in its docstring) + six rows of
`inventory/xbe_class_table_names.csv` + the artifacts that read it (`class_constructors`,
`class_ctor_bases`, `class_size_sources`, `class_table_parents`, `symbols/xbox.map`) + the generated
blocks above and on `docs/systems/CLASS_REGISTRATION.md`, `docs/STATUS.md` and the README badges +
`docs/UNKNOWNS.md` U8. No boot: the image and the committed inventories.

**Next measurement:** the 18 that remain need a name the stores do not carry —
`tools/xemu/class-tables-live.py` on a level with the renderer and net-driver packages loaded (one
boot; U8's runtime route), or an engine-side class list.

## The audio slot's receivers, typed (2026-11-20)

U8's instrument reads a displacement's register; the audio module's census already lists its own sites, so
`tools/vcall-receiver.py --from-census inventory/xboxaudio_chain.csv --census-kind slot31` writes
`inventory/audio_slot_receivers.csv` (115 rows: 106 calls, 9 pattern hits inside another instruction) without a
second decoder. `tools/audio-slot-typing.py` then joins each row to the class tables read out of the image
(`inventory/audio_slot_typing.csv`):

| measurement | value |
|---|---|
| `call [reg+0x7c]` sites in `.text` | 115 |
| of those, real calls (two decoders agree) | **106** |
| receiver is the function's own `this` (typed through the holder's table) | **24** |
| of those, the holder's slot 31 is the device body | **0** |
| tables long enough to have a slot 31 | 403 of 492 |
| distinct pointers in slot 31 across them | 112 |
| tables installing the device body (`0x0002fd70`) anywhere | **1** (`XBoxAudioSubsystem`, slot 31) |
| tables installing the pump body (`0x0002feb0`) anywhere | **1** (same table, slot 34) |

The control is the base class: `AudioSubsystem`'s own table installs `0x000e3e30` at slot 31 and
`Engine_UnAudio` at slot 34, so both slots are overridden by the fork rather than inherited, and a dispatch
through an `AudioSubsystem` vtable reaches the fork's device. What types the *field* is the script:
`inventory/audio_owner_declarations.csv` records the three subsystem-typed variables in the 2,319 class texts of
the 18 packages, all in `Engine` (`Audio : audiosubsystem`, `GS : GSSubsystem`, `Voice : voicesubsystem`). The
offsets those variables live at are not in the packages, which is why the 31 field-origin sites stay untyped.
