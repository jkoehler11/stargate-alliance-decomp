# The class constructor chain, and the second copy of it in the image

`inventory/class_constructors.csv` names a constructor for every class the image builds, from the
class table each one installs. Four of those tables are the whole UE2 object model, and their
constructors chain: `UObject` (`0x004dd2a8`), `UStruct` (`0x004d4418`), `UState` (`0x004d4570`) and
`UClass` (`0x004d4870`), each ctor installing its own table last and calling its base ctor first.

`UClass`'s registration ctor is `0x0003af60`: the 493 `ucpp_*` stubs call it with a class literal and
a flags word, and `tools/class-flag-stores.py` reads the flags it composes out of those 493 sites
(`docs/systems/CHECKPOINTS.md`, *How a bit can reach the class flag word*). That note closed with a
second `UClass` ctor in the image -- `0x0003c430`, found by the `int3`-padding rule, called by nothing
-- and nominated the pair `0x0003c430`/`0x0003bd30` against `0x0003af60`/`0x0003a5f0` as the next
measurement: *what did the linker keep a copy for?*

`tools/native-unreachable.py` answers it, and answers the general question behind it. A body is
**unreachable** when no byte of the image points at it, counting every way this build points at code:
a relative `E8` call, a relative `E9` jump, or a dword in any section equal to the start (a table
slot, a registration table, a `push imm32`). The population is `symbols/xbox_functions.csv` plus the
bodies that begin at the image's own `int3` padding and carry no map entry. A body whose every
inbound edge comes from a body that is itself unreachable goes too, so the answer is a fixpoint and
not a single in-degree count -- which is what makes `0x0003bd30` dead although it has two callers.

<!-- generated:native-unreachable -->
**3773 of the image's 19617 bodies are unreachable, and the copy the previous note nominated is one of them.** The population is the 16375 bodies `symbols/xbox_functions.csv` names plus the 3242 that start at the image's own `int3` padding and carry no map entry; an edge is a relative `E8` call, a relative `E9` jump, or a dword in any section equal to the start. 3335 bodies have no outside edge at all, and 438 more lose theirs once bodies whose callers are all in that set are followed to a fixpoint -- **3773 in total (3101 the map names, 672 found only by padding), in 426 runs, 2702 of them carrying a real name and 1858 starting on a 4-byte boundary.** Both facts about a start are kept per row: a body the map names at an odd address that nothing references may be a definition Ghidra invented over data, and this measurement does not separate that from dead code.

**The 2702 named dead bodies split by what the name is: 2550 a shipped-library symbol (a C++ mangled name, an `__stdcall` decoration or a C `_name`), 78 a `<Module>_<File>` assert token and 74 another identifier -- and the 672 padding-only bodies carry no name at all, because they have no map row.**

**The unreachable bodies are not scattered: they form 426 runs, and the biggest single run is 362 bodies over 0x49f360-0x4aa5d8, of which 114 start on a 4-byte boundary.** A run is a set of unreachable bodies no more than `0x400` apart; a run that starts and ends inside live code is what a dropped translation unit looks like, and a run whose starts are mostly off a 4-byte boundary is more likely the map's own definitions over data -- which is why each run row carries both numbers.

**The nominated pair is a second compilation of the class constructor family, with the State constructor inlined into it.** Read from the bodies themselves:

| body | what it is | installs | base call | zeroes | outside edges |
| --- | --- | --- | --- | --- | --- |
| `0x03af60` | registration ctor (493 call sites) | `0x4d4870` | `0x3a5f0` | 25 stores | 493 |
| `0x03c430` | the copy the previous note nominated | `0x4d4870` | `0x3bd30` | the +0x80 block | 0 |
| `0x03a5f0` | base the registration ctor calls | `0x4d4570` | `0x62470` | the +0x80 block | 1 |
| `0x03bd30` | base the nominated copy calls | `0x4d4418` | `0x3b440` | 13 stores | 2 |
| `0x03c3d0` | sibling copy: installs the State table | `0x4d4570` | `0x3bd30` | the +0x80 block | 0 |

Two things follow from the table. **First, the copy installs the same class table as the registration ctor (`0x4d4870`) and sets the same flags, but its base call goes to `0x3bd30`, which installs `0x4d4418` -- the Struct table -- while the live ctor's base call goes to `0x3a5f0` and installs `0x4d4570`, the State table.** The block each body clears separates the two readings: the copy carries the +0x80 block, the sibling at ``0x03c3d0`` -- which installs the State table itself -- carries the +0x80 block, the live State ctor carries the +0x80 block, and the live Class ctor carries 25 stores. A Class ctor that clears the block its State base clears, with the State table install dropped, is the State ctor inlined into it, and then the base call can only go to the class below State.

**Second, nothing references it: the copy has 0 outside edges and its base has 2, and both of those base edges come from inside the same unreachable set** (`0x03c3f2` in the State copy, `0x03c45c` in the Class copy). So the reference graph is closed under the family: no call, no jump and no dword in any section enters it. It is therefore not a copy the linker kept *for* a reference -- nothing can call it. Whether it survived as an unswept body or lost a COMDAT pick is not decidable from the image; that it is unreachable is.
<!-- /generated:native-unreachable -->

## What the copy is not

It is **not** a copy kept for a reference. Its only inbound edges are the two call sites on its base
ctor, and both sit inside the unreachable set; no table slot anywhere names either body. It is also
**not** the registration ctor with a different argument list: it clears a block of fields the
registration ctor never touches, and it skips the table install that the State ctor would perform.

## What the copy is

A second compilation of the same source constructors, with `UState`'s ctor inlined into `UClass`'s.
Inlining a base ctor drops the base's table install as a dead store and hoists the base's own base
call, so an inlined `UClass` ctor calls `UStruct`'s ctor directly and carries the six stores that
clear `+0x80..+0x94` -- the only work `UState`'s ctor does outside its table install. `0x0003c3d0`
is the same source before that inlining: it installs the State table itself and carries the same six
stores. The live `UClass` ctor carries neither, because it calls `UState`'s ctor out of line.

The two bodies do **not** share a signature, though: `0x0003af60` ends `ret 0x34` (thirteen arguments)
and `0x0003c430` ends `ret 0x3c` (fifteen), and `ret n` is the function's own parameter list, so no
inlining decision can move it. The pair is therefore two *overloads* of the class-creating constructor
whose member-init stores happen to agree, not one body compiled twice (`tools/class-ctor-base.py`).

## The base call is the last call, and the dead family is the top of an overload ladder

`inventory/class_constructors.csv` records `call_before_store`: the **first** `E8` before the table
store. Every reading since has taken that column for the base-constructor call, and for a body that
calls anything else first it is not. `tools/class-ctor-base.py` decodes the body with objdump, takes the
**last** direct call before the store, and resolves that callee through the same census to the class
table it installs, so an inheritance edge is a measured table rather than an argument from call order.

The reading that changes: `0x0003bd30` (six arguments, installs `UStruct`'s table) first calls
`0x0003b440` -- `Core_UnClass`, the factory that allocates a 0x520-byte object and calls the
*registration* `UClass` ctor with the literals `Class` and `System`. That is not a base constructor.
The last call before its store is `0x00073810` (`Core_UnObj`, five arguments), which installs the
object-root table `0x004dd2a8`, so the dead family is a complete chain:

    object root (5 args, 0x00073810) -> Struct (6, 0x0003bd30) -> State (6, 0x0003c3d0) -> Class (15, 0x0003c430)

and the live chain for the same four tables uses different bodies with different signatures: the object
root's three-instruction default ctor `0x00062ce0` (0 args, 366 of the image's 522 constructors call it,
so it is the base of nearly every class in the image) or the four-argument `0x00062470`, then `Struct` 0/4 args (`0x0003d560`, the registration
ctor, and `0x0003a1b0`), `UState` 5 (`0x0003a5f0`) and `UClass` 13 (`0x0003af60`, the 493 registration
sites). So the dead family is **neither** a duplicate object layer -- every one of the four tables has
its live installer, and the class hierarchy appears once -- **nor** the live bodies recompiled: each of
its four bodies is the highest-argument overload of its class, and the chain is dead from the top
because `0x0003c430` has no caller in any section. Its object-root ctor is not exclusively the family's:
`0x00073810` also has a call site at `0x00038cfb`, inside no mapped function, which is why the root
survived a sweep that took the three bodies above it. `0x00062470` is classified a *destructor* by
`tools/class-ctors.py` because it installs its table before any call and does not return `this`, yet
`UState`'s ctor calls it first and it links the object it builds into the global list at `0x006c2104`,
so its edge is reported as `base-installer` rather than dropped.

The object root's table `0x004dd2a8` is named `Object` in the block below, and that name is a join against
the parsed hierarchy rather than a write order: 25 registrations resolve to that table through one shared
constructor thunk, so `tools/class-table-names.py` takes the literal that is an ancestor-or-self of every
other literal in the group, and exactly one of the 25 is the root of `inventory/classes.csv`
(`tools/class-registrations.py`, *Which registration names a table, and the 25 names of the object root*
below).

<!-- generated:class-ctor-base -->
| class | ctor | args | recorded call | measured base | base class | verdict |
|---|---|---|---|---|---|---|
| Struct | 0x0003a1b0 | 4 | 0x00062ce0 (Object) | 0x00062ce0 | Object | base-ctor |
| Struct | 0x0003bd30 | 24 | 0x0003b440 | 0x00073810 | Object | base-ctor |
| Struct | 0x0003d560 | 0 | 0x00062ce0 (Object) | 0x00062ce0 | Object | base-ctor |
| State | 0x0003a5f0 | 20 | 0x00062470 (Object) | 0x00062470 | Object | base-installer |
| State | 0x0003d6a0 | 0 | 0x00062ce0 (Object) | 0x00062ce0 | Object | base-ctor |
| Class | 0x0003ad20 | 0 | 0x00062ce0 (Object) | 0x00062ce0 | Object | base-ctor |
| Class | 0x0003ae30 | 4 | 0x0003a1b0 (Struct) | 0x0003a1b0 | Struct | base-ctor |
| Class | 0x0003af60 | 52 | 0x0003a5f0 (State) | 0x0003a5f0 | State | base-ctor |
| Object | 0x00062ce0 | 0 | - | - | - | no-base-call |
| Object | 0x00063a60 | 4 | - | - | - | no-base-call |
| Object | 0x00073810 | 20 | - | - | - | no-base-call |

522 constructors in the image: 509 read a base class off the last call before their table store (1 of those to a body the shape rule calls a destructor), 9 install the table before any call (the base was inlined), 4 end in a body that installs no class table, and 6 of the recorded calls are not the last call and so are not the base the census implied.

**A body can install more than one class table, and the last store is the body's own.** 8 of the 522 rows sit in a body that stores two or more tables (4 distinct bodies). The first store is the table of a base whose constructor the compiler inlined into the derived body; the last store is the table of the class the body builds. So a row whose `table_va` differs from its body's last store (`own` = `no`) records a base class's vtable: **4 row(s)**, and those are the rows the site census cannot name from.

| body | name | stores in body order | own table | own table class | row whose table is a base |
|---|---|---|---|---|---|
| 0x00110120 | FUN_00110120 | 0x004d03c8 -> 0x004fb9b8 | 0x004fb9b8 | PackageMapLevel | - |
| 0x002e81f0 | ctor_FireTexture | 0x004cfc88 -> 0x004cfd78 | 0x004cfd78 | FireTexture | FractalTexture |
| 0x002e8260 | FUN_002e8260 | 0x004cfc88 -> 0x004cfe68 | 0x004cfe68 | WaterTexture | FractalTexture |
| 0x002e8370 | ctor_IceTexture | 0x004cfc88 -> 0x004d0228 | 0x004d0228 | IceTexture | FractalTexture |

**The registration side agrees on every site it can join.** 491 of the 493 sites record the last store of the body their thunk resolves to; 2 cannot be joined because the census has no constructor row for the body their thunk resolves to (`TextBuffer`, `HTTPDownload`).
<!-- /generated:class-ctor-base -->

## A constructor body installs two tables, and `0x004cfc88` is neither texture class's own

`tools/class-ctor-base.py` also counts the class tables each body stores (`stores`, `own_table`,
`own_table_class`, `own`). **4 of the 518 constructor bodies store two**, and in all four the first store is
a base class's table and the last is the table of the class the body builds -- MSVC writes the base
constructor's vtable itself when it inlines that constructor into the derived body:

| body | class it builds | stores in body order | own table |
|---|---|---|---|
| `0x00110120` | `PackageMapLevel` | `0x004d03c8` -> `0x004fb9b8` | `0x004fb9b8` |
| `0x002e81f0` | `FireTexture` | `0x004cfc88` -> `0x004cfd78` | `0x004cfd78` |
| `0x002e8260` | `WaterTexture` | `0x004cfc88` -> `0x004cfe68` | `0x004cfe68` |
| `0x002e8370` | `IceTexture` | `0x004cfc88` -> `0x004d0228` | `0x004d0228` |

**The table both texture constructors store is a base's, so the derived name above is not the contradiction
it looked like; it is the only reading the bytes allow.** The body that owns `0x004cfc88` outright is
`0x002e81b0`, whose base call is Texture's constructor and whose single store is `0x004cfc88`; each derived
body inherits that store through the inlined base constructor and then writes its own table over it.
`0x002e8260` is WaterTexture's constructor -- it is the base call of WaveTexture's, FluidTexture's and
WetTexture's constructors, and WaterTexture is the only direct subclass of `FractalTexture` left once
`FireTexture` and `IceTexture` are placed.

**489 of the 493 registration sites recorded the last store of the body their thunk resolves to** when
this artifact was measured, so the registration and the store rule agree wherever a class has a table of its
own (PackageMapLevel's site records `0x004fb9b8`, its constructor's last store). The 4 that did not split:
2 sites resolve to a body the constructor census has no row for (`TextBuffer`, `HTTPDownload`), and 2
recorded a table their own constructor does not build -- `FireTexture` at `0x002e8468` and `IceTexture` at
`0x002e8768`, both recording the base's `0x004cfc88` because the site census still read each body's *first*
store. `tools/class-registrations.py` reads the last store now (the section below), so those two sites
record `0x004cfd78` and `0x004d0228`, and every site whose body this artifact has a row for joins it --
**491 of 493**, since the 2 that do not are the bodies it has no row for. The earlier claim that the two
tables were named by no registration at all was that reader's defect, not the image's.

## Which registration names a table, and the 25 names of the object root

`inventory/xbe_class_table_names.csv` is keyed by **table**: it follows registration -> class literal ->
constructor thunk -> constructor -> `mov [this], table`, and when several sites resolve to one table the
writer keeps the last and drops the rest silently. `tools/class-registrations.py` keeps every site, and
the difference is the object root.

**25 of the image's 493 registration sites pass the same constructor thunk `0x00034650`**, which is the
guarded stub `mov ecx,[esp+4]; test ecx,ecx; je; jmp 0x00062ce0`: a class with no native constructor of
its own is registered with that shared default, so its registration names the *root's* table and not its
own. The 25 literals are `Object`, `Field`, `Property`, `Struct`'s siblings and the like -- `Factory`,
`Exporter`, `Engine`, `Player`, `NetDriver`, `Material`, `RenderDevice`, ... -- and the per-table artifact
showed whichever site happened to be written last, `VertexStreamBase` at `0x001eb186` -- a literal no
package in the dump declares, chosen because its site sorts after `0x00064af6`. One other table carries
the same shape (`0x004cfba0`: `Texture`, `FractalTexture`, `WaterTexture`), so **28 of 493 registrations sit
on a table more than one site resolves to** and cannot name the table they are attached to. The two texture
sites that used to join them name their own tables now, which is the second reading described below.

<!-- generated:class-registrations -->
| table | committed name | sites | class literals the registrations push |
|---|---|---|---|
| 0x004dd2a8 | Object | 25 | Exporter (`0x000349e6`), Factory (`0x00036066`), Field (`0x0003b276`), Object (`0x00064af6`), Subsystem (`0x00066b16`), Language (`0x00066d96`), Property (`0x000778d9`), VoiceSubsystem (`0x000e2426`), Viewport (`0x000f92c9`), CameraEffect (`0x000fbd46`), Client (`0x000fd969`), Channel (`0x001083c6`), NetConnection (`0x0010d6a9`), Download (`0x0011e9d9`), RenderDevice (`0x0011f9f9`), Engine (`0x00121989`), LevelBase (`0x0015c989`), Material (`0x00169ad6`), BitmapMaterial (`0x00169ca6`), RenderedMaterial (`0x00169d66`), NetDriver (`0x00183599`), PendingLevel (`0x001a51d9`), Player (`0x001bddc6`), RenderResource (`0x001eafb6`), VertexStreamBase (`0x001eb186`) |
| 0x004cfba0 | Texture | 3 | Texture (`0x0027e889`), FractalTexture (`0x002e1489`), WaterTexture (`0x002e17d9`) |

| class literal | size | site | table |
|---|---|---|---|
| Object | 40 | 0x00064af6 | 0x004dd2a8 |
| Subsystem | 44 | 0x00066b16 | 0x004dd2a8 |
| Language | 44 | 0x00066d96 | 0x004dd2a8 |
| VoiceSubsystem | 44 | 0x000e2426 | 0x004dd2a8 |
| RenderResource | 44 | 0x001eafb6 | 0x004dd2a8 |
| Field | 52 | 0x0003b276 | 0x004dd2a8 |
| VertexStreamBase | 56 | 0x001eb186 | 0x004dd2a8 |
| CameraEffect | 60 | 0x000fbd46 | 0x004dd2a8 |
| Material | 60 | 0x00169ad6 | 0x004dd2a8 |
| Exporter | 64 | 0x000349e6 | 0x004dd2a8 |
| RenderedMaterial | 64 | 0x00169d66 | 0x004dd2a8 |
| BitmapMaterial | 88 | 0x00169ca6 | 0x004dd2a8 |
| Channel | 100 | 0x001083c6 | 0x004dd2a8 |
| Factory | 104 | 0x00036066 | 0x004dd2a8 |
| Property | 108 | 0x000778d9 | 0x004dd2a8 |
| Player | 108 | 0x001bddc6 | 0x004dd2a8 |
| LevelBase | 148 | 0x0015c989 | 0x004dd2a8 |
| PendingLevel | 176 | 0x001a51d9 | 0x004dd2a8 |
| RenderDevice | 184 | 0x0011f9f9 | 0x004dd2a8 |
| Client | 196 | 0x000fd969 | 0x004dd2a8 |
| Texture | 204 | 0x0027e889 | 0x004cfba0 |
| FractalTexture | 228 | 0x002e1489 | 0x004cfba0 |
| Engine | 284 | 0x00121989 | 0x004dd2a8 |
| Viewport | 456 | 0x000f92c9 | 0x004dd2a8 |
| Download | 1112 | 0x0011e9d9 | 0x004dd2a8 |
| NetDriver | 4524 | 0x00183599 | 0x004dd2a8 |
| WaterTexture | 4868 | 0x002e17d9 | 0x004cfba0 |
| NetConnection | 20368 | 0x0010d6a9 | 0x004dd2a8 |

The 28 sites that share a constructor thunk still register **22 distinct sizes**, 40 to 20368 bytes: the thunk cannot name the class, but the second argument separates them.

Every site is keyed to the table its constructor body stores **last**: symbol-bounded 491, padding-bounded 2. 1 site(s) follow a guarded stub's `call` one hop (0x00075820 -> 0x000669e0).

493 registration sites: 493 resolve to a class table, 28 sit on a table that more than one site resolves to, and 0 resolve to no table at all. 28 sites therefore do not name the table they are attached to.
<!-- /generated:class-registrations -->

## The table name is a hierarchy join, and the site census says when it cannot be one

`tools/class-table-names.py` now names a table from its **whole** site list, and it never reads an address
order to break a tie. Three rules, and the `name_rule` column says which one produced each row:

- `unshared-thunk` (463 of the 466 named tables): exactly one site on the table uses a constructor thunk no
  other site uses. That thunk's constructor installs the table for this class and no other, so the literal
  at that site is the class.
- `hierarchy-ancestor` (2 tables): every site shares a thunk, so no site owns the table. Among the literals
  `inventory/classes.csv` knows, exactly one is an ancestor-or-self of every other in the group, and that is
  the class whose constructor the rest reuse. This names `0x004dd2a8` `Object` (the only class in the dump
  with no super) and `0x004cfba0` `Texture`.
- `ancestor-derived` (0 tables now, 1 before): the shared group names no common ancestor itself, but it has
  exactly one nearest common ancestor, and the row says the name is derived from it. It named `0x004cfc88`
  `FractalTexture` while the reader still took each constructor's **first** store; with the body's own
  store read, both texture sites name their own tables and this row is carried by the rule below, from
  stronger evidence.
- `base-store-ancestor` (1 table): no registration names the table at all, but two or more registered
  classes store it *first* in their own constructor bodies, and the nearest common ancestor of those
  owners is the class whose constructor they inlined. `0x004cfc88` keeps `FractalTexture` this way:
  FireTexture and IceTexture both write it before their own tables, and FractalTexture is the one class
  between them and `Texture`. One owner is not evidence -- a class's nearest ancestor is itself -- so a
  single owner leaves the table unnamed.

**The address orders are refuted, and that is why the rule is a join.** The lowest site on the root's table
is `Exporter` (`0x000349e6`) and the last is `VertexStreamBase` (`0x001eb186`); neither is the class the
hierarchy puts there, and `VertexStreamBase` is not a class in `inventory/classes.csv` at all. Exactly three
of the 492 rows change class, and the `object_size` column follows the site the rule chose, so it moves on
the same three (4868 -> 204 on `0x004cfba0`, 296 -> 1304 on `0x004cfc88`, 56 -> 40 on `0x004dd2a8`). That
size is the chosen site's push, not a second measurement; the runtime read agrees with one of the three
names -- `inventory/live_class_tables.csv` carries **454 objects of class `Texture`** on `0x004cfba0`
(`docs/UNKNOWNS.md` U8).

