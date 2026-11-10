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
class tree for 96% of the classes that have subclasses at all. The 15 disagreements are listed by the
same run against the CSVs, and they are the population a later pass should read — not this page's
subject.

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

**24 of the 27 push the same pointer: `0x00034650`, a thunk to `0x00062ce0`, which installs table
`0x004dd2a8`.** One pointer for `Object`, `Engine`, `Property`, `Player`, `Texture`, `NetDriver`,
`LevelBase`, `RenderDevice` and sixteen more is not a per-class constructor; it is the shared base
constructor these registrations pass. That is the mechanism behind U8's residual, measured rather than
assumed: the literal path has no per-class table to link to because the argument it would link through
is one shared function.

**Three of the 27 do resolve, and they show why the window has to be read with care.** `Texture`
(`0x0027e889`) and `FractalTexture` (`0x002e1489`) both push `0x0027f070`, a thunk into `0x0027e3b0`
— the constructor of **`WaterTexture`**, whose own registration site is `0x0027e8e0`, 0x57 bytes after
`Texture`'s. The 96-byte literal window therefore holds a *neighbouring* registration's push, which is
exactly the reading `registration-sites.py` warns about when it reports the class of such a site as
"the site's only identifier literal". Only `FireTexture` (`0x002e8468`) pushes a pointer that lands on
its own installer: `0x00018120` → `0x002e81f0`, which stores table `0x004cfd78`.

## Status

The constructor tree is a real second instrument (375/391 agreement) and it confirms three of the 26
names independently of the registration literal. **It does not close U8's 26**: 23 are leaves with no
callers, and the site join runs into one shared base constructor for 24 of 27. What would settle the
rest is one of:

* a **runtime read** — the object array holds each `UClass`, whose `+0x00` is the table, so a boot that
  walks the array and reads each class object's name pairs every table with a name from the build
  itself (`tools/xemu/mem.py`, `tools/xemu/names.py`);
* a **decompile of `0x00062ce0`**, to see what the shared argument at `0x00034650` is and whether the
  engine's base registrations pass their own constructor somewhere else in the same function.

Neither is a base-class-table problem any more: the tables are located, sized and slotted
(`docs/NATIVE_VM.md`); this page adds only which of them the tree can name.