**The table a constructor installs is its last store, and three rows move because of it.** The earlier
reader took the first `mov [reg], <table>` in the body, and MSVC writes a base constructor's vtable itself
when it inlines that constructor into a derived body: `0x002e81f0` (FireTexture's) stores `0x004cfc88` and
then `0x004cfd78`, and `0x002e8370` (IceTexture's) stores `0x004cfc88` and then `0x004d0228`. Reading the
last store names `0x004cfd78` **FireTexture** (`0x002e8468`) and `0x004d0228` **IceTexture**
(`0x002e8768`), each from a constructor thunk no other site uses, and leaves `0x004cfc88` to the base-store
rule above -- the same name, from the store order instead of a nearest common ancestor. The artifact gains
two names (466 -> 468) and loses two `no-registration` rows (26 -> 24); the multi-site tally falls from 3
tables to 2.

**The read is bounded by the body, and here the bound moves a row.** A flat 400-byte read reaches past a
short constructor's own `ret` into the next function, whose store is then attributed to the wrong body. The
read stops at the size `symbols/xbox_functions.csv` gives the body and, where the map has no size, at the
body's own `int3` padding: 465 of the 467 rows a site resolves are symbol-bounded and 2 are padding-bounded,
and one of those is the row that changes. TextBuffer's target `0x00075820` is a 19-byte guarded stub, and
its flat window reads two stores that belong to the body after it. **A guarded stub can forward with `call`
rather than `jmp`**: `0x00075820` is `mov ecx,[esp+4]; test ecx,ecx; je; push L""; call 0x000669e0; ret`,
and `0x000669e0` -- which `tools/class-ctors.py` also classifies as the constructor, by shape -- installs
`0x004ddf78` at `0x00066a14`. The read follows that one call and no further, which moves TextBuffer's site
from the window's `0x0007588d` store to the real one at `0x00066a14`.

<!-- generated:class-table-names -->
| table | name | rule | sites | literals |
|---|---|---|---|---|
| 0x004dd2a8 | Object | hierarchy-ancestor | 25 | Exporter, Factory, Field, Object, Subsystem, Language, Property, VoiceSubsystem, Viewport, CameraEffect, Client, Channel, NetConnection, Download, RenderDevice, Engine, LevelBase, Material, BitmapMaterial, RenderedMaterial, NetDriver, PendingLevel, Player, RenderResource, VertexStreamBase |
| 0x004cfba0 | Texture | hierarchy-ancestor | 3 | Texture, FractalTexture, WaterTexture |

**A table is named from its constructor body's last store.** 3 table(s) are named from a body that installs two class tables -- the first store there is a base class's table, written by the base constructor the compiler inlined -- and 2 row(s) are read without a symbol size, bounded at the body's own `int3` padding: `0x004ddf78` (TextBuffer), `0x0052cb00` (HTTPDownload).

492 tables: 478 named (465 by an unshared constructor thunk, 2 by the hierarchy a shared thunk forces, 0 derived from a nearest common ancestor, 1 from a base store no registration names, 6 from the tree the child supers describe, 4 from the assert file their slots inline), 0 unresolved, 14 with no registration. 2 tables are reached by more than one registration site, so a write-order rule would name them by whichever site happened to be written last.
<!-- /generated:class-table-names -->

## The site census reads the body's last store, and 2 of 493 rows move

`tools/class-registrations.py` keys each site to the table its constructor body stores **last** -- the rule
`tools/class-table-names.py` names tables with -- so this page no longer carries two readings of the same
bytes. Each row gained `installed_at` (the store's own address), `stores` (how many class tables the body
stores), `bound` (`symbol`, `padding` or `window`) and `stub_va` (the `call` a guarded stub forwarded
through), so the store a row was read from is on the row.

**2 of the 493 rows move, and they are the texture pair.** FireTexture's site `0x002e8468` records
`0x004cfd78` and IceTexture's `0x002e8768` records `0x004d0228`, each read from a body that stores two
tables (`0x002e81f0` at `0x002e8220`, `0x002e8370` at `0x002e83be`); read at the first store both recorded
the base's `0x004cfc88`. No other row moves: the `object_size` column is unchanged on all 493, because it
is the site's own second-to-last push and not the store read. Tables with more than one site fall from
**3 to 2** and the sites on them from **30 to 28**, which is the count
`inventory/xbe_class_table_names.csv` has carried since #533; the distinct sizes those sites register fall
from 24 to 22 for the same reason.

**The read is bounded by the body, and 2 rows are the bound.** 491 of the 493 reads stop at the size
`symbols/xbox_functions.csv` gives the body and 2 at the body's own `int3` padding -- `TextBuffer`'s
`0x00075820` and `HTTPDownload`'s `0x002c2650`, the same two bodies the constructor census has no row for.
TextBuffer's is a guarded stub that installs nothing and forwards with `call`, and the census follows it one
hop: `0x00075820` -> `0x000669e0`, which stores `0x004ddf78` at `0x00066a14` (the row's `stub_va`).
HTTPDownload's body single-stores `0x0052cb00` at `0x002c2684`; no hop is needed there.

**The join to the constructor census closes.** Per site, `installed_at`, `stores` and `table_va` equal
`inventory/class_ctor_bases.csv`'s `function_va + store_off`, `stores` and `own_table` on **491 of 493**
rows; the 2 that cannot be joined are exactly the two bodies that artifact has no row for, and both were
read here instead rather than left as holes.

## The object size has one source, and three of the columns are copies of it

`object_size` reads like four measurements -- the registration census, the naming census, the constructor
census and the base-call census all carry a column of that name -- and the plan proposed joining them as an
independent check on the registration's second argument. Joined on the table, they are **one read**: the
site's second-to-last push, the argument position. `inventory/xbe_class_table_names.csv` reaches the same
value from its chosen site's last immediate push on all 468 tables that have a site, and
`tools/class-ctors.py` and `tools/class-ctor-base.py` copy the column off the table row
(`trow["object_size"]`) rather than measuring it, so their 986 and 504 rows agree by construction.
`inventory/class_records.csv` carries the class **script** record's byte length and no object size at all;
it is smaller than the object on 266 of the 333 classes that have both.

<!-- generated:class-size-sources -->
| source | what the value is | rows that carry it | equals the site's push |
|---|---|---|---|
| `inventory/class_registrations.csv` | the site's second-to-last push (the argument position) | 472 sites | -- this is the read |
| `inventory/xbe_class_table_names.csv` | its chosen site's last immediate push | 472 tables (468 whose site resolves to them, 4 whose site is their class's) | 472 of 472 |
| `inventory/class_constructors.csv` | copied from the table row | 986 rows | 986 of 986 |
| `inventory/class_ctor_bases.csv` | copied from the constructor row | 504 rows | 504 of 504 |
| `inventory/class_records.csv` | the class **script** record's byte length | 333 of 468 classes | not an object size (smaller than it on 266 of 333) |

**The object size has one source, and three of those columns are copies of it.** 468 of the 492 tables have a registration site that resolves to them; their sizes are **202 distinct values, 40 to 348696 bytes**, and the naming census carries the same value on all of them. The 2 constructor-side artifacts copy the column rather than measuring it, so a disagreement between two of the four columns can only come from the site-to-table attribution upstream -- which is what the object root's 40 against the old reader's 56 was.

**4 more carry their class's own site, and that site installs another table.** These are the `slot-file-token` rows: the class's literal and the size it pushes are the site's, but the store the site reaches is the object root's, so the size is the class's own measurement while the table it names comes from the ladder (`0x004c93d0` 456, `0x004d0318` 4524, `0x004d03c8` 20368, `0x004fa390` 100).

20 of the 492 tables have **no size at all** -- no registration site resolves to them and no class site names them, so nothing in this tree measures them.
<!-- /generated:class-size-sources -->

**What a disagreement between two of the columns can mean, then, is an attribution defect upstream, not a
second measurement.** The object root's 40 against the old reader's 56 was exactly that: the first-store
rule attached `VertexStreamBase`'s site (`0x001eb186`, which pushes 56) to the root's table. Each site's
push is its own class's size, which is why the 25 sites sharing the root's constructor thunk register 25
distinct sizes. A genuinely independent size would have to come from outside these artifacts -- a runtime
read of the class's `PropertiesSize`, or a `new` site that pushes it -- and nothing in this tree measures
the value twice.

## The 24 tables no registration reaches

The site census resolves 468 of the 492 tables to a registration site, and the size join above closes on
the other 24 as "nothing in this tree measures them". That is true of the *registration* path, not of the
image. The same `C7 /0` store sweep `tools/class-ctors.py` runs finds a body installing every one of the
24, and those bodies are not isolated: their callers are class constructors, which is the base-class
shape. The 24 are the tables the registration literals never name, built by the derived classes whose
own tables the census does name.

Every store site is a row, and the verdict names the kinds of edge into the body it sits in: `ctor` /
`dtor` (the caller is a constructor or destructor out of `inventory/class_constructors.csv`), `installer`
(a table-installing body the shape rule could not classify), `wrapper` (a forwarder whose first call
targets the body -- the two forms this image carries for every destructor), `code` (a call from an
ordinary body), `unmapped` (a call from code the symbol map does not cover) and `stub` (a tail jump). The
`proposed class` column is not this tool's measurement: it is `inventory/class_table_parents.csv`'s
caller-rule answer for the same table, joined in so the two readings sit side by side.

<!-- generated:class-orphan-tables -->
| table | slots | proposed class | store sites | installing bodies | registration thunk | callers | verdict | named classes whose constructor calls it |
|---|---|---|---|---|---|---|---|---|
| `0x004c78c0` | 31 | -- | 1 | 1 | no | 17 | dtor+wrapper+stub | NetDriver, AudioSubsystem, table:0x004d2400, new +3 |
| `0x004c7958` | 46 | Material | 2 | 2 | no | 40 | ctor, dtor+wrapper+stub | RenderedMaterial, Modifier, Combiner, MultiPassMaterial +32 |
| `0x004c7a10` | 46 | RenderedMaterial | 1 | 1 | no | 17 | dtor+wrapper+stub | BitmapMaterial, ConstantMaterial, TexCoordMaterial, VertexColor +5 |
| `0x004c7ac8` | 48 | BitmapMaterial | 1 | 1 | no | 10 | dtor+wrapper+stub | Texture, ProxyBitmapMaterial, ScriptedTexture, ShadowBitmapMaterial |
| `0x004c7c78` | 32 | CameraEffect | 1 | 1 | no | 16 | dtor+wrapper+stub | SGScreenSniperOverlay, SGScreenMenuPause, SGScreenBlur, SGScreenDOF +6 |
| `0x004c9348` | 31 | -- | 2 | 2 | no | 11 | ctor, dtor+wrapper+stub | Viewport, NetConnection |
| `0x004c94b0` | 43 | -- | 2 | 2 | no | 4 | ctor, dtor+wrapper+stub | XboxClient |
| `0x004cfe68` | 59 | WaterTexture | 2 | 2 | no | 8 | ctor, dtor+code+stub | WaveTexture, FluidTexture, WetTexture |
| `0x004d1468` | 35 | -- | 4 | 2 | no | 28 | ctor, dtor+wrapper+stub, no-body | table:0x004d17a8, Struct, Const, Enum +13 |
| `0x004d17a8` | 53 | -- | 3 | 3 | no | 36 | ctor, dtor+wrapper+stub | IntProperty, BoolProperty, FloatProperty, StrProperty +9 |
| `0x004d2400` | 37 | -- | 1 | 1 | no | 3 | dtor+wrapper+stub | XHVVoiceSubsystem |
| `0x004d34d8` | 32 | -- | 2 | 2 | no | 3 | dtor+wrapper+stub, no-caller | ObjectExporterT3D |
| `0x004d3af0` | 33 | -- | 2 | 2 | no | 4 | ctor, dtor+wrapper+stub | TextBufferFactory |
| `0x004eaea8` | 47 | -- | 1 | 1 | no | 6 | dtor+wrapper+stub | NullRenderDevice, D3DRenderDevice |
| `0x004fc4f0` | 30 | -- | 3 | 3 | no | 13 | ctor, dtor+wrapper+stub | table:0x004fc588, Level, DemoPlayPendingLevel, NetPendingLevel |
| `0x004fc588` | 34 | -- | 2 | 2 | no | 8 | ctor, dtor+wrapper+stub | DemoPlayPendingLevel, NetPendingLevel |
| `0x004fcfe0` | 36 | -- | 1 | 1 | no | 6 | dtor+wrapper+stub | ChannelDownload, HTTPDownload |
| `0x005040d0` | 56 | Engine | 2 | 2 | no | 5 | ctor, dtor+wrapper+stub | GameEngine |
| `0x00517860` | 30 | -- | 1 | 1 | no | 7 | dtor+wrapper+stub | table:0x005178d8, IndexBuffer, SkinVertexBuffer |
| `0x005178d8` | 32 | -- | 1 | 1 | no | 11 | dtor+wrapper+stub | VertexBuffer, VertexStreamVECTOR, VertexStreamCOLOR, VertexStreamUV +1 |

**20 tables, 35 store sites in 33 bodies, 2 of the sites in no symbol-map body.** None of the bodies that install one of these tables is a registration thunk target, and one body stores 1 table more than once.

**6 of the 20 tables carry a name from the caller rule**, the join in the `proposed class` column and not this sweep's measurement; the rest are that tool's `constrained` verdict (a caller set whose common ancestor is not one class), its name-clash case, or a table with no named caller at all.

**32 of the 33 installing bodies are reached, and the shape is a base class, not isolated code: 12 are called from a class constructor (10 of the 20 tables have at least one) and 20 from a class destructor.** 98 named classes call an installer body from their own constructor. The other edges are 19 forwarder wrappers (a body whose first call targets the installer), 73 tail jumps of which 72 come from code the symbol map does not cover, and 0 calls from no map body at all; 1 bodies have no inbound edge.
<!-- /generated:class-orphan-tables -->

## The 246 tables no registration reaches: 37 a constructor bounds, 205 only unnamed bodies store

`docs/UNKNOWNS.md` U40 counts 279 gap bodies a class-table slot names and 264 of them in tables nothing
registers. The full population is **246**: the 232 tables `inventory/native_vtable_shared.csv` pairs with
a mapped table by prefix, plus the 14 mapped tables the class map leaves proposed. The installer's own
call site is the route the entry names, and `tools/class-table-installers.py` walks it over every store
`inventory/native_vtable_reach.csv` already censused.

**A constructor's store bounds a table; it does not name one.** A constructor installs its parent's table
before its own (this page's `base-store-ancestor` rule), so a table a single `ctor_FireTexture` stores is
one of `FireTexture`'s **ancestors'** tables -- the table is at least as deep as that class and never the
class itself. 37 tables carry that bound; the other 205 are stored only by bodies no class names, and 4 by
no store at all. The symbol map's `size` column is a **byte count in decimal**, and this census read it as hex
until 2026-11-20: a short body's span then ran past its own end and the store of the body next door was
credited to it, which is where the 39/209/2 this section used to quote came from. Read at the right base the
same rule moves 12 rows' installer sets and three verdicts, and every mangled C++ name the evidence carried
(the UIX screen constructors, `Core_UnObjBas`, `dtor_Movie`) disappears -- none of the 246 is installed by a
body with a C++ symbol. So the U40 route answers the *reachability* half of its question and not the naming half,
which is why no name is written from it: the six names the tree can carry are already applied
(`name_rule` `tree-paired`).

<!-- generated:class-table-installers -->
| the tables no name reaches | tables |
| --- | --- |
| stored by a class constructor (so an ancestor of that class) | 37 |
| stored only by bodies no class names | 205 |
| stored by nothing | 4 |
| **total** | **246** |

The constructor-stored tables carry a bound, not a name: 24 distinct classes, and a table a constructor stores is its base's, never its own. 0 of the population share a prefix with a mapped table (``), which is the family the shared census already recorded.

| class whose constructor stores it | tables it bounds |
| --- | --- |
| `SkeletalMesh` | 5 |
| `TextBuffer` | 2 |
| `LinkerLoad` | 2 |
| `XboxViewport` | 2 |
| `PackageMapLevel` | 2 |
| `TcpipConnection` | 2 |
| `TcpipConnectionXbox` | 2 |
| `XHVVoiceSubsystem` | 2 |
| `DemoRecConnection` | 2 |
| `StaticMesh` | 2 |
| `TcpNetDriver` | 1 |
| `TcpNetDriverXbox` | 1 |
| `LinkerSave` | 1 |
| `LinkerSaveGame` | 1 |
| `LinkerLoadGame` | 1 |
| `NullRenderDevice` | 1 |
| `ControlChannel` | 1 |
| `DemoPlayPendingLevel` | 1 |
| `DemoRecDriver` | 1 |
| `Engine` | 1 |
| `Input` | 1 |
| `Level` | 1 |
| `NetPendingLevel` | 1 |
| `D3DRenderDevice` | 1 |
<!-- /generated:class-table-installers -->

## Where the 246's stores write: a class table is `[reg]`, and 36 of the 37 are a member's table

`tools/class-table-installers.py` calls every `mov [reg(+disp)], <imm32>` an install, which is what the
store is and not what it writes: the previous section's populations are populations of *stores*, and the
question behind them is whether the table is the class's own dispatch table or something else the object
holds. `tools/class-table-store-fields.py` decodes the operand instead -- base register, displacement,
and whether the form is a register slot, a member field, a stack slot, a global or a SIB with a scaled
index -- over all 724 tables `inventory/native_vtable_reach.csv` censuses, and joins a member store to the
class whose constructor writes it.

<!-- generated:class-table-store-fields -->
| population | tables | object slot | slot and member | member only | frame only | global only | no `C7` store |
|---|---|---|---|---|---|---|---|
| named by the registration path (control) | 478 | 478 | -- | -- | -- | -- | -- |
| nameless: a class ctor stores it | 37 | 1 | 9 | 27 | -- | -- | -- |
| nameless: only bodies no class names store it | 209 | 173 | 2 | 11 | 20 | 3 | -- |

**The 478 named tables are the control: 478 of them are written into the object's own slot and none of them into a member.** A class's dispatch table is the pointer its installer writes at `[reg]`. Of the 246 tables no registration names, **185 are written into an object slot at least once, 49 into a member field, and 23 have no object-field store of their own at all** -- 20 of those only into a stack slot, where the table's address is passed or spilled rather than written into a field and 3 only into a global variable, where the object is a singleton in `.data`.

**The member offsets are a class's own layout, not a shared constant**: `0x8` in 2 tables, `0x28` in 24 tables, `0x2c` in 8 tables, `0x38` in 1 table, `0x64` in 2 tables, `0xb8` in 6 tables, `0xc0` in 1 table, `0xd8` in 1 table, `0xf0` in 1 table, `0x108` in 1 table, `0x134` in 1 table, `0x14c` in 2 tables, `0x164` in 1 table, `0x17c` in 1 table, `0x194` in 1 table.** A field that holds a table pointer is a member of the class whose constructor stores it, so the table is that member's dispatch table and its slots are methods of the member, not of the class -- naming it `<Class>_vt<slot>` would claim a class slot the image does not have.

**38 of the 49 member tables have a class whose own constructor stores the field** (SkeletalMesh (5), ControlChannel (2), TextBuffer (2), XboxViewport (2), PackageMapLevel (2), TcpipConnection (2)), so the member's owner is known even though the table's class is not: these are `<Class>`'s field, and the name the map lacks is the member's, not a class's.

**What this settles for U40.** 36 of the 37 tables a class constructor stores are member tables (27 only a member, 9 the object slot as well), so the `at-least-<base>` bound those tables carry is a bound on the *owner* of a member, not on a class's dispatch table; the reading that a ctor's table is its own or its base's holds only for the 1 that write `[reg]` at all. And 173 of the 209 plain-only tables are written into the object slot by a body no class names, which is the shape of a constructor the map has not named rather than of a table nothing installs.
<!-- /generated:class-table-store-fields -->

Three readings follow, and each corrects the section above rather than restating it:

- **The `at-least` bound is a bound on a member's owner.** A constructor that writes a table into a field
  is not installing a class table at all: `ctor_ControlChannel` writes `0x004fa5b0` at `[esi]` -- its own
  dispatch table -- and then `0x004c7854` and `0x004fa5a8` at `[esi+0x64]`, a member that holds a two-slot
  table. The `base-store-ancestor` rule says which of a constructor's `[reg]` stores is its own; it says
  nothing about the field stores, so for the 36 bounded tables what is bounded is the class that owns the
  member, not a class whose dispatch table this is.
- **A member table's slots are not class slots.** `0x004fa5a8`'s two slots are `FUN_0010a2e0` (250 bytes)
  and `0x00020f00` (a one-byte body), and `0x004c7854`'s are `__purecall` and that same one-byte body: a
  member's table, so a `<Class>_vt<slot>` name would claim a slot in a class table that does not hold
  them. The 38 member tables whose owner's constructor is known name their slots' *owner* once the member
  is named, which is a naming pass of its own, one class at a time.
- **`install` counts a stack slot.** Twenty of the 246 are written only at `[esp+disp]`/`[ebp+disp]`, where
  the address is spilled or passed and nothing owns it, so `tools/native-vtable.py`'s `installs` column is
  an upper bound on installations and the previous section's "2 stored by nothing" is 23 in this reading
  (20 stack-only, 3 with no `C7 /0` store at all). The gap between the two counts is the addressing bytes:
  `mov [reg+disp32],imm32` is ten bytes, and `tools/class-orphan-tables.py` read its immediate one byte
  short until this bundle -- which is why the twelve `mov [esi+0xb8],<index table>` stores of the
  LinkerLoad/LinkerSave family were invisible to it.

What this does not say: the partition is the form of the store, not a proof about `this` -- a `[reg+0x28]`
store whose base is an argument rather than the object would read the same. The named population is what
makes it evidence, and it is the part that cannot be argued with: 478 of the 478 class tables the
registration path names are written at `[reg]`, and not one of them only into a member.

## What a member table holds: an interface vtable, and five shapes that repeat

The store-field partition leaves **49 tables whose stores write a member field**, and the reading that
follows from the store's form -- a field that holds a table pointer holds an interface object's table --
is settled by the tables themselves rather than by the store. `tools/class-member-tables.py` reads each of
the 49 at its address and censuses the slots: every slot is a dword into `.text`, so it resolves to a row
of `symbols/xbox_functions.csv` and its body's size says whether the slot is an implementation (a body
longer than 8 bytes), a stub (`ret`, `xor eax,eax; ret`) or `__purecall`. The control is the class table:
**all 492 tables the class-map census covers -- the 478 the registration path names among them -- share one
slot-0 body**, so a table whose slot 0 is something else is not a class's dispatch table at all.

The slots are mostly empty. Of 292 slots over the 49 tables, **111 are stubs of 8 bytes or less and 24 are
`__purecall`**, so a third of every interface here is its default do-nothing implementation; 82 slots land
on bodies the function map does not cover and 198 are `FUN_` rows it does, which is why the map's coverage
of an interface method is the exception and not the rule. The 115 distinct bodies are shared hard: the
most-referenced is a 3-byte stub that fills 33 slots. The 82 slots outside the map also split the one
population U40 counts: **32 of them are gap bodies the padding rule decodes**, so 32 of U40's 269 gap
bodies in an unnamed table's slot are in these 49 member tables and the other 237 are in the plain-only
ones; the remaining 50 are bodies neither the map nor the gap rule reaches.

What repeats is more informative than what is unique. **25 of the 49 fall into 7 families** under a rule
stated once -- identical slot vectors are the same table emitted twice; one table *subsumes* another when
it implements exactly the slots the other leaves `__purecall` (an implementation ladder); and two tables
of four slots or more that agree on *more than half* their slots are one interface with per-class
overrides. The families read as interfaces embedded by several classes:

- **`0x004c7860`, 22 slots, five tables** -- the one the four Linker classes write at `[+0xb8]`. The base
  table is also written at `[reg]` by 21 bodies no class names, so it is an object's own dispatch table,
  and the four per-class tables differ from it in 8 or 10 slots. Within the family the **Load pair differ
  at exactly one slot (0) and the Save pair at exactly one slot (0)**, so the interface is one
  implementation with a single per-class entry, not four implementations.
- **`0x004fc35c`, 8 slots, six tables** -- a **four-rung implementation ladder**: the rungs carry 8, 7, 6
  and 0 `__purecall` slots, each implements exactly the slots the one below leaves abstract, and the slots
  that get implemented resolve to `6=Engine_UnPenLev` and `7=FUN_0015ce70`. `DemoPlayPendingLevel`,
  `Level` and `NetPendingLevel` own the two 0-purecall rungs at `[+0x28]`.
- **`0x004d03b8` (1 slot) and `0x004d03bc` (2 slots), four identical tables each** -- `PackageMapLevel`,
  `TcpipConnection`, `TcpipConnectionXbox` and `DemoRecConnection` write the same two tables at `[+0x2c]`
  and `[+0x28]`, so those four classes embed the same pair of one- and two-slot interface objects.

What this does not say: the *type* of the member. The owner class is known from its constructor and the
field offset is read from the store, but the interface object's class is not registered anywhere, so the
name a slot would carry is a member's, and reading one interface's slots as a class's virtual methods is
the reading the slot-0 control refuses. What the field itself can be called is now measured: the artifact
carries `declared_in` / `layout` / `member_name`, and **32 of the 49 tables have a label of the form
`<Class>_member_<field>`** -- 34 are a single owner's field, one is declared in a shared ancestor
(`Linker_member_0xb8`, written by both `LinkerLoad` and `LinkerSave`), one has owners in no common chain
(the same offset is two layouts) and 13 have no owner at all. The four rows whose store reports two field
offsets keep no label, because the store does not pair an offset with its owner. A declared *property name*
is not available for any of them: the assert-derived member map covers four classes and names **0 of the 53
owner/offset pairs** behind these tables. Nor does a slot's body size date it: a 3-byte stub in a table is
the interface's default, not an unimplemented slot.

<!-- generated:class-member-tables -->
| family | tables | slots | owners | slots the family varies |
|---|---|---|---|---|
| `0x004fc35c` | 6 | 8 | DemoPlayPendingLevel, Level, NetPendingLevel | 0, 1, 2, 3, 4, 5, 6, 7 |
| `0x004c7860` | 5 | 22 | LinkerLoad, LinkerLoadGame, LinkerSave, LinkerSaveGame | 0, 1, 4, 6, 7, 8, 9, 10, 11, 15, 16, 17 |
| `0x004d03b8` | 4 | 1 | DemoRecConnection, PackageMapLevel, TcpipConnection, TcpipConnectionXbox | -- |
| `0x004d03bc` | 4 | 2 | DemoRecConnection, PackageMapLevel, TcpipConnection, TcpipConnectionXbox | -- |
| `0x004c7854` | 2 | 2 | ControlChannel, TextBuffer | -- |
| `0x004c93c8` | 2 | 2 | XboxViewport | -- |
| `0x004d0314` | 2 | 1 | TcpNetDriver | -- |

**32 of the 49 tables carry a label of the form `<Class>_member_<field>`, and the class is the one that declares the field.** 34 are a single owner's field, 1 are declared in a shared ancestor of every owner (`Linker_member_0xb8`), 1 have owners in no common chain -- where the same offset is two different layouts, not one field -- and 13 have no owner at all. A table whose row carries more than one field offset keeps no label: the store does not pair the offset with its owner, and naming one of them would pick.
| 24 table(s) in no family | 24 | -- | -- | -- |

**The 49 member tables are interface vtables, and their slot 0 is not a class slot.** All 492 class tables the class-map census covers (478 of them named by the registration path) share one slot-0 body, `0x00062650`; none of the 49 member tables carry it, and their slot-0 values are 36 distinct bodies. A class dispatch table starts with the entry every class table starts with; an interface object's table starts with its own first method, which is why no slot here can be named `<Class>_vt<slot>`.

**292 slots resolve to 115 distinct bodies, and 82 slots land outside the symbol map.** 24 slots are `__purecall`, 111 are stubs of 8 bytes or less (`ret`, `xor eax,eax; ret` -- the default implementations of an interface whose real work is elsewhere), 12 carry a real name in the map rather than a `FUN_` row, and 198 are `FUN_` rows the map covers.
**32 of the 82 slots outside the map are bodies U40's gap rule decodes** -- that is, **32 of the 269 gap bodies U40 counts in an unnamed table's slot are in these 49 member tables** and the rest are in the plain-only ones -- and the remaining 50 slots outside the map are bodies neither the map nor the padding rule reaches, so those interface methods are code nothing has decoded yet.

**25 of the 49 tables fall into 7 families, and the widest is the 22-slot one.** `0x004fc35c`: 6 tables at 8 slots, varying 0, 1, 2, 3, 4, 5, 6, 7. `0x004c7860`: 5 tables at 22 slots, varying 0, 1, 4, 6, 7, 8, 9, 10, 11, 15, 16, 17. `0x004d03b8`: 4 tables at 1 slot, varying nothing. `0x004d03bc`: 4 tables at 2 slots, varying nothing. `0x004c7854`: 2 tables at 2 slots, varying nothing. `0x004c93c8`: 2 tables at 2 slots, varying nothing. `0x004d0314`: 2 tables at 1 slot, varying nothing.

**In the `0x004fc35c` family the 6 tables are `0x004fc35c`, `0x004fc4cc`, `0x004fc568`, `0x004fc610`, `0x0050e88c`, `0x005139f4`.** They carry 8, 7, 6, 0 `__purecall` slots over 8 slots. 4 of them form a subsumption chain -- each rung implements exactly the slots the one below leaves `__purecall`, and the slots it implements resolve to 6=Engine_UnPenLev.

**In the `0x004c7860` family the 5 tables are `0x004c7860`, `0x004da720`, `0x004da850`, `0x004da970`, `0x004daa48`.** They carry 0 `__purecall` slots over 22 slots.

**In the `0x004d03b8` family the 4 tables are `0x004d03b8`, `0x004d0470`, `0x004d05d0`, `0x004fcc08`.** They carry 0 `__purecall` slots over 1 slot.

**In the `0x004d03bc` family the 4 tables are `0x004d03bc`, `0x004d0474`, `0x004d05d4`, `0x004fcc0c`.** They carry 0 `__purecall` slots over 2 slots.

**In the `0x004c7854` family the 2 tables are `0x004c7854`, `0x004c933c`.** They carry 1 `__purecall` slots over 2 slots.

**In the `0x004c93c8` family the 2 tables are `0x004c93c8`, `0x004c9610`.** They carry 0 `__purecall` slots over 2 slots.

**In the `0x004d0314` family the 2 tables are `0x004d0314`, `0x004d0528`.** They carry 0 `__purecall` slots over 1 slot.

<!-- /generated:class-member-tables -->

## The hole between `FUN_003f28b0` and `FUN_003f6d70` is a thunk pool

The `stub` edges above come from code the symbol map does not cover, and the map has one large hole
around them (`0x3f28b8`..`0x3f6d70`, 17 KB of `.text`). Reading that hole by stride reads it wrongly
twice over, so this section states both readings and the measurement that settles them.

- **The `.rdata` operand is a `FuncInfo`, not a `UClass`.** Each `mov eax, <imm32>` stub in the hole
  jumps to `0x003e559e`, a wrapper that pushes that immediate as an argument (with three zeroed slots)
  and calls `0x003eb0d0`. The immediate names a `.rdata` record whose first dword is `0x19930520`, the
  MSVC C++ exception `FuncInfo` header -- on every such stub. That is `__ehhandler$F`, the handler a
  function with a `try`/`catch` frame registers, so the hole is a compiler artefact and not a
  class-indexed constructor dispatch: no record there is a class table and no operand is a class.
- **The records do not repeat on a `0x20` stride.** They run 1..29 bytes (median 8) and are cut at their
  own terminators. `mov eax,<FuncInfo>` and `jmp` are two *separate* stubs wherever a forwarder ends
  with no `int3` between them, which is how one stride looked like `jmp, mov, jmp` -- a stride rule
  cannot see a boundary that has no padding behind it.
- **The hole is reached from the bodies it serves.** The store join finds a `c7 /0` frame-slot store or
  a `68` push of each stub's own address: 325 stubs are stored, 315 of them inside a body the symbol
  map already names (`dtor_MatSubAction`, `dtor_ScriptedAction`, `dtor_XboxClient` are three). No
  `E8`/`E9` in `.text` targets a stub in the hole, so the handlers are entered through their frame
  slot, which is what an `__ehhandler` is for.
- **The installer thunks in the hole are not vtable slots.** 22 stubs hold a jump into 13 of the 24
  installers. Each has exactly one dword hit in `.rdata`, all 22 inside the span `0x576d0c`..`0x579b38`
  that also holds the `FuncInfo` records, and none of them lands in any of the 492 class tables. A hit
  inside the `FuncInfo` pool is not a pointer table, so this does not claim the thunks are dispatched;
  the base-constructor shape stays the only reachable evidence for the 24.
- **Most of the unmapped tail jumps are not here.** The block above splits them between this hole and
  the map's other gaps, so "one unmapped island" never explained the whole population.

<!-- generated:native-island -->
| measure | value |
|---|---|
| hole (the symbol-map gap holding the most unmapped installer tail jumps) | `0x003f28b8`..`0x003f6d70` |
| installer tail jumps, all sites | 85 (1 inside a mapped body, 84 not) |
| ...of the unmapped ones, in this gap / in other gaps | 23 / 61 |
| stub rows in the artifact | 1589 (this census row is not one) |
| stubs in the hole | 1589 |
| stub sizes | 1..29 bytes, median 8 |
| `eh-handler` (`mov imm` + `jmp`) | 325 |
| ...whose immediate is a `FuncInfo` (first dword `0x19930520`) | 325 |
| bare `jmp` tail jumps | 0 |
| ...holding a jump or call into the 24 orphan installers | 22 over 13 installers |
| `mov reg,[ebp+d]` + `jmp` forwarders | 200 |
| other / undecoded | 1064 / 0 |
| stubs with bytes the subset could not read | 220 |
| stubs whose address a body stores | 325 (315 in a named body) |
| stubs an `E8` calls | 0 |


The references run `0x00576d0c`..`0x00579b38`, 22 of them inside that 11824-byte span.
Installer-thunk reachability: 22 stubs, 22 with a dword reference outside `.text` (.rdata); 0 of those references land in a class-table slot.

Installer tail jumps, per installer: `0x0002c8a0` x5, `0x00017050` x3, `0x0001cb70` x2, `0x00025ff0` x2, `0x00031830` x2, `0x0001cd10` x1, `0x0001cc10` x1, `0x00017f70` x1, `0x000172a0` x1, `0x000257d0` x1, `0x00025f30` x1, `0x000321f0` x1

The `FuncInfo` immediates run `0x00576210`..`0x0057c358` and every record read is `.rdata`.

Stubs a store names (the body each handler/thunk belongs to):

| stub | kind | target | operand | stored by | body |
|---|---|---|---|---|---|
| `0x003f28b8` | eh-handler | `0x003e559e` | `0x00576210` | `0x0001a922` | `Core_UnTemplate+22` |
| `0x003f28f7` | eh-handler | `0x003e559e` | `0x005763c0` | `0x0001adf2` | `FUN_0001adf0` |
| `0x003f2918` | eh-handler | `0x003e559e` | `0x0057642c` | `0x0001b312` | `FUN_0001b310+1` |
| `0x003f2943` | eh-handler | `0x003e559e` | `0x005764b0` | `0x0001b572` | `dtor_MatSubAction` |
| `0x003f2958` | eh-handler | `0x003e559e` | `0x005764d4` | `0x0001b622` | `dtor_SubActionCameraEffect` |
| `0x003f2983` | eh-handler | `0x003e559e` | `0x00576548` | `0x0001b812` | `dtor_ScriptedAction` |
| `0x003f2998` | eh-handler | `0x003e559e` | `0x0057656c` | `0x0001b8f2` | `dtor_ACTION_TriggerEvent+1` |
| `0x003f29b8` | eh-handler | `0x003e559e` | `0x005765b4` | `0x0001ba92` | `dtor_ACTION_WaitForEvent` |
| `0x003f29d8` | eh-handler | `0x003e559e` | `0x005765d8` | `0x0001bb62` | `dtor_SubActionDepthOfField` |
| `0x003f29f8` | eh-handler | `0x003e559e` | `0x005765fc` | `0x0001bc35` | `FUN_0001bc30` |
| `0x003f2a68` | eh-handler | `0x003e559e` | `0x00576c78` | `0x0001ca62` | `FUN_0001ca60` |
| `0x003f2a8b` | eh-handler | `0x003e559e` | `0x00576c9c` | `0x0001cab2` | `FUN_0001cab0+5` |
| `0x003f2ab3` | eh-handler | `0x003e559e` | `0x00576cec` | `0x0001cb72` | `FUN_0001cb70` |
| `0x003f2ae4` | eh-handler | `0x003e559e` | `0x00576d20` | `0x0001cc12` | `FUN_0001cc10` |
| `0x003f2b23` | eh-handler | `0x003e559e` | `0x00576d70` | `0x0001cd12` | `FUN_0001cd10` |
| `0x003f2b38` | eh-handler | `0x003e559e` | `0x00576db8` | `0x0001cda2` | `dtor_XboxClient` |
| `0x003f2b58` | eh-handler | `0x003e559e` | `0x00576ddc` | `0x0001ce52` | `dtor_XboxViewport` |
| `0x003f2bc1` | eh-handler | `0x003e559e` | `0x00576e38` | `0x0001d092` | `FUN_0001d090` |
| `0x003f2c5a` | eh-handler | `0x003e559e` | `0x00576ebc` | `0x0001d295` | `FUN_0001d290` |
| `0x003f2cf2` | eh-handler | `0x003e559e` | `0x00576f38` | `0x0001d6d5` | `FUN_0001d6d0` |
<!-- /generated:native-island -->

What the census cannot say: the `other` and `partial` rows are the stubs the bounded opcode subset
either read outside the three shapes or could not read whole, so the shape counts are a floor on each
kind rather than a partition of the hole. `make native-island` regenerates the artifact; `--check` is a
synthetic self-test (19 assertions, no dump) and `--check-doc` rebuilds the block from
`inventory/native_island.csv` -- including the hole's bounds and the tail-jump split, which travel in the
artifact's own `census` row so a page rendered from the CSV cannot disagree with the measurement.

## What installs a nameless table: the body's shape, not only its store

The store says a table is written at `[reg]`; it does not say whether the body that writes it is the
object's constructor. `tools/class-table-installer-shape.py` runs the two signatures
`tools/class-ctors.py` uses -- a constructor installs the table and returns `this`, a destructor
installs it before any call and returns nothing -- over **every body that installs one of the 724
tables**, so the nameless population is read by the same rule that named the map's own `ctor_`/
`dtor_` rows. The control is the 478 tables the registration path already names: 991 of their 999
installer bodies carry a `ctor_<Class>`/`dtor_<Class>` symbol, and the shape rule lands on the same
class for **978 of them (97.9%)**, so it is a measured reading and not an assumption about the 205.

<!-- generated:class-table-installer-shapes -->
| population | tables | installer bodies | returns `this` (ctor) | installs before any call (dtor) | neither | ends in a tail `jmp` | names one table only |
|---|---|---|---|---|---|---|---|
| named | 478 | 999 | 512 | 479 | 8 | 0 | 925 |
| plain-only | 205 | 341 | 189 | 103 | 49 | 1 | 224 |
| no-store | 4 | 0 | 0 | 0 | 0 | 0 | 0 |

**The control is the population the registration path already names.** Of the 999 installer bodies behind the 478 named tables, 991 carry a `ctor_<Class>`/`dtor_<Class>` symbol and the shape rule agrees with that class on 978; 512 bodies return `this`, 479 install the table before any call and 8 read as neither.

**Of the 205 plain-only tables, 189 installer bodies return `this`, 103 install before any call and 49 read as neither; 224 of the bodies install exactly one table, which is a self-install.** 97 tables are installed only by a body that returns `this`, 29 only by a destructor-shaped body, 53 by both, 26 by bodies the rule reads as neither and 0 name no installer body at all.

**A destructor never returns `this`, and the class-ctors signature only matches `mov eax, <reg>`.** Its second form is a load from the frame (`mov eax,[esp+N]` / `[ebp+N]`): 2 of the 103 dtor-shaped installer bodies behind the plain-only tables return `this` that way; the other 101 carry the destructor shape, which an initializer that installs the table into an object it was handed -- and returns something other than `this` -- has as well. That is why the verdict is a reading and not a proof: 29 tables have only such installers, and the artifact names them one body at a time instead of sweeping them into a constructor count.
<!-- /generated:class-table-installer-shapes -->

The plain-only tables split three ways, and each split is a different question. A body that
installs exactly one table and returns `this` is that object's constructor, so the table is an
object's own dispatch table inside a class no shipped package registers -- the naming channel for
those is the body, not the class map. The next section reads those bodies, and the channel they
carry is a field layout and a subsystem call rather than a name. A table whose only installer is destructor-shaped has no
constructor site in the sweep; the shape rule reads a tail-jumped epilogue the same way, so the
artifact names each such table rather than claiming the constructor is absent. And the tables whose
installer reads as neither are the residue the two signatures do not describe -- the population U40
names as the 399 bodies nothing names, met here from the table's side.

## What a nameless table's constructor carries: literals, callees and the parent

`tools/class-table-installer-shape.py` split the 246 tables no registration names by the shape of the body
that installs them and left the naming to "the body, not the class map". `tools/class-ctor-evidence.py`
reads each of those bodies once and records the three channels a constructor still offers: the literals it
pushes, the map's own names among the functions it calls, and the parent constructor its first call reaches.
`inventory/class_ctor_evidence.csv` carries one row per table of the 724-table population, with the
population's split and the literal, callee, parent and chain columns.

<!-- generated:class-ctor-evidence -->
| population | tables | installer bodies | a printable literal | an `ini:` literal | a literal that is a class name | a literal that is its own class | a named callee | a base-caller edge |
|---|---|---|---|---|---|---|---|---|
| named | 478 | 999 | 29 | 3 | 18 | 0 | 454 | 454 |
| at-least | 37 | 125 | 12 | 2 | 5 | 0 | 30 | 1 |
| plain-only | 205 | 341 | 55 | 2 | 15 | 0 | 135 | 53 |
| no-store | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

| the 205 plain-only tables, by what installs them | tables | a printable literal | a named callee | a base-caller edge |
|---|---|---|---|---|
| installed-by-ctor | 97 | 20 | 55 | 28 |
| dtor-only | 29 | 4 | 16 | 7 |
| ctor-and-dtor | 53 | 13 | 46 | 16 |
| unclassified | 26 | 18 | 18 | 2 |

**The control answers what a constructor literal is, and it is not a name.** 29 of the 999 installer bodies behind the 478 named tables push a printable UTF-16 literal; 18 of those tables push a literal that is a class name, and **none of the 478 pushes its own class**. The 3 `ini:` literals name the class that *declares* the config property -- `DemoPlayPendingLevel` pushes `ini:Engine.Engine.DemoRecordingDevice` and `NetPendingLevel` `ini:Engine.Engine.NetworkDevice` -- so a literal is a field default or a config section, and the class it can name is the declaring one.

**Of the 242 nameless tables -- 205 plain-only and 37 whose table a constructor bounds -- 67 push a printable literal and 165 call a named function.** 4 push an `ini:` section, and that form does name a class: `0x4c93c4` (`ini:Engine.Engine.Canvas`, `ini:Engine.Engine.Input`); `0x4c93c8` (`ini:Engine.Engine.Canvas`, `ini:Engine.Engine.Input`); `0x4fc610` (`ini:Engine.Engine.DemoRecordingDevice`); `0x5139f4` (`ini:Engine.Engine.NetworkDevice`). The other 63 are field defaults and data addresses -- the commonest are `Engine` (19), `new` (15), `XboxLiveOnlineTask` (15), `Core` (8), `Official.ini` (5), `SavePackagesAsOfficial` (5) -- and 20 of the 242 push a class name (a package, or a class they register), never their own. So the literal channel places 4 of the 242 by config class and describes the fields of the rest, and names none of them.

**The named callees place a table even when nothing names it.** 165 of the 242 nameless tables call at least one of the map's own `Core_*`/`Engine_*` rows; 31 signatures are shared by two or more of them and cover 112 tables, the largest being 33 tables that call the same set. A signature is not a name -- two classes in one subsystem share it -- which is why it is reported as a family and not as a label.

**The base-caller edge is the parent's constructor, and the named population holds it to the script tree.** A table's own installer is the body whose *last* store is that table, and its first call that reaches another table's installer is its parent constructor, followed through the null-check thunk. On the named tables the edge is the class's script `super` on **167**, a further ancestor on **211** (a script-only class installs its table from a body that calls its nearest native base), and on **54** the class has no script chain; **0 contradict**. Of the 242 nameless tables 54 get an edge -- 6 to a named class, 48 to another nameless table -- and 8 climb a chain to a named class, the deepest 3 levels.

**The 4 tables with no installer body are not tables: each is a window into a dword run, or a run the census cut short.** The store site each records lies in no body of the map, and the run of address-like dwords around it is tiled by tables the census already lists: `0x4f70a0` (2 slots) dword run 0x004f6ff8+44; this address at offset 42; 3 listed table(s) in the run, 2 with an installer body; the recorded store site 0x000e1706 lies in no body of the map; `0x5172ec` (5 slots) dword run 0x005172ec+27; this address at offset 0; 1 listed table(s) in the run, 0 with an installer body; the recorded store site 0x001df51e lies in no body of the map; `0x5181dc` (3 slots) dword run 0x005181d0+6; this address at offset 3; 2 listed table(s) in the run, 1 with an installer body; the recorded store site 0x00200606 lies in no body of the map; `0x53f7c4` (5 slots) dword run 0x0053f770+31; this address at offset 21; 2 listed table(s) in the run, 1 with an installer body; the recorded store site 0x003313ce lies in no body of the map; 4 of 5 slots are not 4-aligned, so they are in-function offsets.
<!-- /generated:class-ctor-evidence -->

The 97 tables a constructor alone installs -- the population the previous read left -- are the
`installed-by-ctor` row of the second table: 20 of them push a literal, 55 call a named body and 28 have a
base edge, so the channel is thinner there than in the population as a whole and still says nothing about a
name. What it does say is in the artifact, one table at a time.

**The literal channel is closed by its own control, and closed in a specific way.** A constructor literal is
a field default or a config section, and the class it can name is the class that *declares* the property,
never the object's class: `DemoPlayPendingLevel` pushes `ini:Engine.Engine.DemoRecordingDevice` and is not
`DemoRecordingDevice`. Across the 478 named tables **no constructor pushes its own class's name** while 18
push some *other* class's name (a package, or a class registered from inside the constructor), which is why
the class-name count is reported beside the zero rather than on its own.

What the literals do give is a reading of what the object holds. `0x004c8db8`'s constructor pushes
`Z:\Cache`, `Z:\Help`, `Z:\Maps`, `Z:\Animations`, `Z:\Music` and `D:\` through `thunk__wcscpy` and then
calls `_CreateDirectoryA@8` -- a file manager with six configured roots, one of them a CD drive, which no
class name would have told us. `0x004c96fc`'s writes a 3, a 0, a 0 and a -1 into `[esi+0x4]`..`[esi+0x10]`
and advances the two globals at `0x005fe168`/`0x005fe16c` by `0xe0` on every call, a counter. This is object
layout read from the constructor's side, and it is the same reading the class-record tools do from the
record's side.

**The callee channel places a table in a subsystem without naming it.** 169 of the 246 nameless tables call
at least one of the map's own `Core_*`/`Engine_*` rows, and 31 signatures are shared by two or more of them.
The two largest are idioms rather than classes: 33 tables whose only named callee is `__allmul` (the 64-bit
multiply a counter or a GUID is advanced with) and 10 whose only one is `Core_UnTemplate`. The three tables
that push `ini:Engine.Engine.Canvas` and `ini:Engine.Engine.Input` also call `Engine_UnCanvas` and
`Engine_UnIn` -- the pair of constructors they run names the pair of objects they hold, which is as close to
a name as this channel comes, and it is still a field's type rather than the class's name. Families are
reported, never promoted to labels: two classes in one subsystem share a signature.

**The parent channel is read from the body and held to the script tree.** A table's own installer is the
body whose *last* store is that table -- the last-store rule the site census already uses -- and its first
call that reaches another table's installer, through the null-check thunk, is its parent's constructor. On
the named tables that edge is the class's script `super` 167 times, a further ancestor 211 times, and the
class has no script chain 53 times, with **0 contradictions**: a script-only class installs its table from a
body that calls its nearest native base, so the edge is the nearest native ancestor and not the immediate
one. Of the 246 nameless tables 55 get an edge and only 9 climb to a named class, so the tree does not name
them either; it says which native ancestor's constructor they run, and for the 48 that hang off another
nameless table it says which of the 246 is above which.

**The four tables the census lists with no installer body are not tables.** `0x4f70a0` is the last two
dwords of a 44-dword run that three listed tables tile, two of them with installer bodies (`0x4f6ff8` is
`I3DL2Listener`'s); `0x5181dc` is three dwords into a six-dword table whose head holds two `__purecall`
entries, an interface's tail; `0x53f7c4` is 21 dwords into a run whose entries are 2-mod-4 in-function
offsets, an exception-scope table and not a vtable; `0x5172ec` starts a 27-dword run immediately after the
UTF-16 string `mesh!!`. None of the four has a store site inside any body of the map, which is the census's
reason for listing it and the reason none of them is a class table.

## Who calls the installer: the class below it, and the tables a class builds twice

The three channels above read the body that stores a table. `tools/class-installer-callers.py` reads the
other end of the edge -- every body that **calls** an installer -- over all **724** tables of
`inventory/native_vtable_reach.csv`, not only the 492 the class map covers: **228 of the 246 nameless
tables are tables the mapped census never sees**, and they are read here by the same rule.
`tools/class-table-parents.py` already turns the callers of an unnamed installer into a parent name for the
18 mapped tables the class map leaves open; this pass covers the other 228 and records what the callers say
about each table instead of only naming a parent. MSVC makes every subclass's constructor and destructor
call its parent's installer, so the direct callers of an installer are the classes just below it, and the
deepest common ancestor of those classes over `classes.csv`'s script supers plus
`inventory/class_table_parent_edges.csv`'s native parent edge is the class the table belongs to.

<!-- generated:class-installer-callers -->
| population | tables | installer body called directly (`E8`) | of those, only from a body the map does not cover | a caller class names the owner | no `E8` caller | no installer body |
|---|---|---|---|---|---|---|
| named | 478 | 478 | 0 | 105 | 0 | 0 |
| at-least | 37 | 36 | 0 | 9 | 1 | 0 |
| plain-only | 205 | 197 | 20 | 15 | 8 | 0 |
| no-store | 4 | 0 | 0 | 0 | 0 | 4 |

**The control: on the 478 named tables the callers are the derived classes, and their deepest common ancestor is the table's class on 66, one of its descendants on 38 and an unrelated class on 5.** 369 named tables are called only from bodies with no `ctor_`/`dtor_` symbol, and 0 have no `E8` caller at all.

**Of the 242 nameless tables, 41 are built inside a class's own constructor -- 19 by a group of sibling classes, so the table is the base their constructors and destructors reset to, and 22 by a single class whose own table is a different address, a second table that class builds. 172 are called only from bodies with no class symbol, 20 are called from a body inside a map gap and 9 have no `E8` caller at all.**

**9 tables have no `E8` caller. On 1 of them the installer body is also absent from the reachability fixpoint (`inventory/native_unreachable.csv`: no `E8`, `E9` or dword in any section names it), so the table and its constructor are an isolated pair; on the other 8 a dword the `E8` scan cannot see still names the body.**

**55 tables have a direct `E8` caller inside a body the symbol map does not cover, and for 20 of them that is the only caller. The gap census names the enclosing body of 4 of those 20 (3 distinct heads: `0x002952d0`, `0x002b6f70`, `0x002b73a0`), and the other 16 sites land in gaps the pad-and-prologue rule leaves open.**

**Of the 58 tables whose installer is destructor-shaped or neither, 10 have callers that carry a `ctor_`/`dtor_` class symbol, and there the callers are the sibling destructors that reset the pointer to this table; 35 are called only from bodies with no class symbol, 7 from a body inside a map gap, 6 have no `E8` caller and 0 have no installer body.**
<!-- /generated:class-installer-callers -->

**The direct call is the rule, not the exception, and the control is the class's own lineage.** Of the
478 named tables every one has an installer body that some `E8` in `.text` calls, and 104 of the 109 whose
callers carry a `ctor_`/`dtor_` class symbol land the deepest common ancestor on the table's class (66) or on
one of its descendants (38) -- **104 of 109, the five strangers being the `Material`/`RenderedMaterial`
families whose common base has no script row.** No named table is without a direct caller, so the
installer is reached by a call and not by a pointer. What the 369 named tables
called only from unnamed bodies say is the other half: their callers are real bodies the symbol map still
has no name for, which is U40's population met from this side.

**Of the 246 nameless tables, 41 are built inside a class's own constructor.** Nineteen are called by a
group of sibling classes -- `AudioSubsystem`/`Input`, `D3DRenderDevice`/`NullRenderDevice`,
`ChannelDownload`/`HTTPDownload`, `IndexBuffer`/`SkinVertexBuffer`, the five `VertexStream*` classes,
`TcpNetDriver`/`TcpNetDriverXbox`/`DemoRecDriver`, `TcpipConnection`/`TcpipConnectionXbox`/`DemoRecConnection`,
`Level`/`NetPendingLevel`/`DemoPlayPendingLevel`, `ActorChannel`/`ControlChannel`/`FileChannel`/`VoiceChannel`
-- so the table is the base their constructors and destructors reset to, and the group names the family
around it. Twenty-two are called by exactly one class that **already owns a different table**: `Texture`
(18 slots at `0x0051ffd8`, its own table `0x004cfba0`), `StaticMesh` (two 9-slot tables and its own
`0x0051dd28`), `Cubemap` (15 against `0x005205c8`), `VertMesh` (11 against `0x00520fd8`),
`SkeletalMeshInstance` (9 against `0x0051c188`), `SkeletalMesh` (four 2-slot tables against `0x0051c310`),
`D3DRenderDevice` (65 against `0x0052b460`), `XboxViewport` (1, 2 and 56 slots against `0x004c9618`),
`XboxClient`, `GameEngine`, `TextBufferFactory`, `ObjectExporterT3D`, `LinkerLoadGame`/`LinkerSaveGame` and
`XHVVoiceSubsystem`. Those are not class tables the packages forgot: the class is named and the array is a
second one its constructor writes -- 17 at `[reg]` at least once and 11 into a member field, so
`inventory/class_table_store_fields.csv` is the discriminator between a second class table and a member's
interface, and the 11 written only at `[reg]` are the residue a per-body read still has to name.

**The 29 destructor-shaped and 26 unclassified tables get the per-body answer U40 asked for, and it is the
destructor's own edge.** Ten of the 58 have named `dtor_` callers, and in every one the callers are the
sibling destructors that reset the pointer to this table before running their own base's: `AudioSubsystem`
and `Input` (1 and 31 slots), the material family (`ConstantMaterial`, `ParticleMaterial`,
`PixelShaderMaterial`, `ProjectorMaterial`, `Shader`, `TerrainMaterial`, `TexCoordMaterial`, `VertexColor`,
46 slots at `0x004c7a10`), `Texture`/`ProxyBitmapMaterial`/`ScriptedTexture`/`ShadowBitmapMaterial`
(48 slots at `0x004c7ac8`), `CameraEffect`'s eleven screen subclasses (33 slots at `0x004c7c78`, the one
group whose common ancestor resolves) and likewise `D3DRenderDevice`/`NullRenderDevice`, the two download
classes, the two buffer classes and the five `VertexStream` classes. So a destructor-shaped installer is not
a missing constructor site: it is the base's destructor, and the table it stores is the base's vtable. The
other 48 split 28 called only from bodies with no class symbol, 7 from a body inside a map gap, 5 with no
`E8` caller and 8 with no installer body,
which is why a shape rule alone could not classify them.

## Where the pointer came from: `this`, a member, a frame slot or a global

The sections above read every store relative to the body that writes it, and that body is not always the
object's own constructor: a class that builds a member in place stores into `[reg]` of the *member*'s
`this`, which is a field of the caller, and a body whose `this` is a stack address is building a
temporary. `tools/class-store-provenance.py` reads the other half -- `inventory/class_store_provenance.csv`,
724 rows -- from two instructions per store: the **base register inside the storing body**, resolved
through the thiscall prologue (`mov esi,ecx` makes `esi` the object), a member load, a `lea`, a frame slot,
an absolute operand or a `call`'s return; and the **`ecx` every `E8` into that body passes**, resolved in
the caller's own terms (`mov ecx,esi` is the caller's object, `lea ecx,[esi+0x60]` a member's address,
`lea ecx,[esp+0x14]` a temporary, `mov ecx,eax` after a call a fresh object). Four readings come out of
it: whose object the store writes, which level a caller resets, which member's offset the table sits at,
and whether the object is a `.data` singleton.

<!-- generated:class-store-provenance -->
| population | tables | own object | base level | a member of the caller | a frame slot | an allocation | a global | mixed | no direct call | no installer | no store |
|---|---|---|---|---|---|---|---|---|---|---|---|
| named by the registration path (control) | 478 | 345 | 104 | -- | -- | -- | -- | 29 | -- | -- | -- |
| nameless: a class ctor stores it | 37 | 12 | 4 | 2 | -- | 1 | -- | 17 | 1 | -- | -- |
| nameless: only bodies no class names store it | 209 | 16 | 31 | 11 | 27 | 32 | 5 | 63 | 20 | 4 | -- |

**The control: of the 478 named tables, 345 are installed with the caller's own `this` and 104 with a base level's.** A destructor (and a constructor) level writes its own table at `[reg]` and then calls the next level's with the same object, so a caller that owns a different table is the evidence that the callee's table is an ancestor's. 29 of the named tables are mixed -- a class constructed both as a member and as a temporary by different callers reads that way, which is a fact about its callers rather than about the table.

**Of the 246 nameless tables, 28 are installed with a caller's own `this`, 35 with a base level's, 13 through a member's address, 27 into a frame slot, 33 into an allocation and 5 into a global.** A frame-slot or allocation install is an object built in place -- a temporary, an argument passed by value, or an element of a local array -- so those tables belong to classes the build only ever instantiates as a local; a global install is a singleton in `.data`, constructed behind a one-shot flag. None of the four is a table nothing installs, which is what the store's own form made them look like. The 20 tables `inventory/class_table_store_fields.csv` calls `frame-only` -- their own store writes a stack slot, so the address is spilled rather than installed -- split when the storing body's callers are read: 4 into a frame slot, 2 into an allocation, 2 into a global, 0 with the caller's own `this`, 7 mixed and 5 with no direct caller.

**A second table is a different object, not a second array: of the 23 tables the caller census reads as a second table, 12 are written only at `[reg]` and 11 at least once into a member field.** Read at the call site, the 12 `[reg]` ones resolve to 5 base levels, 4 members of the caller, 1 to a slot or an allocation and 2 mixes -- **not one of them is the owner's own object**, which is what "the class builds it a second time" would have required. The base levels are the same tables `inventory/class_table_parent_edges.csv` records as those classes' `parent_va`, reached from the call sites instead of from the destructor thunk.

**The member offset is the same measured twice.** For 6 second tables the store's own `[reg+disp]` and the caller's `lea ecx,[reg+disp]` are both in the image, and they agree on 6 and differ on 0: 0x0051bc08 `0x164 0xd8`, 0x0051bc10 `0x108 0x134`, 0x0051bf18 `0x194 0xc0`, 0x0051bf20 `0x14c`, 0x0051bf28 `0x17c`, 0x0051d854 `0x14c`.

**5 tables are a module singleton.** 3 of them are stored into the global they live in -- `0x005174a8` into 0x7ab9e8 and 0x7aba2c, `0x00517588` into 0x7ab9d8 and 0x7aba1c, `0x0053a194` into 0x7b9360 -- by a body behind a one-shot flag (`test`/`or` on a byte beside the object, then the store), so the object is a `static` in `.data`, not a class instance. The other 2 are never passed anything but a global's address as `this`, so the object is one too -- it is simply built somewhere this read cannot follow.

**The register-carried install is real but rare: 1 of the 724 tables has one, and none of them is a table without an immediate store at all.** The pair `mov reg,<table>` / `mov [reg2(+disp)],reg` puts the address in the first instruction, so a census that reads the store's own operand sees a register; here the channel adds nothing to the population, which is the negative that keeps the immediate census honest rather than a route still to walk.

**What this settles for U40.** The `[reg]` store is the object's own slot *relative to the body that writes it*, so the store's form alone cannot separate a class table from a member's: in 78 of the 246 nameless tables the store lands on an object that is not the caller's own, and 35 of those are an ancestor class's table, which is a name the caller supplies. What is left needing a per-body read is the 80 mixed tables -- a receiver whose `ecx` comes out of arithmetic (`lea ecx,[eax+esi*4]`), an inlined constructor, or two callers that disagree -- and the 21 tables no body calls directly.
<!-- /generated:class-store-provenance -->

**The three `no-store` tables were a decode bug, not an absence.** `tools/class-table-store-fields.py`
read `C7 05 <disp32> <imm32>` as a six-byte instruction, so the "table" it recorded was the store's own
displacement and an absolute store was invisible: `0x005174a8`, `0x00517588` and `0x0053a194` looked like
tables no store names. Read at ten bytes they are three module singletons -- the bytes at `0x7ab9f8` and
`0x7b936c` are their one-shot flags -- and three more tables gain a global site, which is why
`inventory/class_table_store_fields.csv` reads 20 frame-only and 3 global where it read 20 frame-only and
3 `no C7 store`.

**What this does not say.** It does not name the classes. The five base-level tables are the `parent_va`
of `XboxClient`, `XboxViewport`, `XHVVoiceSubsystem`, `ObjectExporterT3D` and `TextBufferFactory`, and
naming that level is `docs/NATIVE_CLASS_TREE.md`'s question, not this one. It does not follow an `ecx`
that comes out of arithmetic -- the section below does, and the receiver is an element of a dynamic
array -- and it does not decide whether a mixed table is two real construction sites or one caller the
decoder mistook; that too is the section below, and the artifact keeps every call site's resolved chain so
the deeper read starts from the row rather than from the census.

## `ecx` out of arithmetic: an array element, a static in `.data`, or a size

The two residues the section above leaves -- its 109 `mixed` tables and the 21 no `E8` reaches -- are both
a question about one instruction, so `tools/class-receiver-reads.py` reads the same store sites again, one
instruction deeper, over that same population (`inventory/class_receiver_reads.csv`, 130 rows). Three
readings come out of it, and the first is the arithmetic the one-level read reported as an opaque
`indexed`: the build constructs an object **in place inside a dynamic array**, `mov eax,[esi]` (the
array's data pointer) then `imul edi,edi,<size>` then `add edi,eax`, so `ecx` is the new element itself and
the stride is the element's size. The second is that a `mov ecx,<abs>` is not an unnamed temporary but a
fixed object in `.data`, and a `mov ecx,[esi+0x1c]` the pointer stored in that field rather than the field.
The third is that an installation into a fresh object carries the argument its allocating call was handed,
which on the control population is the class's registered `object_size` -- so a nameless table's temporary
has a measured size. What none of the three is, is a class name: they say which object a table belongs to.

<!-- generated:class-receiver-reads -->
| population | tables | an array element | a static object | a pointer field | a walked pointer | two ways (member + temporary) | unresolved | no receiver |
|---|---|---|---|---|---|---|---|---|
| mixed | 109 | 21 | 3 | 10 | -- | 74 | 1 | -- |
| no direct caller | 21 | -- | -- | -- | -- | -- | -- | 21 |

**21 of the 109 mixed tables construct their object inside a dynamic array, and the element's size is in the stride.** The build's shape is `mov eax,[esi]` (the array's data pointer), `imul edi,edi,<size>` (or `shl edi,<n>`), `add edi,eax`, `mov ecx,edi`, `call <installer>` -- so `ecx` is the new element itself and the stride is the element's size: a struct of that size inside a `TArray`. The head is the pointer the data pointer was loaded from -- the caller's own object at `this+0`, a frame slot holding the `TArray`, or a global -- so the array is the caller's member or local, and the element is the object this table belongs to. The one-level read reported that receiver as one opaque `indexed` or `?`.

| table | class | element size | the array's head | installer |
|---|---|---|---|---|
| 0x00510e6c | -- | 0x1c | [[esp+0x14]] | FUN_00179860 |
| 0x00510f00 | -- | 0x6c | [[esp+0x14]] | FUN_00179ab0 |
| 0x00510f78 | -- | 0xb0 | [[esp+0x14]] | FUN_0017a780 |
| 0x0051724c | -- | 0x4c | [walk([esp+0x10]+0x111c)+0x0] | FUN_001de030 |
| 0x00517428 | -- | 0x4 | [ebp+0x34c] | FUN_001e2110 |
| 0x00517430 | -- | 0x4 | [esp+0x3dc] | FUN_001dfde0 |
| 0x0051748c | -- | 0x4 | [[esp+0x14]+0x410] | FUN_001e2110 |
| 0x005174a0 | -- | 0x4 | [ebp+0x34c] | FUN_001e2110 |
| 0x00517ff0 | -- | 0x4 | [0x7b940c] | FUN_001f1f10 |
| 0x005181a8 | -- | 0x1c | [[esp+0x14]] | FUN_001f7620 |
| 0x0051b0e4 | -- | 0x4 | [ebp+0x58] | FUN_0023a8f0 |
| 0x0051bc08 | -- | 0x138 | [[esp+0x14]] | FUN_002382f0 |
| 0x0051bc10 | -- | 0x138 | [[esp+0x14]] | FUN_00238380 |
| 0x0051bc18 | -- | 0x138 | [[esp+0x14]] | FUN_00239c90 |
| 0x0051bca0 | -- | 0x4 | [ebp+0x58] | FUN_0023a8f0 |
| 0x0051bf18 | -- | 0x138 | [[esp+0x14]] | FUN_0023ad50 |
| 0x0051d7e8 | -- | 0x20 | [[esp+0x14]] | FUN_0024e230 |
| 0x0051f6d8 | -- | 0x4 | [[esp+0x14]+0x410] | Core_UnObjBas |
| 0x0051f6ec | -- | 0x4 | [[esp+0x14]+0x410] | Core_UnObjBas |
| 0x0051f700 | -- | 0x4 | [[esp+0x14]+0x410] | Core_UnObjBas |
| 0x0051faa0 | -- | 0x1c | [[esp+0x14]] | FUN_00278450 |

**3 are a fixed object in `.data` and 10 a pointer loaded out of a field, so neither is an unnamed temporary.** `mov ecx,0x7ab94c` hands the installer the address of a module singleton: 0x0051707c -> 0x7b53c8, 0x00518080 -> 0x6e7a48, 0x0052352c -> 0x7b74c0. A `mov ecx,[esi+0x1c]` is the pointer *stored* in that field rather than the field itself (the field itself is reached with `lea`, which is how a table installed into an embedded member reads), and 10 tables are installed into an object a field points at.

**A temporary's size is the argument the allocating call is handed, and on the control it is the class's registered `object_size`.** The receiver of an installation into a fresh object is read as `new(<arg>:<callee>)` -- the immediate pushed before the call that returns it -- and joined against `inventory/class_registrations.csv` it agrees on **11 and differs on 0** of the tables whose class a registration names, so the argument is a size. The 74 tables constructed both as a member and as a temporary are a fact about their callers, not a residue, and the size is what separates two classes with the same shape: 0x004c7800 0x30, 0x004c8410 0x8064, 0x004c84d0 0x86c, 0x004c9738 0x84, 0x004c9b50 0x58, 0x004d0470 0x4fb4, 0x004d0474 0x4fb4, 0x004d0480 0x4fb4 and 32 more.

**The 21 `no-direct-call` tables have a channel that is not a call, and a call for most of them.** An installer is also reached by a tail jump (`E9`) or by a dword that holds its address -- a class-table slot, or a `.data` pointer: 9 of 21 have one of the three and 12 have none, which is a statement about *this* scan and not about the image, because a direct `E8` call is a fourth channel it never reads. `inventory/class_installer_callers.csv` scans for it and finds **14 of the 21 called, 13 of them only from a body inside a map gap, 7 with no `E8` and 0 with no installer body at all**. By channel: 6 by a class-table slot (0x004d0d80 slot:0x5045e0+30, 0x004f7070 slot:0x4d1f88+36, 0x0051d300 slot:0x51d260+39, 0x0051d444 slot:0x51d3a0+39); 1 by a tail jump (0x004eaf68 tail:0xbd578); 4 by a dword pointer (0x0051a810 ptr:DOLBY+0x36e0, 0x0051a810 ptr:DOLBY+0x3718, 0x0051a810 ptr:DOLBY+0x3760, 0x00523008 ptr:.rdata+0xf6).

**1 stays unresolved** (0x0051b0cc): the instruction that defines the receiver is not a copy, an address, a load or a scaled add, and the artifact names its mnemonic.
<!-- /generated:class-receiver-reads -->

**What this does not say.** It does not name the classes behind the array elements: the element a table is
installed into is a struct of the stride's size inside a `TArray`, and the array's owner is the caller
body, which the map still calls `FUN_*`. It does not decide whether an installer reached by nothing at all
is dead code or reached through an address no dword in the image holds, which is the reading
`tools/native-unreachable.py` owns. It does not read a receiver out of an `imul` whose index is not the
register the walk follows, which is what the three unresolved rows are.

**The read also repaired the store form the two censuses above it misread.** `C7 84 24 <disp32> <imm32>` is
an **eleven-byte** store -- a frame slot past `disp8` -- and both `tools/class-table-store-fields.py` and
`tools/class-store-provenance.py` computed its immediate at the ten-byte offset, so they read the
displacement's last byte instead of the table. The form occurs **1,038** times in `.text` and **6** of those
stores carry one of the 724 tables. Both tools now read it at its own length and pin it in their selftests,
which gains stores on four tables -- `0x004c7860` 38 to 41, `0x0051707c` 1 to 2, `0x00517e88` 1 to 2 and
`0x00564dbc` 2 to 3 -- moves `0x00564dbc` from `own-object` to `mixed` and leaves every verdict of the
store-form census where it was, so this page's earlier counts stand except where the regenerated blocks
say otherwise.

## What this measurement does not say

- **It does not separate dead code from map artefacts.** A body the symbol map names at an odd
  address that nothing references may be a definition Ghidra invented over data; the artifact keeps
  the start kind (`map`/`pad`), the alignment and the reference count on every row so the two can be
  told apart by a later read, and the rendered counts are split by start kind.
- **It does not claim a body is cut content.** Native code being unreachable says nothing about the
  scripts or the maps; `tools/class-reach.py` is the instrument for that question.
- **It does not read what a computed jump cannot show.** A body reached only through a jump table that
  holds offsets rather than addresses (an opcode dispatch table, say) would be reported unreachable.
  No such table is known in this image, but the census is a lower bound on reachability for that
  reason, and the resident `GNatives` table (`docs/NATIVE_VM.md`) is a table of addresses, which the
  dword scan does read.

- **It does not produce the site census.** `tools/class-registrations.py` reads the same last store
  (`make class-registrations`), and the two artifacts agree on all 493 rows, but each tool owns its own
  block on this page and `make ci` holds it to its own artifact.
- **It does not say why two classes name an ancestor's constructor.** FractalTexture's and WaterTexture's
  sites resolve through one shared thunk to Texture's constructor, while the image holds a separate body
  for each (`0x002e81b0`, `0x002e8260`); the registration's fourth argument may be the constructor of the
  nearest ancestor that has one rather than the class's own, and this measurement does not decide that.

- **It does not name the 24 tables.** The caller classes are the derived set, and `tools/class-table-parents.py`
  turns that set into a candidate name (the deepest common ancestor of the callers); this sweep only says
  who installs them, whether a registration thunk reaches the installer, and whether it is called at all.
  Its two readings meet in `inventory/class_orphan_tables.csv`'s `proposed` column and neither is a
  measurement of the other.
- **It does not read the hole the tail jumps come from.** Those `stub` edges sit in a 17 KB gap between
  the 8-byte `FUN_003f28b0` and `FUN_003f6d70`; `tools/native-island.py` reads it, and the section
  above says what it is. It is not a registration and not a dispatch table: its `mov eax, <.rdata>`
  stubs are the compiler's C++ exception handlers.

## The other 61 tail jumps sit in exception descriptors, not in a call

The hole above holds 23 of the 84 unmapped installer tail jumps; the other 61 sit in 13 further gaps of
`symbols/xbox_functions.csv`, and they were the population that could still have turned out to be a real
caller of the 24 unregistered tables. Reading them answers it the other way.

- **Every one of the 61 is the same 8-byte forwarder.** The instruction walk decodes all 61 sites inside a
  stub of the shape `mov reg,[ebp+d]; jmp <installer>`, 8 bytes each, naming 18 of the 24 installers. The
  14 gaps hold 8,460 stubs in all (the hole's 1,589 among them), so the forwarders are a small minority of
  what fills them -- the rest is the `__ehhandler` pool and pad the bounded walk cannot decode.
- **No branch reaches any of the 14 gaps.** The `E8`/`E9` edges entering a gap from outside it total 0,
  and no `E8` in `.text` targets any of the 61 stubs. The 11 `jmp` edges whose site is inside the same gap
  are short hops between fragments of one gap, not entries. Neither a `c7 /0` store nor a `68` push names
  a stub -- the channel that reaches an `__ehhandler` through its frame slot.
- **The one channel that does name them is a `.rdata` dword, and it is not a table.** All 61 stubs carry a
  dword reference (75 refs), and not one of them is a class-table slot: the check spans all 724 class
  tables in the image, the 492 mapped and the 232 unmapped, and returns 0. Each ref sits in an exception
  descriptor -- 74 of the 75 behind the `0xffffffff` unwind-map terminator and 39 of the 75 immediately in
  front of a `0x19930520` `FuncInfo` header (102 and 55 of the 103 refs the whole 84-site population
  carries).
- **The whole map is like this, not just this hole.** 1,906 `eh-handler` stubs sit in the 14 gaps and
  1,896 of them name a `0x19930520` record. The 10 that do not share the `mov imm32; jmp` shape but their
  operand is a `.data` address, and all ten are in one gap (`0x0040ab78`..`0x0040c2c0`): the shape rule
  alone would count them as handlers, which is why the claim is the magic, not the shape. The symbol map's
  holes are therefore the compiler's exception pools through and through, not the 17 KB one alone.
- **So the population that could still be a real caller of the 24 is empty.** A forwarder whose only
  reference is an exception descriptor is compiler plumbing for a `try`/`catch` frame, not a dispatch
  entry, and the base-constructor shape (103 classes calling an installer from their own constructor)
  stays the only reachable evidence for the 24 tables.

The table below is rendered from the artifact's own `census` row, so a page rebuilt from
`inventory/native_island_gaps.csv` carries the same numbers as the measurement that wrote it.

<!-- generated:native-island-gaps -->
| measure | value |
|---|---|
| installer tail jumps, unmapped | 84 |
| ...in the island hole `0x003f28b8`..`0x003f6d70` / in the other gaps | 23 / 61 |
| map gaps holding them | 14 (13 besides the hole) |
| bytes of `.text` those gaps cover | 93441 |
| stubs decoded in them | 8460 |
| the 61 sites' stub shape | 61 of 61 are a forwarder, all 8 bytes |
| `E8`/`E9` edges entering a gap from outside it | 0 (`E8` 0) |
| `jmp` edges whose site is inside the same gap | 11 |
| `eh-handler` stubs in the 14 gaps / naming a `FuncInfo` | 1906 / 1896 |
| the 61 stubs an `E8` calls / a store or push names | 0 / 0 |
| the 61 stubs a dword outside `.text` names | 61 (75 refs) |
| ...of those refs, in a class-table slot (492 mapped + 232 unmapped tables) | 0 of 103 |
| ...whose next dword is a `FuncInfo` header `0x19930520` | 39 of 75 (55 of the 103 refs all 84 carry) |
| ...whose previous dword is the `0xffffffff` unwind terminator | 74 of 75 (102 of 103) |
| installers the 84 tail jumps name | 24 of the 24 (the 61 name 18) |

| gap | bytes | stubs | stub shapes | sites | edges in from outside | edges inside | eh-handler / `FuncInfo` |
|---|---|---|---|---|---|---|---|
| `0x003f28b8`..`0x003f6d70` (hole) | 17592 | 1589 | eh-handler:325,forward:200,other:1064 | 23 | 0 | 4 | 325 / 325 |
| `0x003fc208`..`0x00400340` | 16696 | 1551 | eh-handler:293,forward:165,other:1093 | 23 | 0 | 0 | 293 / 293 |
| `0x004012a8`..`0x00403020` | 7544 | 700 | eh-handler:200,forward:91,other:409 | 9 | 0 | 0 | 200 / 200 |
| `0x00400a08`..`0x004012a0` | 2200 | 194 | eh-handler:53,forward:29,other:112 | 6 | 0 | 0 | 53 / 53 |
| `0x00406338`..`0x00409100` | 11720 | 1078 | eh-handler:189,forward:173,other:716 | 5 | 0 | 0 | 189 / 189 |
| `0x003f27e7`..`0x003f28b0` | 201 | 12 | eh-handler:7,forward:5 | 3 | 0 | 0 | 7 / 7 |
| `0x003f6d78`..`0x003f71a0` | 1064 | 86 | eh-handler:33,forward:27,other:26 | 3 | 0 | 0 | 33 / 33 |
| `0x003f7d78`..`0x003fa140` | 9160 | 787 | eh-handler:176,forward:95,other:516 | 2 | 0 | 0 | 176 / 176 |
| `0x00400348`..`0x00400a00` | 1720 | 152 | eh-handler:56,forward:20,other:76 | 2 | 0 | 0 | 56 / 56 |
| `0x00403028`..`0x004042e0` | 4792 | 419 | eh-handler:106,forward:52,other:261 | 2 | 0 | 7 | 106 / 106 |
| `0x00409108`..`0x0040ab70` | 6760 | 608 | eh-handler:167,forward:67,other:374 | 2 | 0 | 0 | 167 / 167 |
| `0x0040ab78`..`0x0040c2c0` | 5960 | 549 | eh-handler:182,forward:41,other:326 | 2 | 0 | 0 | 182 / 172 |
| `0x003f71a8`..`0x003f7d70` | 3016 | 252 | eh-handler:81,forward:52,other:119 | 1 | 0 | 0 | 81 / 81 |
| `0x00404f98`..`0x00406330` | 5016 | 483 | eh-handler:38,forward:189,other:256 | 1 | 0 | 0 | 38 / 38 |

All 61 sites outside the hole sit in stubs, 61 of which the walker decodes as a forwarder, and `E8`/`E9` edges entering the gaps from outside total 0. Across the whole 84-site population the only channel that names a stub is a `.rdata` dword (103 refs), and none of them is a class-table slot, mapped or unmapped: they sit in exception descriptors -- the word in front of one is the unwind terminator on 102 of the 103 refs, and 55 stand immediately in front of a `FuncInfo` header. So the 84 are compiler exception plumbing rather than dispatch entries, no branch reaches any of them, and the 24 installers keep their 103 constructor callers as the only reachable evidence.
<!-- /generated:native-island-gaps -->

What this does not say: a descriptor names *where* a stub's address is stored, not what the unwinder does
with it. Whether the Xbox exception dispatcher can call one of these forwarders is a question about the
runtime rather than about the image, and nothing here measures it -- the claim is that no branch reaches
them, no store names them, and no class table holds them. `--check` is a synthetic self-test (18
assertions, no dump, writes nothing) and `--check-doc` rebuilds the block from the committed artifact.


## What an unnamed table is: a class's dispatch table, or an interface's vtable

The censuses above read a table through the instruction that writes it -- the store's operand, the call
site's `ecx`, the ctor's other stores -- and none of them says *what the table is*. The two things a
`[reg]` store can hold are not the same kind of object: a class's dispatch table opens with the entries
every class table opens with, and an interface object's table opens with the interface's own first
method. `tools/class-plain-tables.py` reads the **246** tables no registration names
(`inventory/class_plain_tables.csv`, 246 rows) and asks the contents:

- **the class-table prefix.** All 478 tables the registration path names start with the same three bodies
  (`0x00062650`, `0x00062660`, `0x00062670`), and none of the 49 member tables does, so "starts with the
  prefix" is a *measured* test on both sides of the population rather than a heuristic. A table that
  carries it is a class's dispatch table whatever its store wrote into.
- **the slot vocabulary.** A C++ vtable's slots are the class's own virtual methods, and this build's map
  carries masked-symbol matches against the shipped XDK libraries (`uix.lib`, `xvoice.lib`,
  `xonline.lib`), so a table whose slots are one class's methods is that class's vtable.
- **the installer's symbol.** `??0<C>@@`/`??1<C>@@` is a constructor or destructor and names the class it
  builds outright. It is the narrow channel of the two, because only the XDK library bodies carry a
  mangled symbol while the slot vocabulary does not need one.
- **the parent edge.** `inventory/class_table_parent_edges.csv` records the table a class's own destructor
  resets to, so a table that is a named class's `parent_va` is that class's base level, and the children
  name the ladder it heads.

<!-- generated:class-plain-tables -->
| population | store | tables | slots | the class-table prefix | named by their slots | named by the installer | neither |
|---|---|---|---|---|---|---|---|
| bounded | `vtable-slot` | 1 | 9 | 0 | 0 | 0 | 1 |
| bounded | `vtable-slot+member` | 9 | 50 | 0 | 0 | 0 | 9 |
| bounded | `member-field` | 27 | 192 | 0 | 0 | 0 | 27 |
| plain-only | `vtable-slot` | 173 | 2421 | 14 | 18 | 22 | 141 |
| plain-only | `vtable-slot+member` | 2 | 7 | 0 | 0 | 0 | 2 |
| plain-only | `member-field` | 11 | 43 | 0 | 0 | 2 | 11 |
| plain-only | `global` | 3 | 41 | 0 | 0 | 0 | 3 |
| plain-only | `frame-only` | 20 | 260 | 0 | 0 | 0 | 20 |

**The block scan's extent and the tables' own extent agree on all 246 rows: 3023 slots.** A string's first two characters would be an aligned dword inside `.text`'s address range, so this is a measurement and not a formality -- the class census ends 12 of its 492 tables on that dword, 14 dwords in all.

**14 of the 246 tables no registration names carry the class-table prefix, and the control is measured on both sides.** All 478 tables the registration path names start with the same three bodies (`0x00062650`, `0x00062660`, `0x00062670`) and none of the 49 member tables does, so the prefix separates a class's dispatch table from an interface object's table without a name. The 14 that carry it are class tables whose class no registration names; the other 232 are interfaces.

**All 14 are recorded as a parent edge, and 13 are a named class's.** `0x004d17a8` (53 slots) is the table behind `ArrayProperty`, `BoolProperty`, `ByteProperty`, `DelegateProperty`, `FixedArrayProperty` and 8 more; `0x005178d8` (32 slots) is the table behind `VertexBuffer`, `VertexStreamCOLOR`, `VertexStreamPosNormTex`, `VertexStreamUV`, `VertexStreamVECTOR`; `0x004c78c0` (31 slots) is the table behind `AudioSubsystem`, `Engine`, `Input`; `0x004eaea8` (47 slots) is the table behind `D3DRenderDevice`, `NullRenderDevice`; `0x004fcfe0` (36 slots) is the table behind `ChannelDownload`, `HTTPDownload`. Those are the tables `inventory/class_table_parent_edges.csv` records as the classes' `parent_va`, read from the destructor thunk rather than from a registration, and no shipped package declares the class of any of them -- `inventory/classes.csv` has no row -- which is why no registration site can carry its name. Where the child the edge names is itself one of the 14 (`0x004c78c0` -> `0x004d0318` -> `TcpNetDriver`), the ladder is read rung by rung; the five base levels U40 left open are five of these 14, and their parent is unnamed because the class is not in any `.u`.

**18 of the 232 interfaces are named by their own slots.** A C++ vtable holds its class's virtual methods, and the map carries masked-symbol matches against the shipped XDK libraries (`uix.lib`, `xvoice.lib`, `xonline.lib`), so a table whose slots are one class's methods carries that class: `0x0053f9ec` = `CUIPlugin` (7 of 7 slots that carry a class), `0x00540484` = `CEncoderXMO` (7 of 7 slots that carry a class), `0x005404bc` = `CEncoderXMO` (6 of 7 slots that carry a class), `0x0053f8a0` = `CPlayersFeature` (5 of 5 slots that carry a class), `0x0053f718` = `CAlertFeature` (4 of 4 slots that carry a class), `0x0053f770` = `CFeatureContext` (4 of 4 slots that carry a class), `0x0053f808` = `CVoiceMailPseudoFeature` (4 of 4 slots that carry a class), `0x0053f998` = `CLogonFeature` (4 of 4 slots that carry a class), `0x0053f6ec` = `CPopupScreen` (3 of 3 slots that carry a class), `0x0053f874` = `CPopupScreen` (3 of 3 slots that carry a class), `0x0053f90c` = `CFriendsScreen` (3 of 3 slots that carry a class), `0x0053f97c` = `CLogonScreen` (3 of 3 slots that carry a class), `0x0053f700` = `CNotificationScreen` (2 of 2 slots that carry a class), `0x0053f920` = `CFriendsFeature` (2 of 2 slots that carry a class), `0x00540464` = `CQueueXMO` (2 of 2 slots that carry a class), `0x005404f4` = `CDecoderXMO` (2 of 2 slots that carry a class), `0x00540520` = `CDecoderXMO` (2 of 3 slots that carry a class), `0x0053f990` = `CNotificationTask` (1 of 1 slots that carry a class).

**The same reading names 0 of the 49 member tables.** Their slots carry no mangled class scope at all, so the interface object a member field holds stays unnamed by this channel -- the member's *own* name comes from the field it is stored in, which `tools/class-member-tables.py` already builds.

**The installer's own symbol names a class on 24 tables, and the two channels agree on 18.** It is `0x0053f6d8` = `CFriendsScreen CLogonScreen CNotificationScreen CPlayersScreen CPopupScreen`, `0x0053f6ec` = `CPopupScreen`, `0x0053f700` = `CNotificationScreen`, `0x0053f718` = `CAlertFeature`, `0x0053f770` = `CFeatureContext`, `0x0053f7f0` = `CVoiceMailPseudoFeature`, `0x0053f808` = `CVoiceMailPseudoFeature`, `0x0053f85c` = `CVoiceMailScreen`, `0x0053f874` = `CPlayersContextScreen`, `0x0053f888` = `CPlayersFeature`, `0x0053f8a0` = `CPlayersFeature`, `0x0053f8f4` = `CPlayersScreen`, `0x0053f90c` = `CFriendsScreen`, `0x0053f920` = `CFriendsFeature`, `0x0053f97c` = `CLogonScreen`, `0x0053f990` = `CNotificationTask`, `0x0053f998` = `CLogonFeature`, `0x0053f9ec` = `CUIPlugin`, `0x00540464` = `CQueueXMO`, `0x00540484` = `CEncoderXMO`, `0x005404bc` = `COneToOneEncoder`, `0x005404f4` = `CDecoderXMO`, `0x00540520` = `COneToOneDecoder`, `0x0057534c` = `exception`, a CRT class and not one of the interfaces. A `??0<C>@@`/`??1<C>@@` symbol is a C++ constructor or destructor and names the class it builds outright; it is the narrow channel of the two, because only the XDK library bodies carry a mangled symbol, and the slot vocabulary does not need one.

**3023 slots resolve, and 815 of them sit outside the function map.** 116 slots are `__purecall` and 1172 are stubs of 8 bytes or less -- an interface's default implementation -- so the interface half repeats the shape the 49 member tables already showed, at a receiver that is the object's own slot rather than a member field.
<!-- /generated:class-plain-tables -->

**The 14 class tables are the heads of native ladders, and 13 of them are a named class's base.** A
constructor installs its parent's table before its own, so a registration site names the table of *its
own* class and an ancestor's table is named only when the ancestor has a site -- and none of these 14 has
one, which is what put them in this population. The ladders they head are named by their members:
thirteen `*Property` classes under `0x004d17a8`, `Const` under `0x004d1468`, `AudioSubsystem`/`Input`
under `0x004c78c0`, `XboxClient` under `0x004c94b0`, `XHVVoiceSubsystem` under `0x004d2400`,
`ObjectExporterT3D` and `TextBufferFactory` on their own tables, `D3DRenderDevice`/`NullRenderDevice`
under `0x004eaea8`, `Level` under `0x004fc4f0`, `NetPendingLevel`/`DemoPlayPendingLevel` under
`0x004fc588`, `ChannelDownload`/`HTTPDownload` under `0x004fcfe0`, `IndexBuffer`/`SkinVertexBuffer`
under `0x00517860` and the five vertex-stream classes under `0x005178d8`. The four tables the
registration path named in the 2026-11-21 round (`0x004c93d0` `Viewport`, `0x004d0318` `NetDriver`,
`0x004d03c8` `NetConnection`, `0x004fa390` `Channel`) have left this population and are still rungs of
it: the chain `0x004c78c0` -> `0x004d0318` -> `TcpNetDriver` is read rung by rung, and `0x004fa390`
heads the four channel classes. Read as UE2.5's own hierarchy the rungs are `UField` over
`Const`/`Enum`/`Struct`, `UProperty` under the thirteen property classes, `USubsystem` over
`AudioSubsystem`/`Input`/`NetDriver`/`RenderDevice`, `UChannel` over the four channel classes,
`ULevel` and `UPendingLevel`, `URenderDevice` and `UVertexStream`; that is a reading of the shipped hierarchy and not a measurement, because the image
holds no name string for any of them, and U40 records it as the candidate that a class record's name
field would settle.

**The 232 interfaces are the same kind of object as the 49 member tables, at a different receiver.** A
member table's interface object hangs off a field of its owner; these hang off the object's own slot and
are built by a constructor that is not a class's. The shape repeats: `__purecall` and eight-byte stubs
dominate the slots and most of the remaining bodies sit outside the function map, so an interface's
default implementation is the common case here too. Eighteen of them are named by their own slots, and every one
of those is a library class -- a UIX screen, feature or plugin, or an XMO codec -- matched from the
library's own object files, the only place in this image where a mangled symbol reaches a vtable.

**What this does not say.** It does not name the 14: no registration site names them
(`inventory/class_registrations.csv` has no row whose table is one of these) and the parent-edge ladder
is the only name the image gives them. It does not name the 214 interfaces whose slots
carry no mangled class -- their slot bodies are code no symbol names, so the channel that names the
eighteen UIX/XMO tables cannot reach them. And it does not say an unnamed interface is unused: one can be
constructed, dispatched through and freed with no symbol naming it, which is what the caller census
already shows for a third of this population.

`--check` is a synthetic self-test (a prefixed table, a prefixed table that is a parent edge, two
interfaces and a stranger; the mangled-name reader against a constructor, a method, a scalar deleting
destructor and a template; no dump, writes nothing) and `--check-doc` rebuilds the block from the
committed `inventory/class_plain_tables.csv` with no dump. Both run in `make ci`.

The interface census reads the 232 slot vectors and writes the shape, the family, the role and the object
size:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-shapes.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-shapes.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic set of slot vectors (a rung that implements the slot below leaves `__purecall`, the
reverse refusal, more than half alike, two two-slot tables that are *not* kin, a stranger) plus the role
assignment, the size columns and the twin count, and it writes nothing; `--check-doc` rebuilds its block
from the committed `inventory/class_interface_shapes.csv` with no dump. Both run in `make ci`.



## What the 18 heads' slot bodies say: four files, and the four names they earn (2026-11-20)

Three name routes for the 18 class tables no registration names (14 since the four below were applied) are measured shut: the registration
literal (the site pushes a shared default constructor), the installer body (0 of 26 carry a string at
all) and the registration's size argument (every size in the tree originates at a registration, so the
join is circular). The **runtime** route is closed too, and this is the number: `0x004c78c0` and its
seventeen siblings appear in **neither** booted read -- `inventory/live_class_tables.csv` (312 rows at
the title screen) carries 179 tables and `live_class_tables_m00.csv` (376 rows) 189, **all of them
named**. Absence alone says nothing about them, and the base rate is why: **289 of the 478 named tables
are absent from both reads**, because only a class whose default object is constructed is in the array.
So a boot can confirm a name this population already has and cannot supply one it lacks.

What is left is the slot body, and it carries something the installers do not: **the symbol map names
an unnamed body after the assert path it inlines** (`Engine_UnChan` is `...\Engine\Src\UnChan.cpp`),
so a table's slots vote for a `.cpp` file. `tools/class-slot-tokens.py` tallies that vote per table over
`inventory/xbe_class_tables.csv`'s extents (the body-start rule, so the read cannot disagree with the
artifact about which dwords are slots) and writes `inventory/class_slot_tokens.csv`, one row per table
with the token counts and every token that at most 8 of the 492 tables carry.

<!-- generated:class-slot-tokens -->
**492 of the 492 class tables carry an assert-file token on at least one slot (478 of the 478 named, 14 of the 14 unnamed).**

No unnamed table carries a token that is rare across the 492.
<!-- /generated:class-slot-tokens -->

**The four are the base of a named ladder, and the ladder is the argument -- not the token.** Each of the
four shares its slot count with every child that carries the same token, so the head is the table the
children extend, and the class the engine puts at that level is the name:

| head | slots | children carrying the same token | class the level is |
|---|---:|---|---|
| `0x004d0318` | 40 | `TcpNetDriver` 40, `TcpNetDriverXbox` 41, `DemoRecDriver` 40 | `NetDriver` |
| `0x004d03c8` | 42 | `TcpipConnection` 42, `TcpipConnectionXbox` 42, `DemoRecConnection` 42 | `NetConnection` |
| `0x004fa390` | 37 | `ControlChannel` 37, `FileChannel` 37, `VoiceChannel` 37, `ActorChannel` 37 | `Channel` |
| `0x004c93d0` | 56 | `XboxViewport` 57 | `Viewport` |

`0x004fa390` is the row `inventory/class_table_name_pairs.csv` already called a `Channel` hint from the
family anchor, and this read is an independent second instrument on it; `0x004c93d0` is the open row that
page read as `XboxViewport` before #533 gave that name to `0x004c9618`, and the token agrees with the
correction: it is the base, and `XboxViewport` adds one slot. **2026-11-21: those four names are applied.**
All four are rows of `inventory/xbe_class_table_names.csv` with `name_rule` `slot-file-token`, so the
population this section reads is 14 -- `0x004c78c0` and its thirteen remaining siblings -- and the other
ten carry nothing but the
base rate -- `UnObj`, on all 492 tables -- and the property head `0x004d17a8` (53 slots) carries `UnObj`
alone even though thirteen `*Property` classes extend it, so a token is not a property of a hierarchy's
size.

**Naming them is a rule, and it ships as `slot-file-token`.** A token is a file, and the engine's class
for a file is the one step the image does not state: the control is that only **13 of the 478 named
tables** carry a token that starts with their own class name, so the channel cannot name a class on its
own. So the file-to-class step is a documented literal (`SLOT_FILE_CLASS` in `tools/class-table-names.py`)
and the rule asks the image everything else -- and refuses the name unless all of it holds:

* the class is a **literal the build ships**: `inventory/class_registrations.csv` carries a site that
  pushes it (`NetDriver` at `0x00183599`, `NetConnection` at `0x0010d6a9`, `Channel` at `0x001083c6`,
  `Viewport` at `0x000f92c9`);
* that site installs **no table of its own** -- it pushes the shared default constructor `0x00034650`, so
  the store it reaches is the object root's, which is exactly why no site can name these tables;
* the table **carries the file**: `UnNetDrv` on 1 of `0x004d0318`'s 40 slots, `UnConn` on 4 of 42,
  `UnChan` on 4 of 37, `UnCamera` on 4 of 56;
* the **slot ladder** holds: every named table carrying the same file is at least as long (a derived
  class's table extends its base's -- 490 of 490 measured rungs, `inventory/class_ladder_rungs.csv`), and
  no *shorter* unnamed table carries the file, since that one would be the better head;
* the **size ladder** holds: every named carrier's object size is at least the class's own (100 ->
  104/104/200/628 for the channel family, 4524 -> 4564/4608/4684 for the driver, 20368 -> 20404/20424,
  456 -> 464).

A row only ever moves from empty to named, and its `evidence` column carries the literal's site, the vote
and both ladders it was named against, so an authored file-to-class step cannot quietly name a table the
image contradicts. The four rows that moved are `0x004d0318` `NetDriver`, `0x004d03c8` `NetConnection`,
`0x004fa390` `Channel` and `0x004c93d0` `Viewport`; the census goes from 474 named of 492 to **478 of
492, 14 unnamed**.

**The other fourteen are not waiting on the same rule.** They carry `UnObj` and nothing else -- the token
on all 492 tables -- so the slot route has no vote to offer them at all, and `inventory/class_slot_tokens.csv`
records the same number one level down: **0 of the 14 unnamed still carry a distinctive token**. Of the
two the earlier paragraphs followed by hand, `0x004c78c0` (the object root, 31 slots) and `0x004d17a8`
(the property head, 53 slots) are the two that would need a name from somewhere other than their slots.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-slot-tokens.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-slot-tokens.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic self-test (the module prefix against a library name, a `FUN_*` and a `thunk_*`;
the tally; the block render; no dump, writes nothing) and `--check-doc` rebuilds the block from the
committed CSV with no dump. Both run in `make ci`.

## The 232 interfaces' identity: 18 named, 49 held, 165 anonymous

The section above asks *what kind of object* a table is and answers it with the class-table prefix. The
question it leaves is *which of the 232 are the same interface*, and that one the image answers with the
vtable rather than with a name, because no symbol or string in the image names most of these classes.
`tools/class-interface-shapes.py` reads the 232 slot vectors (`inventory/class_interface_shapes.csv`, 232
rows) and applies `tools/class-member-tables.py`'s kin rule to them unchanged -- identical vectors, one
vector *subsuming* another of the same length, or two vectors of four slots or more agreeing on more than
half -- so a family here means exactly what a family means there.

**The 49 member tables are 49 of these 232, not a neighbouring population.** Every `table_va` in
`inventory/class_member_tables.csv` is a row of `inventory/class_plain_tables.csv` (49 of 49), so "the 232
interfaces" already *are* the member tables plus the plain ones; what separates the two is the **store** --
a member field of a class's object, or the object's own slot -- and not the table. That matters because a
member's *type* is then a question this population can answer without a class name: the field names the
interface, and the family says how many owners hold that shape (`LinkerLoad,LinkerSave@0xb8` is one table
two classes hold at the same offset). The earlier sentence that the slot-symbol reading "names 0 of the 49
member tables" is a statement about that *channel* (1 of the 49's 292 slots carries a mangled class scope,
and one slot cannot name a table), not about the tables being a separate population.

<!-- generated:class-interface-shapes -->
| role | tables | slots | with an exact twin | in a family of two or more | alone |
|---|---|---|---|---|---|
| named | 18 | 225 | 2 | 10 | 8 |
| member | 49 | 292 | 15 | 27 | 22 |
| anonymous | 165 | 2002 | 10 | 100 | 65 |
| **all** | **232** | **2519** | **27** | **137** | **95** |

**232 table(s) over 2519 slot(s) are 131 family(ies); 27 table(s) have an exact twin and 95 stand alone.** The largest family holds **33** tables of **22** slots.

| family | tables | slots | roles | names | members (up to 12) |
|---|---|---|---|---|---|
| `0x004c7860` | 33 | 22 | anonymous,member | LinkerLoad,LinkerLoad,LinkerSave,LinkerLoadGame,LinkerSave,LinkerSaveGame | 0x004c7860 0x004c8410 0x004c84d0 0x004c9038 0x004c9738 0x004c9858 0x004c9a98 0x004c9b50 0x004d0de8 0x004d3118 0x004d3248 0x004d3ba0 |
| `0x004e3520` | 10 | 9 | anonymous | - | 0x004e3520 0x004f7690 0x00507bb8 0x0051707c 0x0051d238 0x0051d300 0x0051d7c4 0x005207a4 0x0052086c 0x00523394 |
| `0x0053d590` | 7 | 4 | anonymous | - | 0x0053d590 0x0053d5c8 0x0053d5fc 0x0053d630 0x0053d70c 0x0053d768 0x0053d7a0 |
| `0x004fc35c` | 6 | 8 | member | DemoPlayPendingLevel,Level,NetPendingLevel,unowned | 0x004fc35c 0x004fc4cc 0x004fc568 0x004fc610 0x0050e88c 0x005139f4 |
| `0x005170f4` | 5 | 15 | anonymous | - | 0x005170f4 0x00517134 0x005171b4 0x0051a27c 0x0051a9e4 |
| `0x004d03b8` | 4 | 1 | member | DemoRecConnection,PackageMapLevel,TcpipConnection,TcpipConnectionXbox | 0x004d03b8 0x004d0470 0x004d05d0 0x004fcc08 |
| `0x004d03bc` | 4 | 2 | member | DemoRecConnection,PackageMapLevel,TcpipConnection,TcpipConnectionXbox | 0x004d03bc 0x004d0474 0x004d05d4 0x004fcc0c |
| `0x00507c7c` | 4 | 5 | anonymous,member | unowned | 0x00507c7c 0x0051724c 0x00517430 0x0051f6ec |
| `0x00516fd4` | 4 | 14 | anonymous | - | 0x00516fd4 0x0051700c 0x00517044 0x005170a8 |
| `0x0053f718` | 4 | 21 | named | CAlertFeature,CFriendsFeature,CLogonFeature,CPlayersFeature | 0x0053f718 0x0053f8a0 0x0053f920 0x0053f998 |
| `0x00510f78` | 3 | 18 | anonymous | - | 0x00510f78 0x005174a8 0x00517588 |
| `0x0052d3e8` | 3 | 25 | anonymous | - | 0x0052d3e8 0x0052e0c8 0x0052f1c0 |

| object size (the installer's allocation) | tables | roles |
|---|---|---|
| 0x30 | 3 | anonymous |
| 0x58 | 3 | anonymous,member |
| 0x2c | 2 | anonymous |
| 0x38 | 2 | anonymous |
| 0x4f90 | 2 | member |
| 0x4fb4 | 2 | member |
| 0x4fc8 | 2 | member |
| 0xb0 | 2 | member |
| no size read | 199 | anonymous,member,named |
<!-- /generated:class-interface-shapes -->

**A shape is the identity, and 27 tables are exact copies.** The 232 tables carry **216** distinct slot
vectors: **27** of them repeat another's vector exactly, so the population is 232 *tables* and 216
*shapes*. The repeat is spread across the roles (2 of the 18 named tables, 15 of the 49 member tables and
10 of the 165 anonymous ones). *The 27 exact repeats are per-class, not per-translation-unit* below reads
where each repeat sits and which class owns it -- and a per-translation-unit copy of one class's table is
the answer for one group of the eleven, not for the population.

**131 families, and the largest is one interface implemented 33 times.** The kin rule leaves **95** tables
alone and joins **137** into families of two or more. The largest family holds **31** tables of 22 slots,
and its named members are the four `Linker` classes -- `LinkerLoad`, `LinkerSave`, `LinkerLoadGame` and
`LinkerSaveGame`, each holding it in a field at `0xb8` -- plus 27 anonymous tables whose objects are of very
different sizes (`0x58`, `0x84`, `0x86c`, `0x8064`), i.e. one archive-style interface that a file archive, a
memory archive and a save archive each implement. The second-largest holds **10** tables of 9 slots, three
of them exact twins, and two of its members (`0x0051d300`, `0x0052086c`) are the tables U40 already records
as the base table `SparkEmitter`, `SpriteEmitter` and `TrailEmitter` install, so that family is the emitter
hierarchy's interface. Neither reading is a symbol the image carries: they are readings of the hierarchy,
labelled as readings and not written into the artifact as names.

**The roles are the only names, they are disjoint, and they do not propagate.** 18 tables are named by the
class scope of their own slots' masked symbols, 49 by the class and field that hold them, and **165 carry
neither**. The 18 and the 49 do not overlap, and **no family holds both a named table and an unnamed one**
(0 of the 132), so the slot-symbol names do not reach down a family: a family is a unit of work, not a
name. **33** tables also carry an object size, read from the installer's allocation immediate or from the
class registry when the class registers one; the other **199** are allocated on a stack frame, through a
global, or by an allocation whose size operand is not an immediate, so a size is a bonus identity channel
rather than a census.

**What this does not say.** It does not name the 165, and it cannot: the family says which tables are one
interface and the role says where a name already exists, but neither invents a class name the image never
stores. What it does say is that naming them is not 165 separate questions -- **100** of the 165 sit in a
family with another table, and one representative per family is what a per-body read would owe, with the
lone **65** as the residue that read has to carry.

`--check` is a synthetic self-test (a rung that implements the slot below leaves pure, the reverse refusal,
more than half alike, two two-slot tables that are *not* kin, a stranger; the role of a named, a member and
an anonymous table; a size column and an allocation immediate; the twin count) and writes nothing;
`--check-doc` rebuilds the block from the committed `inventory/class_interface_shapes.csv` with no dump.
Both run in `make ci`.

## Regenerating

The other gaps of the symbol map have their own tool, reading the same image and the same map:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/native-island-gaps.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/native-island-gaps.py --write-doc docs/systems/CLASS_REGISTRATION.md


    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/native-unreachable.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/native-unreachable.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic self-test (no dump, writes nothing) and `--check-doc` rebuilds the block
above from the committed `inventory/native_unreachable.csv`; both run in `make ci`.

The base-call reading has its own tool and artifact:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-ctor-base.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-ctor-base.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a self-test over synthetic bodies plus an artifact round trip, and `--check-doc` rebuilds
its block from the committed `inventory/class_ctor_bases.csv` with no dump; both run in `make ci`.

The naming census reads those bodies and writes the artifact named in the heading above:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-table-names.py \
        "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-table-names.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic image -- an unshared thunk winning over a shared one, a shared group falling back
to its ancestor, a two-store body whose last store is its own, a guarded stub followed through its `call`, an
`int3`-bounded body that must not inherit its neighbour's store, and the five refusals of the
`slot-file-token` rule -- and `--check-doc` rebuilds the block from the committed artifact; both run in
`make ci`. **This census reads the slot-token tallies, so the order is the other tool first:** the truth is
names -> `make class-slot-tokens` -> names again, and a run that regenerates the naming census alone reads
the previous run's vote.

The registration census is the same shape, and it is the capstone-free half of `tools/class-table-names.py`:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-registrations.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-registrations.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic image -- a two-store body named by its last store, an `int3`-bounded body that must
not inherit its neighbour's table, and a guarded stub followed through its `call` -- and `--check-doc`
rebuilds the block above from the committed artifact; both run in `make ci`.

The size column's provenance has its own instrument, and it reads committed CSVs only -- no dump, no image:

    python3 tools/class-size-sources.py -o inventory
    python3 tools/class-size-sources.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` joins a synthetic set of the five CSVs (a copied column that agrees, one that disagrees, and a
table no registration reaches) and writes nothing; `--check-doc` rebuilds its block. Both run in `make ci`.

The 24 tables no registration reaches have their own instrument, and it is the store sweep filtered to a
population rather than a new pass over the image:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-orphan-tables.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-orphan-tables.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic image -- a `C7 /0` store with a displacement, a SIB form that must be skipped, a
caller of each kind, a tail jump, and a store outside every body -- and `--check-doc` rebuilds its block
from `inventory/class_orphan_tables.csv` with no dump. Both run in `make ci`.

The store-field partition is the same population read one operand deeper:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-table-store-fields.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-table-store-fields.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic image -- a slot store, a `disp8` and a `disp32` member store, a stack store, a
global, an indexed SIB and a table no store carries -- and `--check-doc` rebuilds its block from
`inventory/class_table_store_fields.csv` with no dump; both run in `make ci`.

The constructor evidence is one pass over the same bodies, and its block above is rebuilt from
the committed CSV with no dump:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-ctor-evidence.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-ctor-evidence.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic image -- a UTF-16 literal that must decode, an ASCII blob and a small integer that
must not, an `ini:` section, a call whose target resolves through the map, a thunk that reaches the parent
constructor, and a body installing two tables that must not yield an edge -- and `--check-doc` rebuilds the
block from the committed CSV. Both run in `make ci`.

The member tables' slots are the same population read one level deeper still:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-member-tables.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-member-tables.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic set of slot vectors -- a `__purecall` rung, the rung above it, a table that only
agrees on half its slots and a stranger -- and `--check-doc` rebuilds its block from
`inventory/class_member_tables.csv` with no dump; both run in `make ci`.

The pointer every store goes through is read from the same sites, one operand in and one caller out:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-store-provenance.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-store-provenance.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is a synthetic image -- a thiscall prologue, a member load, a nested member, a frame slot, a
global load, a `call`'s return, four call-site `ecx` forms, the ten-byte absolute store and five
register-store pairs -- and `--check-doc` rebuilds its block from `inventory/class_store_provenance.csv`
with no dump; both run in `make ci`.

The deeper read of that population -- the arithmetic receiver, the temporary's size and the channel a
callerless installer has -- is its own tool over the same sites:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-receiver-reads.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-receiver-reads.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is 32 assertions on synthetic instructions -- the `TArray` growth shape, a `shl`
stride, a scaled `lea`, a fixed object, a pointer field, a nested load, a pushed allocation
argument, the eleven-byte `disp32` frame store, a tail jump, a dword pointer and a table slot
-- and it writes nothing; `--check-doc` rebuilds its block from the committed
`inventory/class_receiver_reads.csv` with no dump. Both run in `make ci`.

## What the per-body read owes: 88 representatives, not 165

The shape census left the naming question with a bound -- *one representative per family is what a per-body
read would owe* -- and this is that bound as a list. `tools/class-interface-families.py` reads the committed
shape census and writes one row per family (`inventory/class_interface_families.csv`, 131 rows): the
representative a read would start from, the role that names it if anything does, the installer and caller
counts already measured for it, and whether the family is owed a read at all.

<!-- generated:class-interface-families -->
| representative role | families | tables held | per-body read owed |
|---|---|---|---|
| named | 12 | 18 | no |
| member-held | 31 | 81 | no |
| anonymous | 88 | 133 | yes |

**131 families, and 88 of them have no named and no member-held table: the per-body read is 88 representative reads over 133 tables, not one read per anonymous table.** The other 43 families already carry a name -- 12 from a slot's masked class scope, 31 from the member field that holds them -- and owe no read at all.

**32 of the 165 anonymous tables sit in a family the census would read once**, and the remaining 133 are members of the 88 anonymous-only families: the family is a unit of work, not a name, and one representative per unpaid family is the whole residue.
<!-- /generated:class-interface-families -->

The read is owed where *no* member carries a name, and the number is smaller than the table count by the
families that already answer it: the representative is a formality for the named and member-held families,
and for the 88 anonymous ones it is the class whose name a per-body read would have to earn. That read is
now costed in the section below: **55 of the 88 have a mapped body, 26 own only a gap head U40 already
decodes, and 7 are the compiler's defaults**, so the list is 55 reads and not 88 -- and the same 257
distinct bodies are U40's slot-named half, which makes the interface population and the map's gap work
one list rather than two.

## The channel that named four class tables is measured shut for the interfaces

The `slot-file-token` rule above reads a table's slot bodies for the assert file they inline. The interfaces
are the next population that owes a read, so the same tally runs over `inventory/class_plain_tables.csv`'s
232 interface tables (`tools/class-interface-tokens.py`, `inventory/class_interface_tokens.csv`), read at the
extents that census measured.

<!-- generated:class-interface-tokens -->
| token (the `.cpp` its slots assert in) | tables | slots | shape families |
|---|---:|---:|---:|
| `UnTemplate` | 4 | 4 | 2 |
| `XboxLiveMatchMaking` | 4 | 4 | 4 |
| `InternetLink` | 3 | 6 | 1 |
| `UnPenLev` | 3 | 4 | 1 |
| `UnRender` | 3 | 3 | 1 |
| `UnLinker` | 2 | 2 | 1 |
| `UnRenderUtil` | 2 | 2 | 2 |
| `UnLevel` | 1 | 4 | 1 |
| `UnRenderPrivate` | 1 | 4 | 1 |
| `D3DRenderInterface` | 1 | 3 | 1 |
| `UnActCol` | 1 | 3 | 1 |
| `UnOctree` | 1 | 2 | 1 |

**25 of the 232 interface tables carry an assert-file token on at least one slot (0 of the 23 named, 25 of the 209 unnamed), over 2519 slots in all** -- and **25 carry one no more than 8 of the 232 carry**, of which 25 are unnamed.

**7 tokens are carried by more than one table, and 4 of them sit in exactly one shape family** (`InternetLink`, `UnLinker`, `UnPenLev`, `UnRender`) -- a second, independent vote for the grouping `class-interface-shapes.py` made from the slot vectors. **3 cross families** (`UnRenderUtil`, `UnTemplate`, `XboxLiveMatchMaking`), which is what an inherited slot looks like rather than a family signature.

**The channel that named four class tables names nothing here, and the reason is the population.** An interface's slots are the compiler's defaults (111 stubs of 8 bytes or less, 24 `__purecall`; class-table census) and the shipped XDK libraries' methods, whose names are MSVC-mangled rather than `<Module>_<File>`, so the vote is 45 of 2519 slots; 6 of the 25 tables that do carry a token carry only tokens a *class* table already has, i.e. a slot inherited from an engine base class rather than a slot that describes the interface. So the representative reads cannot be paid for with this channel, and an interface's name has to come from its slots' behaviour or from a member field that holds it.
<!-- /generated:class-interface-tokens -->

**What the vote does buy is a second opinion on the families.** Seven tokens are shared by more than one
table and **four of them sit in exactly one shape family** (`InternetLink`, `UnLinker`, `UnPenLev`,
`UnRender`) -- an independent vote for the grouping the shape census made from the slot vectors -- while
three cross families (`UnRenderUtil`, `UnTemplate`, `XboxLiveMatchMaking`), which is what a slot inherited
from an engine base looks like rather than a family signature. **19 of the 25 tables carry a file no class
table carries** (`XboxLiveMatchMaking` on 4, `D3DRenderInterface`, `UnLevel`, `UnActCol`, `UnTex`, ...), so
the vote does say which module emitted the slot -- a module label, not a class name, and the image has no
registration literal for those classes. **That is why the 88 representative reads stay owed**: they are
behaviour reads, and the fragment's own question -- *which interface is this* -- has to be answered from
what a representative's slots do, or from a member field that holds it.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-tokens.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-tokens.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is synthetic (a slot whose body asserts in a file, a library method's mangled name, the document
frequency, the class-table control, the block and the artifact round trip; no dump, writes nothing) and
`--check-doc` rebuilds the block from the committed CSV. Both run in `make ci`.

## What the 88 representatives hold: 55 bodies, 26 gap heads and 7 defaults

The families census bound the per-body read at 88 representatives and the token census measured the
interface side of the assert-file channel shut; neither said how much of that list has a body to read.
`tools/class-interface-behaviour.py` reads every slot of the 232 interface tables three ways -- the body
census (a purecall, a stub of 8 bytes or less, a body with a map row, a body without one), the printable
UTF-16 literals a body pushes, and the direct `E8` calls it makes -- so the read's size is a measurement
before it is a question.

<!-- generated:class-interface-behaviour -->
| what the slot bodies are | tables | slots |
|---|---|---|
| `callee-file` | 40 | 524 |
| `assert-only` | 13 | 172 |
| `mapped-no-name` | 112 | 1252 |
| `unmapped-only` | 48 | 485 |
| `no-body` | 19 | 86 |

**165 of the 232 interface tables hold at least one body the map decodes (656 slots), 151 call something, and 40 name the file they call; 48 tables' only body is an undecoded gap head and 19 hold no body at all.** The 342 interface slots outside the function map are 342 `tools/gap-heads.py` heads over 257 distinct bodies, so they are U40's work and not a fourth channel: 213 of the 257 sit in exactly one table and 44 in two or more -- and of the shared ones 42 sit at the same slot index in both tables and 2 at different indices, so the twin families share bodies and an add would have to name one body from two tables for those.

**The 2 cross-index heads `0x000bd360` at `0x004eaf68` slot 7 and `0x0052d3e8` slot 20 and `0x0052e0c8` slot 20 and `0x0052f1c0` slot 20; `0x000bd380` at `0x004eaf68` slot 6 and `0x004eaf68` slot 20 and `0x0051a810` slot 20 and `0x0052ef14` slot 6** -- one body two tables place at different slots, which is an interface implemented at a different offset rather than two interfaces, so the row that names it has to carry both indices.

| the 88 representatives | families |
|---|---|
| `callee-file` | 10 |
| `assert-only` | 9 |
| `mapped-no-name` | 36 |
| `unmapped-only` | 26 |
| `no-body` | 7 |

**Of the 88 representative reads the families census owes, only 55 have a mapped body to read; 26 more own nothing but an undecoded gap head and 7 are the compiler's defaults (purecalls and stubs) and have no read at all.** 10 of the mapped ones carry a callee file.

| representative | slots | the file its slots call | callee class | the slot's own token | agree |
|---|---|---|---|---|---|
| `0x004c8db8` | 25 | UnTemplate:21 | - | - | one-side |
| `0x004d0ec8` | 21 | UnTemplate:2 | - | - | one-side |
| `0x00510f78` | 18 | UnCache:2 UnRenderLight:2 | - | - | one-side |
| `0x005124a4` | 12 | UnTemplate:2 | - | UnOctree | no |
| `0x004f7070` | 12 | UnTrace:1 | - | - | one-side |
| `0x00516fd4` | 14 | UnActor:2 UnMaterial:2 UnObjBas:2 UnFluidSurface:1 UnMath:1 UnParticleSystem:1 | - | - | one-side |
| `0x005170f4` | 15 | UnActor:2 UnMaterial:2 UnObjBas:2 UnFluidSurface:1 UnParticleSystem:1 | - | - | one-side |
| `0x0052bbf8` | 65 | D3DRenderState:1 D3DResource:1 | - | D3DRenderInterface | no |
| `0x0052ef14` | 8 | UnTemplate:1 | - | - | one-side |
| `0x0053d58c` | 1 | UnTemplate:1 XboxLiveMatchMaking:1 | - | - | one-side |

**The callee file is the helper's file, not the interface's.** 14 tables carry both a slot token and a callee file: **0 agree and 14 disagree**, and 37 carry only one of the two. A slot token is the file the interface's own method was compiled from and a callee file is the file of the code that method calls, so the call target labels what a slot does -- a template instantiation, a class helper, zlib -- and cannot stand in for the interface's name.

**52 tables' slot bodies push a printable literal, and none of them is a source path.** The strings name what the slot does rather than the file it came from (`0x004c8410` zlib: uncompress failed; `0x004c84d0` ReadFile failed: Count=%i BufferCount=%i Error=%s; `0x004c85b0` ReaderSaveLinear; `0x004c8db8` D:\; `0x004c93c4` ISFULLSCREEN; `0x004c9858` Core), so a literal labels a slot's subsystem where the map has no name for its body, and the `<Module>_<File>` token is not among the pushed immediates.
<!-- /generated:class-interface-behaviour -->

**55 of the 88 representatives have a mapped body, and that is the whole per-body read.** The other 33
split by what they own instead: **26** hold no mapped body at all but do hold a body
`tools/gap-heads.py` already decodes -- U40's slot-named gap heads, so they are an add on the map's
existing route rather than a read of this census -- and **7** are the compiler's defaults, purecalls and
stubs with no body anywhere, which owe no read and cannot be named from behaviour they do not have.
Nineteen of the 232 tables are in that no-body class and 48 are unmapped-only, so the representative
list is representative of the table list: the same three populations in the same proportions.

**The call target labels what a slot does, not which interface it is.** 40 tables' slots call a body the
map names with a `<Module>_<File>` symbol, so the callee's file is readable, and 11 representatives carry
one -- but **of the 14 tables that carry both a callee file and the slot's own assert token, 0 agree and
14 disagree**. A slot token is the file the interface's own method was compiled from; a callee file is
the file of the code that method calls, which on this population is a template instantiation
(`UnTemplate` on 7 representatives), a class helper (`UnClass`) or a library (`zlib`, `D3DResource`). It
is a behaviour label and the page's earlier reading of it as a "second witness" was wrong: the second
witness for an interface's *name* has to come from a member field or a caller, not from a call target.

**The literals name a subsystem and never a source path.** 52 tables' slot bodies push a printable
UTF-16 literal and **none of them ends in `.cpp` or `.h`** -- `zlib: uncompress failed`,
`[FCollisionHash::RemoveActor] IterateCount Over 10000!`, `ISFULLSCREEN`, `ReaderSaveLinear`. The
`<Module>_<File>` symbol the map carries is therefore not the pushed assert path of this build, and the
literal channel labels a slot's subsystem (`CollisionHash`, `D3D`, `zlib`, the package writer) where the
map has no name for its body; it cannot name an interface either.

**Where this leaves the naming question.** The 55 mapped representatives are now an index rather than a
blank: each row carries its slot mix, its callees and its literals, so a later read can start from the
`UnTemplate`/`UnClass`/`D3D*` groups instead of re-deriving them. What the artifact does not do is name
one, and this measurement says why: an interface's own file is written in no channel its slots carry --
not in the token that 25 of 232 tables have, not in what they call, and not in what they push. The
remaining name channels are the ones already measured for other populations: a member field that holds
the object (which is how the 49 member tables are named) and a caller whose class is known.

The census reads every slot of the population, so its `census` row holds the totals and the distinct
count of gap-head bodies the slots hold:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-behaviour.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-behaviour.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is synthetic (the five categories, the two file witnesses and their four agreement answers,
the source-path pattern, and the artifact round trip; no dump, writes nothing) and `--check-doc` rebuilds
the block from the committed CSV with no dump. Both run in `make ci`.

## Who builds an interface object: a constructor, a constructor's helper, or neither

The family census left 88 representatives owed a read, the behaviour census priced that read and measured
the two channels a slot carries, and neither named an interface -- because a slot's assert file is the file
the interface's *own* method was compiled from and its callee's file is the file of the code that method
*calls*. The page named the two channels that were left: **a member field that holds the object** and **a
caller whose class is known**. This reads both, from the store sites `inventory/class_store_provenance.csv`
already measured, and it answers a different question than a name.

The chain is three joins and no new scan. A store site falls inside a body, using the map's own extents; a
body that also installs a class table is that class's constructor
(`inventory/class_constructors.csv`, built from the class-table store, and its `form` column separates a
`ctor` from a `dtor`); so a table stored by a constructor is stored inside a named class. Where it is not,
the read goes one call out -- which constructor bodies call the body that holds the store -- and the
register the constructor loads before that call says where the object goes. A slot body called only from a
destructor is reported apart, because a destructor resets a member's vtable on the teardown path rather
than building it.

<!-- generated:class-interface-installers -->
| verdict | tables | anonymous | of the 88 representatives | what it means |
|---|---|---|---|---|
| `ctor-holder` | 36 | 1 | 0 | the store itself sits in a body that installs a class table |
| `ctor-caller` | 19 | 11 | 7 | the store body is called by a body that installs a class table, and a ctor is one |
| `dtor-caller` | 1 | 0 | 0 | only destructors call the store body: the teardown path, not the build |
| `unnamed-holder` | 172 | 149 | 77 | neither the store body nor any caller installs a class table |
| `no-site` | 4 | 4 | 4 | the provenance census names no store site |

**36 of the 232 interface tables are stored inside a class constructor and 35 of those are the member-held tables the field already names, so that channel is the member-field channel read end to end; 19 more are stored by a body a constructor calls, 11 of them anonymous and 7 owed a per-body read.**

| table | slots | how it is stored | the class that builds it | what the constructor puts in `ecx` |
|---|---|---|---|---|
| `0x0052bbf8` (owed) | 65 | `ctor-caller` | D3DRenderDevice | this+0x4754 |
| `0x004c9038` | 22 | `ctor-caller` | PackageMapLevel | this+0x294 |
| `0x004c9738` | 22 | `ctor-caller` | PackageMapLevel | ? |
| `0x0051ffd8` (owed) | 18 | `ctor-caller` | Texture | member(eax) |
| `0x00520248` (owed) | 15 | `ctor-caller` | Cubemap | member(eax) |
| `0x0051bc20` (owed) | 11 | `ctor-caller` | VertMesh | this+0x184 |
| `0x005181a8` (owed) | 9 | `ctor-caller` | StaticMesh, StaticMeshInstance | member(ebx) this+0x34 |
| `0x0051bc4c` (owed) | 9 | `ctor-caller` | SkeletalMeshInstance | this+0x7c |
| `0x0051d7c4` | 9 | `ctor-caller` | StaticMesh | this+0x60 |
| `0x0051d80c` | 9 | `ctor-caller` | StaticMesh | this+0xc0 |
| `0x0051d830` | 9 | `ctor-holder` | StaticMesh | - |
| `0x00518104` (owed) | 7 | `ctor-caller` | StaticMesh, TerrainSector | this+0x94 this+0xdc this+0xf8 |
<!-- /generated:class-interface-installers -->

**The ctor-holder channel is the member-field channel, and that is its control rather than its finding.**
`tools/class-member-tables.py` names a member table from the field that holds it, and the field is written
by the owner's constructor -- so the channel this read adds *is* that one, read from the code side. It
returns 36 tables, and **35 of them are the member tables the field already names**; the one anonymous table
it reaches is `0x0051d830` (9 slots) in StaticMesh's constructor, the second member of family
`0x005181a8`. A channel that returns the population it was derived from and one stranger is a control, not
a naming route.

**`vtable-slot` and `member-field` are one event read at two operands, and the split leaks through the
compiler's inlining.** The store-field census decides a table's receiver by the store's operand: `[reg]` is
the object's own slot, `[reg+disp]` a member. When the compiler inlines the member's constructor into a
helper, the helper is handed `this+off` in `ecx` and stores `[ecx]` -- an object-root operand for a member
store. **11 anonymous tables are member stores read this way**, and the offsets are explicit in the
constructor: `this+0x4754` for `0x0052bbf8` (65 slots, D3DRenderDevice), `+0x184` for `0x0051bc20` (11,
VertMesh), `+0x7c` for `0x0051bc4c` (9, SkeletalMeshInstance), `+0x34` for `0x005181a8` (9, StaticMesh and
StaticMeshInstance), and `+0x94`/`+0xdc`/`+0xf8` for `0x00518104` (7, StaticMesh and TerrainSector). The
operand split is still a measurement -- it is the compiler's choice of where to write, not a guess -- but
it is a measurement of the write, and one inlined helper moves a member store into the root population.

**An offset is not an identity: `0x00518104` is stored at three offsets in one class.** The 7-slot table
goes in at `+0x94`, `+0xdc` and `+0xf8`, all with the constructor's own `this`, so far from being one
member it is the same interface embedded three times in the object (and, read from StaticMesh and
TerrainSector, in two classes). The `D3DRenderDevice` row is the opposite kind: one offset, one class, one
table -- and at 65 slots it is the largest anonymous interface in the population.

**Two of the eleven are built on a member rather than on the object: `0x0051ffd8` (18, Texture) and
`0x00520248` (15, Cubemap) get `[eax+disp]` in `ecx`, not `this`.** The class's constructor is in the call
chain, but the object it hands the helper is a nested object the constructor fetched from `this`; the
table's owner is that nested object's class, not `Texture`. This is the same distinction
`class_member_tables.py` records as `layout=ancestor` against `layout=coincident`, read from the argument
instead of the field.

**The family is the unit the class names, and one family proves it.** Family `0x005181a8` (9 slots) has
two tables and both are StaticMesh's: `0x005181a8` stored through the constructor's helper with
`this+0x34`, and `0x0051d830` stored by the constructor itself. A family whose every member's builder is
one class is a family that class can name, and the two operands it was stored with are the compiler's
inlining decision rather than two different owners.

**What this does not say.** It does not name an interface. A class and an offset name the *field*, in the
convention `class_member_tables.csv` already uses (`Linker_member_0xb8`), and the interface implemented at
that field stays anonymous; the interface's own name is not in the image's symbols, strings or call graph
that this thread has read. The 149 anonymous tables that stay `unnamed-holder` are built by bodies no
class constructor reaches -- library or static construction -- and they are the population a per-body read
would still owe if a name is to come from code. Four anonymous tables (`0x004f70a0`, `0x005172ec`,
`0x005181dc`, `0x0053f7c4`) have no store site in the provenance census at all, which is that census's
extent rule reaching for a store the locator did not find, not four tables without an installer.

## The 88 representatives read one slot at a time: 915 slots, 142 bodies, 16 shared

The representatives the family census owed were costed by the behaviour census as 56 with a mapped body,
26 with a gap head and 7 compiler defaults. That cost is a table-level answer; the read itself is per slot,
and it is the artifact behind the second block: every slot of all 88 representatives, classified by what it
holds, with each mapped body's name, size, callees, first literal and the other representatives that hold
it.

<!-- generated:class-interface-rep-bodies -->
| what a representative's slot holds | slots | distinct bodies |
|---|---|---|
| `mapped` | 165 | 142 |
| `gap` | 161 | 150 |
| `outside` | 262 | 235 |
| `stub` | 289 | 26 |
| `pure` | 38 | 1 |

**The 88 owed representatives hold 915 slots: 165 mapped over 142 distinct bodies, 161 U40 gap heads, 262 outside the map, 289 stubs and 38 purecalls; 33 mapped slots carry a name the map holds and 16 bodies are held by two or more representatives.**

| body | name | representatives that hold it |
|---|---|---|
| `0x00016f00` | FUN_00016f00 | `0x004d2e58` `0x0052d7d0` |
| `0x00016f30` | FUN_00016f30 | `0x004d2e58` `0x0052d7d0` |
| `0x00016f40` | FUN_00016f40 | `0x004d2e58` `0x0052d7d0` |
| `0x00018b90` | FUN_00018b90 | `0x004c85b0` `0x004c8db8` |
| `0x00019240` | FUN_00019240 | `0x004d2e58` `0x0052d7d0` |
| `0x0001b230` | FUN_0001b230 | `0x004d2e58` `0x0052d7d0` |
| `0x00034240` | FUN_00034240 | `0x004c85b0` `0x004c8db8` |
| `0x0008a940` | FUN_0008a940 | `0x004e3520` `0x00517214` `0x00523340` |
| `0x001d5510` | FUN_001d5510 | `0x00516fd4` `0x005170f4` |
| `0x001d6040` | FUN_001d6040 | `0x00516fd4` `0x005170f4` |
| `0x001d63a0` | FUN_001d63a0 | `0x00516fd4` `0x005170f4` |
| `0x002c3db0` | FUN_002c3db0 | `0x0052cefc` `0x0052d3e8` `0x0052ef14` |
| `0x002c4f10` | FUdpLink_BindPort | `0x0052cefc` `0x0052ef14` |
| `0x002c50b0` | FUN_002c50b0 | `0x0052cefc` `0x0052ef14` |
| `0x00329c70` | FUN_00329c70 | `0x0053d590` `0x0053d73c` `0x0053d7fc` `0x0053e3dc` `0x0053ec54` |
| `0x0032a490` | FUN_0032a490 | `0x0053d590` `0x0053d660` `0x0053d6fc` `0x0053d73c` |
<!-- /generated:class-interface-rep-bodies -->

**Three things follow that the table-level census could not say.** First, **the 175 mapped slots are 146
distinct bodies and 33 of the slots carry a name the map holds -- every one of them a library or engine
file, never a source path**: `Engine_UnTex`, `Engine_UnOctree`, `Engine_UnRenderLight`, `Engine_UnActCol`,
`D3DDrv_D3DRenderInterface`, `IpDrv_InternetLink`, `Core_UnTemplate`, `StatsReadTask_Finished`,
`XboxLive_XboxLiveMatchMaking`, and the UIX mangled names the 18 named tables already carry. A slot body
the map names is named after the file it was compiled from and the file is always a library, so the read
reproduces the token census's verdict at the body instead of at the table. Second, **the 302 stub slots are
26 distinct bodies, and one of them fills 116**: an interface's default implementation is a handful of
shared functions, not one stub per slot, which is what makes an interface's slot vector readable as
"mostly defaults plus a few real bodies". Third, **the sharing is not pairwise noise: 17 bodies sit in 6
clusters of representatives** -- `0x004d2e58`/`0x0051ec98`/`0x0052d7d0` share five bodies,
`0x00516fd4`/`0x005170f4` three, `0x0052cefc`/`0x0052d3e8`/`0x0052ef14` three, and
`0x0053d590`/`0x0053d660`/`0x0053d6fc`/`0x0053d73c`/`0x0053d7fc`/`0x0053e3dc`/`0x0053ec54` two -- so two
anonymous interfaces can be two instantiations of one implementation, which is the twins census's finding
for the gap heads read on the mapped bodies instead.

The census row of the slot artifact carries the totals, and the block above is rebuilt from it with no
dump:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-installers.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-installers.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is synthetic (the map-extent lookup on a body's last byte and the hole between two bodies, the
store-site parse, the five installer verdicts including the dtor-only edge, the `ecx` placement at a call,
the five slot kinds, and both artifact round trips; no dump, writes nothing) and `--check-doc` rebuilds
both blocks from the committed CSVs with no dump. Both run in `make ci`.

## The 27 exact repeats are per-class, not per-translation-unit

`inventory/class_interface_twins.csv` groups the 232 tables by shape and records each group's members, the
span they occupy, and the class each member's slot symbol or member field names. A repeat is not an
anonymous duplication of one table: the group is a set of classes that each emit the same interface, which
is what a C++ compiler writes for four classes embedding one two-slot interface at one offset.

<!-- generated:class-interface-twins -->
| shape | tables | slots | members | span | roles | classes |
|---|---|---|---|---|---|---|
| `0ffbe2d3` | 4 | 1 | 0x004d03b8 0x004d0470 0x004d05d0 0x004fcc08 | 0x2c850 | member 4 | DemoRecConnection,PackageMapLevel,TcpipConnection,TcpipConnectionXbox |
| `4055b423` | 4 | 2 | 0x004d03bc 0x004d0474 0x004d05d4 0x004fcc0c | 0x2c850 | member 4 | DemoRecConnection,PackageMapLevel,TcpipConnection,TcpipConnectionXbox |
| `cacf26f7` | 3 | 9 | 0x004f7690 0x0051d238 0x005207a4 | 0x29114 | anonymous 3 | - |
| `ba02af46` | 2 | 2 | 0x004c7854 0x004c933c | 0x1ae8 | member 2 | ControlChannel,TextBuffer |
| `e574845e` | 2 | 1 | 0x004c78b8 0x0053f978 | 0x780c0 | anonymous 1 member 1 | - |
| `67c859fa` | 2 | 2 | 0x004c93c8 0x004c9610 | 0x248 | member 2 | XboxViewport |
| `f64e27b4` | 2 | 1 | 0x004d0314 0x004d0528 | 0x214 | member 2 | TcpNetDriver |
| `e0d62302` | 2 | 2 | 0x004d1430 0x004d1438 | 0x8 | anonymous 2 | - |
| `d0e78346` | 2 | 9 | 0x00510e90 0x0051d80c | 0xc97c | anonymous 2 | - |
| `64351708` | 2 | 4 | 0x0053d590 0x0053d7a0 | 0x210 | anonymous 2 | - |
| `7d30e054` | 2 | 5 | 0x0053f6ec 0x0053f874 | 0x188 | named 2 | CPopupScreen |

**11 shapes repeat over 27 tables, and the repeat is not a copy of one class's table: 3 groups are **per-class** (10 tables, 6 distinct owner classes), 3 hold one class's table twice (6 tables), and 5 groups (11 tables) carry no class at all.**

**5 of the 11 groups sit inside one 4 KiB page and the widest spans `0x780c0`**, so a repeat is emitted next to the classes that hold it rather than collected in one place: the two 4-member groups are one interface at `+0x2c` and one at `+0x28` held by `PackageMapLevel`, `TcpipConnection`, `TcpipConnectionXbox` and `DemoRecConnection`, one table per class, and the pair is repeated in each.
<!-- /generated:class-interface-twins -->

The correction matters for the naming question rather than for the count: a repeat is evidence that the
shape is one interface with several implementations, so the family is the unit a name would settle, and a
group whose members carry no class at all is the anonymous half of the same question rather than a
different one.

## What a body says about its own file: the map's names are its assert references, and the one gap head that carried one is a map row

Every naming question on this page ends in the same place: a name for a body nothing names. The map's own
`<Module>_<File>` names are the build's `__FILE__` assert paths, so the read a gap head needs is whether the
body *holds* that reference. `tools/gap-body-files.py` reads three channels at the body instead of at the map
row, over the 1,450 gap heads U40 decodes and over every body the map has.

  * **`assert`** -- a code site holding the address of one of the image's assert-path strings. It is a *pushed
    address*, not a pushed string: the site is the `imm32` of a `68` push whose dword is the path's VA, which is
    why the interface behaviour census's printable-literal read found no source path in any of its 52 tables. The
    assert path is referenced by address and never spelled as a literal.
  * **`string`** -- a code site holding the address of any printable string in the image (a UTF-16 run of six
    characters or more, or a narrow run of eight). A body that pushes `invalid stencil op` is a D3DDrv body
    whatever else is unknown about it.
  * **`callee`** -- the `<Module>_<File>` tokens of the map bodies a body calls through a direct `E8 rel32`.
    This places a body in a module and never names it; an `E8` byte inside another instruction's displacement
    can encode a false call, so it is an upper bound with the map's own rate beside it.

The control is the map's population, and it is exact rather than a rate: of the 1,321 bodies whose own name is
a `<Module>_<File>` token, **1,282 reference the assert path of the file their name is built from, 0 reference a
different one and 39 reference no assert path at all**. A name the map holds is therefore not a guess this read
corroborates -- it is the same reference, one level out.

<!-- generated:gap-body-files -->
| gap head | size | names | the reference |
|---|---|---|---|
| `0x003e0a30` | 5715 | ANG_DAMP | USE_FAST_SPIN | 38 string site(s) |
| `0x000fd030` | 2227 | UltraHigh | Normal | 14 string site(s) |
| `0x002b0050` | 1169 | Options | UseTrilinear | 11 string site(s) |
| `0x002bda20` | 564 | D3DERR_UNSUPPORTEDALPHAOPERATION | D3DERR_UNSUPPORTEDCOLORARG | 9 string site(s) |
| `0x00182f30` | 846 | Client | KeepAliveTime | 7 string site(s) |
| `0x0007e970` | 595 | Options | Options | 6 string site(s) |
| `0x000f9990` | 469 | Client | Client | 5 string site(s) |
| `0x0011f0e0` | 1499 | Skipped '%s' | %s\%s-%i.uxx | 5 string site(s) |
| `0x0002e690` | 624 | PAUSESOUNDS | PAUSESOUNDS | 4 string site(s) |
| `0x0002d600` | 299 | XBoxAudio | MusicVolume | 3 string site(s) |
| `0x00192730` | 836 | Reviewing Paths | This pickup should have a PickupBase | 3 string site(s) |
| `0x0001fa30` | 154 | Arctor<BYTE>(Linear)==LINEAR_SEEK | ..\..\Core\Inc\FFileManagerLinear.h | 2 string site(s) |
| `0x00035f10` | 222 | InContextCommand | StrProperty0 | 2 string site(s) |
| `0x0007c02f` | 560 | %s::ImportText: Illegal or missing key n | %s::ImportText: Incomplete parenthesised | 2 string site(s) |
| `0x0011ea30` | 605 | Receiving package '%s' | Engine | 2 string site(s) |
| `0x001d3270` | 436 | NumItems | Config%d | 2 string site(s) |
| `0x00295d30` | 175 |      Title: %s |      Album: %s | 2 string site(s) |
| `0x002ce490` | 3123 | RI_AuthenticationFailed | RI_BannedClient | 2 string site(s) |
| `0x002d26b0` | 250 | Client | LogMaxConnPerIPPerMin | 2 string site(s) |
| `0x002d4100` | 201 | UTcpNetDriverXbox | bUseVDP | 2 string site(s) |
| `0x003e3050` | 879 | line %d, char %d: Expected int, found %s | line %d, char %d: found garbage after da | 2 string site(s) |
| `0x00012410` | 165 | [SDS] - Launch Main Exiting Loop | 1 string site(s) |
| `0x0001f800` | 115 | LinearFileStillOpen | 1 string site(s) |
| `0x00026be0` | 75 | InPos>=0 && InPos<=Size | 1 string site(s) |
| `0x0007e8c0` | 49 | Aborted! | 1 string site(s) |
| `0x000adb00` | 173 | (Karma):  | 1 string site(s) |
| `0x00182880` | 85 | Engine | 1 string site(s) |
| `0x00194ca0` | 85 | Engine | 1 string site(s) |
| `0x00194d00` | 85 | Engine | 1 string site(s) |
| `0x00194dc0` | 85 | Engine | 1 string site(s) |
| `0x001b1690` | 3870 | %s flew out of the world! | 1 string site(s) |
| `0x001bff70` | 174 | Cast of %s to %s failed | 1 string site(s) |
| `0x001d2620` | 143 | Engine | 1 string site(s) |
| `0x00223bb0` | 107 | GetBoneRotation: Bone [%s] not found in  | 1 string site(s) |
| `0x002b5880` | 244 | invalid stencil op | 1 string site(s) |
| `0x002b96b0` | 160 | Engine | 1 string site(s) |
| `0x002b9a10` | 65 | CREATING COPY TARGET - RETURNING | 1 string site(s) |
| `0x002c1890` | 195 | Settings | 1 string site(s) |
| `0x002c62c0` | 387 | !!Send() got SOCKET_ERROR %d for %s | 1 string site(s) |
| `0x002d3790` | 56 | UnregisterInfo: XNetUnregisterKey(HostKe | 1 string site(s) |
| `0x003081c0` | 239 | UnregisterPlayer failed with code 0x%x | 1 string site(s) |
| `0x00318f90` | 879 | Engine | 1 string site(s) |
| `0x00328890` | 606 | SessionStartTask::Finsh: ExchangeKey: %s | 1 string site(s) |
| `0x00017630` | 38 | Engine_UnStaticMesh | a call into a named body |
| `0x0001bf10` | 1490 | Core_UnTemplate | a call into a named body |
| `0x0002a870` | 46 | XboxAudio_FindSlotByKey | a call into a named body |
| `0x0002a8a0` | 49 | XboxAudio_FindSlotByKey | a call into a named body |
| `0x0002a8e0` | 113 | XboxAudio_FindSlotByKey | a call into a named body |
| `0x0002afb0` | 258 | XboxAudio_ApplyBufferParams XboxAudio_HasPendingKey | a call into a named body |
| `0x0002b570` | 67 | XboxAudio_CloseSoundDevice | a call into a named body |
| `0x0002b7f0` | 202 | XboxAudio_PollDeviceChanges | a call into a named body |
| `0x0002bc20` | 86 | XboxAudio_FindSlotByKey XboxAudio_ApplyBufferParams | a call into a named body |
| `0x0002bc80` | 86 | XboxAudio_FindSlotByKey XboxAudio_ApplyBufferParams | a call into a named body |
| `0x00034a30` | 125 | Core_UnObj | a call into a named body |
| `0x00034be0` | 43 | Core_UnObj | a call into a named body |
| `0x00038ce0` | 65 | Core_UnObj | a call into a named body |
| `0x00059730` | 177 | Core_UnTemplate | a call into a named body |
| `0x000679c0` | 188 | Core_UnObjBas Core_UnObj | a call into a named body |
| `0x00068a40` | 128 | Core_UnObj | a call into a named body |
| `0x00068ac0` | 95 | Core_UnTemplate | a call into a named body |
| ... | | 47 more | |

**2507 heads decode as 2189 bodies and 318 islands; of the bodies 0 hold an assert path, 43 a printable string and 755 a call into a body the map names, and 2082 hold none of the three.**

**Control: 1337 of the map's 16617 bodies hold an assert site, and 1283 of the 1329 `<Module>_<File>` bodies carry the file their own name is built from, 0 carry a different one and 46 reference no assert path at all -- so the name is that reference read one level out.**

**The owed interface representatives' slots hold 150 of these heads and only 108 are bodies -- 33 are islands and the rest never close -- so the interface population's gap-head slots over-count their methods by 42; of the 108 bodies 0 hold an assert path, 2 a string and 61 a call into a named body, and the per-body read cannot name an interface (U40).**
<!-- /generated:gap-body-files -->

**The one gap head the channel named is now a map row.** `0x0007e3b0` (65 bytes) holds two push sites of
`UnVcWin32.cpp`, so the body nothing named was a `Core` translation unit's and the name it earned is
`Core_UnVcWin32`. It is a row in `symbols/xbox_functions.csv` since this bundle, with the boundary and
override rows that create it, so the census above reads 1,450 heads and **0** of them holds an assert path --
the channel's one hit left the gap population. The four `Core_UnVcWin32` bodies in the map are
`0x0007e360` (37 bytes), `0x0007e390` (32), `0x0007e3b0` (65) and `0x0007f4e0` (183).

**The 43 that carry a string are placed in a subsystem, not named.** `0x002b5880` pushes `invalid stencil op`
and is a D3DDrv body, `0x002bda20` pushes `D3DERR_UNSUPPORTEDALPHAOPERATION`, `0x0002d600` pushes `MusicVolume`
beside the literal `XBoxAudio`, and `0x002ce490` pushes `RI_AuthenticationFailed` and `RI_BannedClient`. The two
of the 43 that sit in an owed representative's slot are `0x002b5880` and `0x002c62c0` (`!!Send() got SOCKET_ERROR %d for %s`, the socket layer). A string says which
subsystem a body belongs to and never which class it is a method of.

**The 457 that call a named body are a module, and the widest callee is a template.** 23 distinct callee
files reach them: `Core_UnTemplate` 19 bodies, `Core_UnObj` and `Engine_UnActor` 7 each,
`XboxAudio_FindSlotByKey` 5, and nineteen files reach 1 to 3. That is the verdict the behaviour census reached
from the other side -- a callee's file is the file of the code a slot *calls* -- now read at the body the slot
holds.

**The interface population's gap heads over-count the methods they hold by 41.** The 88 owed
representatives' slots hold 150 of these heads, and only 109 are bodies: 36 are jump-table islands of three to
six bytes and 5 never close, so a slot that holds one points at a dword inside a function rather than at a
method. Of the 109 bodies, **0 carry an assert path, 2 a string and 61 a call into a named body** -- so U40's
per-body route cannot name an interface, and the read is now a count rather than an assumption.

The census row of the artifact carries every number above, and the block is rebuilt from the committed CSV
with no dump:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/gap-body-files.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/gap-body-files.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is synthetic (the body lookup, the `<Module>_<File>` token, an `E8` target inside and outside the
body, the drop of a site no body owns, all four verdicts, the render and both artifact round trips; no dump,
writes nothing) and `--check-doc` rebuilds the block from the committed CSV with no dump. Both run in
`make ci`.

## The table's own bytes: no RTTI, and the reader's control is the image's own

Every name the 232 interface tables carry came from a join -- a registration, a script class, a member
field, a slot token -- and each of those channels has been measured shut or partial. The one channel still
unread is the table's own bytes: MSVC writes a pointer to an `RTTICompleteObjectLocator` in the dword
**before** a vftable when the translation unit is built with RTTI, and that locator's `pTypeDescriptor`
names the class (`.?AV<Class>@@`). An interface table is a vtable nothing registers, so this is the one
channel that could have named one from the table alone.

`tools/class-interface-rtti.py` reads `vftable[-1]` for all 724 tables -- the 232 interface tables and
every one of the 492 class tables with a measured parent edge -- and follows it as a locator: signature
zero, a type descriptor eight bytes before a decorated name, and only a `.?AV`/`.?AU` name accepted, so a
dword that merely points at a zero is not mistaken for one.

<!-- generated:class-interface-rtti -->
| population | tables | `col` | `no-col` | `not-a-col` | `prev-unmapped` |
|---|---:|---:|---:|---:|---:|
| interface | 232 | 0 | 54 | 178 | 0 |
| class | 492 | 0 | 299 | 193 | 0 |
| all | 724 | 0 | 353 | 371 | 0 |

**0 of the 724 tables carry an RTTI locator.** The `-4` dword is zero on 353 of them and a mapped pointer on 371, and none of those 371 pointers resolves to a locator whose type descriptor names a class -- so no translation unit these tables were compiled in was built with RTTI, and an interface table's own bytes name nothing.

| the image's control | host | signature | verdict |
|---|---|---|---|
| `.?AVexception@@` | `0x00578e04` | `0x00000000` | `col` |
| `.?AVlength_error@std@@` | `0x00578e3c` | `0x00000028` | `not-a-col` |
| `.?AVlogic_error@std@@` | `0x00578e20` | `0x0000000c` | `not-a-col` |
| `.?AVout_of_range@std@@` | `0x00598d60` | `0x00000000` | `col` |

**The image carries 4 decorated names, and the reader answers both ways on them: 2 hosts pass the whole chain and name `.?AVexception@@`, `.?AVout_of_range@std@@`, and 2 hosts are refused by the signature test.** No dword in the image holds any of the hosts, so no vftable heads one, so the control shows the reader accepting a real locator and refusing a non-locator in this image, and the zero above is the build's flag rather than the read.
<!-- /generated:class-interface-rtti -->

**What this settles.** An interface table's own bytes name nothing, and the selector is the build's flags
rather than the read. The image carries four decorated names, the reader accepts two as complete locators
and refuses the other two on the signature (they are not locators: one is a `_ThrowInfo`-shaped structure
whose first dword is `0x28`, the other `0xc`), and no dword in the image holds any of the four hosts -- so
RTTI was emitted for the CRT's own throw types and never for a class table. The 353 zero dwords are the
shape of a vftable compiled without RTTI; the 371 that hold a mapped pointer hold the previous table's last
slot or a neighbouring dword, which is why the chain is followed to a name instead of tested at the
signature. This is the fifth name channel measured shut for the interfaces -- after the assert token, the
callee file, the pushed literal and the slot body's own file -- and the only one that could have answered
from the table alone.

**What it does not say.** A build without RTTI still has a name channel in *code that wraps the object*:
the constructor that stores the table is in the class's own translation unit and its asserts name the file.
That channel is the one the installers census reads, and this measurement says nothing new about it.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-rtti.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-rtti.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is synthetic (the four chain verdicts, the control row and the artifact round trip; no dump,
writes nothing) and `--check-doc` rebuilds the block from the committed CSV with no dump. Both run in
`make ci`.

## The defaults read as bodies: 27 bodies, eight shapes, and the seven that own nothing else

`tools/class-interface-behaviour.py` counted 289 stub slots and 38 `__purecall` slots and stopped there,
because a read is only owed where there is a body. A default slot is still code, and
`tools/class-interface-residue.py` disassembles each distinct default body the 88 owed representatives hold
-- **27 bodies over 327 slots** -- and classifies it by what it returns.

<!-- generated:class-interface-defaults -->
| the default body | bytes | what it returns | slots | representatives |
|---|---:|---|---:|---|
| `0x000171c0` | 3 | `return-zero` | 116 | 38 |
| `0x003e5501` | 20 | `purecall` | 38 | 6 |
| `0x0001b520` | 3 | `return-this` | 29 | 16 |
| `0x00031e70` | 3 | `bare-return` | 29 | 8 |
| `0x002783d0` | 6 | `return-const` | 29 | 22 |
| `0x00020f00` | 1 | `bare-return` | 12 | 5 |
| `0x00018b70` | 5 | `return-zero` | 11 | 8 |
| `0x000ea7f0` | 3 | `bare-return` | 10 | 7 |
| `0x00275ed0` | 6 | `return-const` | 8 | 8 |
| `0x00018bb0` | 5 | `return-zero` | 5 | 5 |
| `0x00016ec0` | 4 | `return-minus-one` | 4 | `0x004d2e58` `0x0052d7d0` |
| `0x000bd350` | 8 | `return-const` | 4 | 4 |
| `0x00179920` | 7 | `return-pair` | 4 | 4 |
| `0x001eaaf0` | 4 | `return-field` | 4 | 4 |
| `0x0025ff30` | 5 | `return-this` | 4 | `0x004d2e58` `0x0052d7d0` |
| `0x00386710` | 1 | `bare-return` | 3 | `0x00564da4` `0x00564dbc` |
| `0x00016f50` | 4 | `return-field` | 2 | `0x004d2e58` `0x0052d7d0` |
| `0x00018bc0` | 5 | `return-zero` | 2 | `0x004c85b0` `0x004c8db8` |
| `0x00018bd0` | 5 | `return-zero` | 2 | `0x004c85b0` `0x004c8db8` |
| `0x001271f0` | 3 | `bare-return` | 2 | `0x005113b0` `0x0051a810` |
| `0x001d4d00` | 8 | `return-const` | 2 | `0x00516fd4` `0x005170f4` |
| `0x0023a2d0` | 3 | `bare-return` | 2 | `0x0051b0cc` `0x0051bc70` |
| `0x00179aa0` | 4 | `return-field` | 1 | `0x00510eb8` |
| `0x0024e1b0` | 6 | `return-const` | 1 | `0x004e3520` |
| `0x0024e2a0` | 6 | `return-const` | 1 | `0x0051d7e8` |
| `0x00331304` | 5 | `return-zero` | 1 | `0x0053f6d8` |
| `0x003479e6` | 1 | `bare-return` | 1 | `0x0053f6d8` |

**The owed representatives' 327 default slots are 27 distinct bodies in 8 shapes: `purecall` 1, `return-zero` 6, `bare-return` 7, `return-this` 2, `return-field` 3, `return-pair` 1, `return-const` 6, `return-minus-one` 1.** The largest is `0x000171c0` (`xor eax,eax; ret`, 3 bytes) filling 116 slots, and the only body that traps is `__purecall` itself (38 slots over 6 tables), so an unoverridden interface method answers rather than refuses: it returns zero, `this`, a field, a constant or nothing at all.

**The constants a default returns are `0x1`, `0x2`, `0x4`, `0x8`, `0x18`** (`mov eax,<n>; ret`), and the fields one reads are `+0xc`, `+0x10`, `+0x2c`, `+0x48` -- a default getter is a field of `this` at a fixed offset, which is the object layout an unoverridden interface method assumes.
<!-- /generated:class-interface-defaults -->

**What this settles.** An unoverridden interface method is not a hole: it returns a value. **116 of the 327
slots are one 3-byte `xor eax,eax; ret`**, 38 are the `__purecall` handler, and the rest return `this`, a
field of `this`, a field pair, a constant (`1`, `2`, `4`, `8`, `24`), `-1`, or nothing at all -- so a call
through a slot an implementation did not override is *defined* behaviour in this build, and a decompiler can
render it as the constant or field access it is instead of an unknown. The field getters (`+0xc`, `+0x10`,
`+0x2c`, `+0x48`) state the object layout an unoverridden method assumes, which is the same layout the
implementing class's constructor writes.

**The 7 representatives that owe no read are that finding from the other side.** None of their 42 slots
holds a body: 33 are `__purecall` (20 of `0x004c7800`'s 21 slots, 11 of `0x004eb914`'s 12, 2 of
`0x005181d0`'s 3) and 9 point outside the map at an address no gap head decodes. An interface whose table
is purecall-only is therefore an interface **nothing implements in this build**, not one whose code the map
has not read -- which is the distinction the family census's "defaults" category was standing in for.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-residue.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-residue.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` is synthetic (the eight shapes, the immediate-store parse, the containing-object lookup and both
artifact round trips; no dump, writes nothing) and `--check-doc` rebuilds both blocks from the committed
CSVs with no dump. Both run in `make ci`.

## The four tables nothing installs: four constructors, three of them unlisted

The installers census left **four anonymous tables** with one store site each and no body around it, and
the provenance census read that as "no installer". `tools/class-interface-residue.py` reads the site
instead: each is a `mov [<reg>+disp], imm32` writing the table into dword 0 of a fresh object, and the body
around it is found by walking back to the padding that bounds the region. Three are constructors that
neither the map nor the gap census starts a body at -- 17, 24 and 106 bytes, each opening `mov eax,ecx` --
and the fourth is a `push-imm` gap head of 73 bytes.

<!-- generated:class-interface-nosite -->
| the table | slots | store | the body it is stored in | bytes | its immediate stores |
|---|---:|---|---|---:|---|
| `0x004f70a0` | 2 | `0x000e1704` | hole `0x000e1700` | 24 | `+0x0=vtable 0x004f70a0` |
| `0x005172ec` | 5 | `0x001df51c` | hole `0x001df4f0` | 106 | `+0x0=vtable 0x005172ec` |
| `0x005181dc` | 3 | `0x00200604` | hole `0x00200600` | 17 | `+0x0=vtable 0x005181dc` |
| `0x0053f7c4` | 5 | `0x003313cc` | head `0x003313ad` | 73 | `+0x0=vtable 0x0053f7c4` |

**All 4 tables are stored: 3 by a constructor no map row and no gap head starts, and 1 by a gap head**, each a `mov [<reg>+disp], imm32` writing the table into the object's dword 0 before the first field write. The 3 unlisted constructors are 17/24/106 bytes and open `mov eax,ecx`; 1 of the 4 tables leaves `0xdeaddead` in a field beside the vftable, which is the layout a fresh object is built with.
<!-- /generated:class-interface-nosite -->

**What this settles.** "No install site" was a limit of the census's body list, not a property of the four
tables: every one is installed by its own constructor, and the objects they build carry the fresh-object
sentinel `0xdeaddead` in the field beside the vftable (24-byte one: fields `+0x58`, `+0x5c`, `+0x60`). The
three unlisted constructors are also the first measure of what the gap census's boundary rule misses: one
sits inside the gap the census attributes to `0x000e1720`, the other two in a span no gap range covers, and
all three are thiscall constructors whose first instruction is `mov eax,ecx` (`8b c1`) -- **an opening the
census did not name**: the shared prologue table's `mov-eax` is `b8` (`mov eax,imm32`), and this note first
read the two as the same shape. `tools/gap-heads.py` now carries `8b c1` among the twelve openings it
measured against the map's own rows, and **all three constructors are heads**, at the 24, 106 and 17 bytes
this census measured by hand. Nothing in this population is left owed: the 26 gap-head representatives
remain the map's work (U40), and these four are read.

`--check-doc` rebuilds both blocks from the committed CSVs with no dump; the census row of each artifact
carries the totals above.

## The 18 heads' rungs: 490 edges, 0 that are not rungs, and the extent rule they fixed

The sections above read the 246 tables nothing names as *objects*: what they contain, which bodies store
them, which are a class's dispatch table. This one reads them as an **inheritance edge**. The 18 heads are
named from the classes below them, and an override census is the constraint that gives those names a
measurable meaning -- but `inventory/class_virtual_overrides.csv` diffs each class against its *script*
super, and none of these classes has one, so it has never had a row for them.

`tools/class-ladder-rungs.py` reads the same two slot vectors over all 493 measured parent edges in
`inventory/class_table_parent_edges.csv` and records what the child does to the parent's table: how many
slots it repeats at the same index, how many it replaces, how many it adds, and slot 3 reported apart
because it is per-class bookkeeping and never an override. The verdict is the one invariant a vtable cannot
break: a derived class's table is never shorter than its parent's.

<!-- generated:class-ladder-rungs -->
| parent (unnamed) | slots | children | replaced | added | most slots in a child | verdicts |
|---|---|---|---|---|---|---|
| `0x004c78c0` | 31 | 7 | 7 | 93 | 56 | extends 6 identical 1 |
| `0x004c9348` | 31 | 2 | 6 | 36 | 56 | extends 2 |
| `0x004c93d0` | 56 | 1 | 14 | 1 | 57 | extends 1 |
| `0x004c94b0` | 43 | 1 | 15 | 0 | 43 | extends 1 |
| `0x004d0318` | 40 | 3 | 16 | 1 | 41 | extends 3 |
| `0x004d03c8` | 42 | 3 | 14 | 0 | 42 | extends 3 |
| `0x004d1468` | 35 | 4 | 9 | 25 | 53 | extends 4 |
| `0x004d17a8` | 53 | 13 | 157 | 0 | 53 | extends 13 |
| `0x004d2400` | 37 | 1 | 7 | 0 | 37 | extends 1 |
| `0x004d34d8` | 32 | 1 | 1 | 0 | 32 | extends 1 |
| `0x004d3af0` | 33 | 1 | 1 | 0 | 33 | extends 1 |
| `0x004eaea8` | 47 | 2 | 30 | 4 | 50 | extends 2 |
| `0x004fa390` | 37 | 4 | 21 | 0 | 37 | extends 4 |
| `0x004fc4f0` | 30 | 2 | 3 | 45 | 71 | extends 2 |
| `0x004fc588` | 34 | 2 | 7 | 0 | 34 | extends 2 |
| `0x004fcfe0` | 36 | 2 | 9 | 0 | 36 | extends 2 |
| `0x00517860` | 30 | 3 | 3 | 2 | 32 | extends 3 |
| `0x005178d8` | 32 | 5 | 15 | 0 | 32 | extends 5 |

**490 of the 490 measured parent edges read as rungs -- 362 `extends`, 128 `identical`, 0 `shorter`.** A child's table is never shorter than its parent's, so a `shorter` row is the parent link or the slot count, not the rule: none does.

**57 of the 490 edges land in a parent no registration names, over 18 distinct heads**, and every one of those heads is a class with no script row, so this is the only override census the 88 edges have: the remaining 433 are the script-linked population the control is read on.

**Control: the 377 script-linked edges this read reaches reproduce `class_virtual_overrides.csv` on 377, 0 disagreeing.** Same slot vectors and the same slot-3 exclusion, so a count on a row that has no published answer is the same measurement.
<!-- /generated:class-ladder-rungs -->

**The control is what makes the rest a measurement, and finding it wrong first was this run's third stale
artifact.** The script-linked edges this read reaches reproduce `class_virtual_overrides.csv`'s own counts
exactly, once that file is regenerated: the committed copy diffed `WaveTexture`, `FluidTexture` and
`WetTexture` against `Texture`'s table (`0x004cfba0`, 58 slots) instead of `WaterTexture`'s
(`0x004cfe68`, 59 slots) -- the name `0x004cfba0` carried before #533 renamed the pair -- so three
classes read 9 overrides and 1 added slot instead of 4 and 0. Regenerated it has **378** resolved pairs
where the committed file had 302: the six tables #533 and its follow-ups named are pairs the old file never
had.

**The 14 `shorter` rows were the extent rule, and the dwords that made them are string data.** A table
in this image sits in front of the build's wide-string pool: `0x004c7c78` was counted 34 slots and its slots
32 and 33 hold `0x00330044` and `0x00440044`, which are the characters `D3` and `DD` of a UTF-16 string --
not `CLiveEngine::SeparateTextAndIcons` and `ASN1Dec_KERB_AP_REPLY` at all, because an aligned dword in
`.text`'s *address range* is not an instruction boundary. `0x00521f80` was counted 34 and its slots 31-33
are two wide spaces and a `T`. **Twelve of the 492 tables ended on such a dword and 14 dwords in all were
counted**, which is the whole over-read: the tables carry the first two characters of the string that
follows them. The old rule was `4-aligned and inside a section flagged executable`, and every section in
this image carries that flag (`.rdata`'s own map coverage is 0.000 against `.text`'s 0.86).

**Two markers inside the tables were the second half of the error, and both are measurements.** `0x003e5501`
is `__purecall`, an *odd* address, so the alignment test ended six tables on a genuine slot; and
`0x00000000` ends a further 221, and where a table stops is itself a reading: `Actor`'s 123-slot table
stops on the float `0x41c80000`, `xEmitter`'s on `0x44160000`. A slot is now a **body start** -- an exact map address at any alignment, or a
terminator one past it -- inside a section the map's own functions cover, and a table ends on a wide-string
dword, a null, or a dword outside code.

**A run of code pointers is still not a table, and the last four over-reads prove it.** `0x004d0318` was
read as 43 slots while its children are 40 and 41; the run holds **install stores of two other tables** at
`+0xa0` and `+0xa4`. Clipping a table at the first interior install store (never before its own
`ProcessEvent`) answers all four -- `0x004d0318` 43->40, `0x004d03c8` 45->42, `0x004fc588` 42->34 and
`0x00521f80` 34->30 -- and **82 of the 492 tables are clipped this way**, which is what makes the packed
`.rdata` region one structure per class rather than 492 runs.

**`Engine` was the fourteenth row and it was the child that was wrong, not the parent.** Its own table is
**56** slots, not 30: slot 30 is `__purecall`, and past it the table runs to 56 and stops on a wide string
(`Se`). It now reads `extends` against `0x004c78c0` (31), 25 slots added, and its own child `GameEngine`
(66) extends it by 10 -- a three-rung chain where the artifact had one broken edge. The other end of the
ladder reads the same way: `StreamBase` is `Object`'s 30-slot table with slot 3 differing, `identical`,
where it was 34.

**What this does not say.** It does not re-name anything: every row is keyed by the table's own address, and
a class with no name in `inventory/xbe_class_table_names.csv` keeps its address here. The one slot the start
rules cannot confirm is `xEmitter`'s slot 18 (`0x00082ea0`), a body behind a jump table; the artifact
counts it in `unconfirmed` and refuses to end a table on it.

## Who declares a slot body: 224 of the 701, 423 an interface's method, and 4 bodies two classes share

`inventory/gap_heads.csv` records **one** table per head: the first dword outside `.text` the read happened
to resolve into a table slot. That is not a declaration. A class table's slot holds a pointer, and every
descendant class that does not override the slot holds the same pointer, so a body a class declares appears
at the same index in a whole subtree of tables -- the widest here is **489 tables for one body**. "Which
table did the scan see first" therefore picks a table out of a subtree and calls the pick a name.

`tools/class-slot-declarers.py` reads every slot of all 724 tables the project censuses (492 class tables,
232 interface tables; `inventory/class_slot_declarers.csv`) and, for each of the 701 gap bodies that sits in
a slot, walks up the **measured parent edges** (`inventory/class_table_parent_edges.csv`, 493 edges):
**the declarer is the highest table whose slot at that index still holds the body.** A declaration is where
the body enters the chain; every table below it is a copy. The edge is the table address, not the class name,
and that matters twice: a parent table no registration named still has an extent, so a slot past that extent
is the child's own (Engine's slots 34-37 and 45-47 are past `Subsystem`'s 31-slot table and are Engine's),
and a class-name walk would have stopped there and called the seven "at or above Engine". The verdicts are
`declared` (the declaring table has a class name), `declared-unnamed` (it has none), `conflict` (two classes
that are not one chain both hold the body), `member` (a table a constructor stores into a member field, whose
slots are the member's methods and never a class slot), `unnamed-table` (no class name and no parent edge
reaches the sit-in) and `interface` (interface tables only).

**The control is the map's own names.** Every `<Class>_vt<slot>` row in `symbols/xbox_functions.csv` is run
through the same walk against its own class's table: **531 of the 538 agree, 7 name a class that inherits the
body**, and none is unresolved. The seven are `GameEngine_vt33`, `_vt54`, `_vt55` (Engine's table holds the
same body at those slots), `XboxViewport_vt51` and `_vt54` (Viewport's), `TcpipConnection_vt39`
(NetConnection's) and `StrProperty_vt41` (an unnamed table, `0x004d17a8`). The gap census's own 221 slot
names are the second control: **219 name the declarer, 0 disagree**, and the 2 that have no declarer are a
conflict and an unnamed table -- never a name the walk contradicts.

<!-- generated:class-slot-declarers -->
| what holds the body | bodies | share | name it earns |
|---|---|---|---|
| a class table, and the walk reaches the top of its chain | 13 | 3% | `<Class>_vt<slot>` |
| a class table no registration names, which the walk reaches by its parent edge | 7 | 2% | `<script>_vt<slot>` when an edge records the script super (0), none otherwise |
| two independent declarations of one body | 4 | 1% | none (a conflict) |
| a member table (a constructor stores it into a field) | 9 | 2% | `<Class>_member_<field>_vt<slot>` |
| a class table the class map leaves unnamed | 3 | 1% | none (no class) |
| interface tables only | 423 | 92% | none (an interface name) |

**The 459 bodies are 1124 slot sit-ins over 724 tables, and a declared body sits in every descendant table that does not override the slot.** The census reads the 724 tables the project knows -- 492 class tables (478 with a class) and 232 interface tables -- and indexes every slot, because `inventory/gap_heads.csv`'s `slot_table` is the *first* table a reference happened to land in, not a declaration. 541 sit-ins are class-table slots and 583 are interface slots; the widest fan-out is **489 tables for one body**, so "the first table that holds it" is a choice among a subtree. The declarer is the highest class in the `extends` chain whose slot at that index still holds the body.

**The control is the map's own names: 531 of 538 agree, 7 name a class that inherits the body, 0 could not be resolved.** Every `<Class>_vt<slot>` row in `symbols/xbox_functions.csv` is run through the same walk against its own class's table. The 7 derived rows are `XboxViewport_vt51`, `StrProperty_vt41`, `XboxViewport_vt54`, `TcpipConnection_vt39`, `GameEngine_vt33`, `GameEngine_vt54`, `GameEngine_vt55`: their class's table holds the body at that slot, and so does an ancestor's, so the name is a holder's and not the declarer's.

**The gap census's own slot names survive the walk: 13 of 15 name the declarer, 0 name someone else, and 2 have no declarer to agree with.** The first-hit table agrees with the declaration walk where the subtree's root is the table the reference landed in, which is the common case; the artifact is what makes the difference checkable rather than assumed.

**11 bodies sit at two or more slot indices**, which no single name can carry: `0x0001f9b0` (10/11), `0x00179930` (1/2), `0x00179b00` (7/8), `0x0017a7d0` (1/7), `0x001ddf20` (2/3/8), `0x001df4e0` (1/2/3), `0x001df570` (1/2), `0x001ec800` (7/8/30). The index set is recorded per body; the verdict is `conflict` only when two *classes* both declare it -- an interface slot beside a class slot is one method seen through two tables, not two declarations.

**The 4 conflicts are one body two independent classes both declare** -- the two tables point at the same address, and whether the linker folded two identical functions or both were built from one stub is not decided here: `0x000624e0` at slot 17; `0x001b9040` at slot 34; `0x001b9060` at slot 35; `0x001ec800` at slots 7/8/30.

**What the walk did with its names.** 242 of these bodies are map rows now -- 209 named `<Class>_vt<slot>` from the declaration, 33 a member's method -- 25299 bytes, each through `symbols/xbe_boundaries.csv` with the `xbe_overrides.csv` creation row a fresh import needs, and each named after its **declarer**, never the first table that held the pointer. 13 bodies earn a name the decode cannot carry, because a name is not a size. What is left is a name, not a read: 423 are an interface's method, and 3 sit in a class table whose class the class map has not named.

<!-- /generated:class-slot-declarers -->

**The 7 bodies declared by an unnamed table are two of them.** `0x0011ea30` (605 bytes) and `0x0011f0e0`
(1,499 bytes) are `ChannelDownload`'s and `HTTPDownload`'s slots 32 and 35, and the table that declares them
is `0x004fcfe0` -- the class map's unnamed class table, which no registration and no edge names. The same is
true of `0x00034be0` (0x004d34d8 slot 11), `0x00036a80` (0x004d3af0 slot 31), `0x000fde10` (0x004c94b0 slot
11), `0x0011e730` (0x004fcfe0 slot 34) and `0x001a5330` (0x004fc588 slot 33). So the declaration is real and
the class is the unnamed-table residue the rung census reads from the other side.

**Five bodies the census named by pointer but not by a class are a class's declared virtual.**
`0x0001db40` (`LodMeshInstance` slot 42), `0x000bd360` (`MeshInstance` slot 86), `0x000bd380`
(`LodMeshInstance` slot 91), `0x00167f50` (`MeshInstance` slot 76) and `0x002c12a0` (`NetPendingLevel` slot
31) carry pointers and a slot, but their first-hit table is an *interface* or a *member* table, which has no
class to name them with, so `gap_heads.csv` leaves `slot_class` empty. A class table holds them at the same
index -- the first-hit channel recorded one of the tables and it was the wrong one.

**The 40 member-table bodies are an interface object's methods, and 31 of them carry a label.**
`inventory/class_member_tables.csv` labels 32 of the 49 member tables `<Class>_member_<field>`, and 31 of
these bodies sit in a labelled one -- `NullRenderDevice_member_0xb8` 6, `NetPendingLevel_member_0x28` 5,
`XHVVoiceSubsystem_member_0x2c` 4, the `Linker_member_0xb8` and `Connection_member_0x28` families among the
rest. The other 9 sit in a member table the label pass refused (no owner, or more than one field offset), so
the method is real and its owner is the open question.

**The 4 conflicts are one body two independent classes both declare**: `0x000624e0` (`Object` slot 17 and
`GameInfo` slot 17), `0x001b9040` and `0x001b9060` (`PSDistortionBaseMaterial` and `PSBlurMaterial`, slots
34 and 35) and `0x001ec800` (four `VertexStream*` classes at slots 7, 8 and 30). Whether the linker folded
two identical functions into one or the tables were built from one stub is not decided here; what is decided
is that no single `<Class>_vt<slot>` name carries them, and 20 bodies in all sit at two or more slot indices,
which the artifact records per body.

**The 701 slot bodies and the 807 bodies named by nothing do not overlap, and that is structural.** A slot
is a dword reference, so a body in a table slot is named by the pointer channel by construction; the 807 of
the 2,749 decoded bodies that nothing names have no reference at all (no dword outside `.text`, no `push`, no
call or jump). The two residues are therefore separate work: this page's 224 declared names are an add the
map's slot route could carry, and the 807 need a channel that does not exist yet.

**No map add is in this bundle.** The 224 declared names (21,613 bytes), the 31 labelled member methods and
the five census misses are the population an add would carry, and a row moves five artifacts; the 423
interface-only bodies need an interface name first, so the 26 gap-head representatives' add is blocked on
that name and not on a read.

## Regenerating, continued

The rung census and the interface families both read committed artifacts; the rung census also reads the
slot vectors:

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-ladder-rungs.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-ladder-rungs.py --write-doc docs/systems/CLASS_REGISTRATION.md

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-slot-declarers.py \
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-slot-declarers.py --write-doc docs/systems/CLASS_REGISTRATION.md

    python3 tools/class-interface-families.py -o inventory
    python3 tools/class-interface-families.py --write-doc docs/systems/CLASS_REGISTRATION.md

`--check` on the rung census is synthetic tables -- a child that replaces one slot and adds one, one that
copies its parent whole, a shorter child, slot 3 alone, and the control's arithmetic -- and on the families
it is synthetic shape rows -- a named family, a member-held one, an anonymous one, a two-owner repeat and a
singleton. `--check-doc` on both rebuilds their blocks from the committed CSVs with no dump. All four run
in `make ci`. `--check` on the declarer census is eight synthetic walks -- an inherited slot, an added slot, an
override below the declarer, a sibling's own slot, a slot past the parent's extent, an unnamed declarer
that names the script an edge records, and an edge-less table -- plus the artifact's own arithmetic
against `gap_heads.csv` and the 538 map names.
