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
**3779 of the image's 19507 bodies are unreachable, and the copy the previous note nominated is one of them.** The population is the 14973 bodies `symbols/xbox_functions.csv` names plus the 4534 that start at the image's own `int3` padding and carry no map entry; an edge is a relative `E8` call, a relative `E9` jump, or a dword in any section equal to the start. 3341 bodies have no outside edge at all, and 438 more lose theirs once bodies whose callers are all in that set are followed to a fixpoint -- **3779 in total (3106 the map names, 673 found only by padding), in 433 runs, 799 of them carrying a real name and 1857 starting on a 4-byte boundary.** Both facts about a start are kept per row: a body the map names at an odd address that nothing references may be a definition Ghidra invented over data, and this measurement does not separate that from dead code.

**The unreachable bodies are not scattered: they form 433 runs, and the biggest single run is 362 bodies over 0x49f360-0x4aa5d8, of which 114 start on a 4-byte boundary.** A run is a set of unreachable bodies no more than `0x400` apart; a run that starts and ends inside live code is what a dropped translation unit looks like, and a run whose starts are mostly off a 4-byte boundary is more likely the map's own definitions over data -- which is why each run row carries both numbers.

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
| 0x002e81f0 | FUN_002e81f0 | 0x004cfc88 -> 0x004cfd78 | 0x004cfd78 | FireTexture | FractalTexture |
| 0x002e8260 | FUN_002e8260 | 0x004cfc88 -> 0x004cfe68 | 0x004cfe68 | - | FractalTexture |
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

492 tables: 468 named (465 by an unshared constructor thunk, 2 by the hierarchy a shared thunk forces, 0 derived from a nearest common ancestor, 1 from a base store no registration names), 0 unresolved, 24 with no registration. 2 tables are reached by more than one registration site, so a write-order rule would name them by whichever site happened to be written last.
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
| `inventory/class_registrations.csv` | the site's second-to-last push (the argument position) | 468 sites | -- this is the read |
| `inventory/xbe_class_table_names.csv` | its chosen site's last immediate push | 468 tables | 468 of 468 |
| `inventory/class_constructors.csv` | copied from the table row | 986 rows | 986 of 986 |
| `inventory/class_ctor_bases.csv` | copied from the constructor row | 504 rows | 504 of 504 |
| `inventory/class_records.csv` | the class **script** record's byte length | 333 of 468 classes | not an object size (smaller than it on 266 of 333) |

**The object size has one source, and three of those columns are copies of it.** 468 of the 492 tables have a registration site; their sizes are **200 distinct values, 40 to 348696 bytes**, and the naming census carries the same value on all of them. The 2 constructor-side artifacts copy the column rather than measuring it, so a disagreement between two of the four columns can only come from the site-to-table attribution upstream -- which is what the object root's 40 against the old reader's 56 was.

24 of the 492 tables have **no size at all** -- no registration site resolves to them, so nothing in this tree measures them.
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
| `0x004c78c0` | 31 | -- | 1 | 1 | no | 17 | dtor+wrapper+stub | table:0x004d0318, AudioSubsystem, table:0x004d2400, new +3 |
| `0x004c7958` | 46 | Material | 2 | 2 | no | 40 | ctor, dtor+wrapper+stub | table:0x004c7a10, Modifier, Combiner, MultiPassMaterial +32 |
| `0x004c7a10` | 46 | RenderedMaterial | 1 | 1 | no | 17 | dtor+wrapper+stub | table:0x004c7ac8, ConstantMaterial, TexCoordMaterial, VertexColor +5 |
| `0x004c7ac8` | 46 | BitmapMaterial | 1 | 1 | no | 10 | dtor+wrapper+stub | Texture, ProxyBitmapMaterial, ScriptedTexture, ShadowBitmapMaterial |
| `0x004c7c78` | 34 | CameraEffect | 1 | 1 | no | 16 | dtor+wrapper+stub | SGScreenSniperOverlay, SGScreenMenuPause, SGScreenBlur, SGScreenDOF +6 |
| `0x004c9348` | 30 | -- | 2 | 2 | no | 11 | ctor, dtor+wrapper+stub | table:0x004c93d0, table:0x004d03c8 |
| `0x004c93d0` | 35 | -- | 2 | 2 | no | 4 | ctor, dtor+wrapper+stub | XboxViewport |
| `0x004c94b0` | 30 | -- | 2 | 2 | no | 4 | ctor, dtor+wrapper+stub | XboxClient |
| `0x004cfe68` | 59 | WaterTexture | 2 | 2 | no | 8 | ctor, dtor+unmapped+stub | WaveTexture, FluidTexture, WetTexture |
| `0x004d0318` | 31 | -- | 2 | 2 | no | 11 | ctor, dtor+wrapper+stub | TcpNetDriver, TcpNetDriverXbox, DemoRecDriver |
| `0x004d03c8` | 31 | -- | 3 | 3 | no | 15 | ctor, dtor+wrapper+stub | TcpipConnection, TcpipConnectionXbox, DemoRecConnection |
| `0x004d1468` | 35 | -- | 4 | 2 | no | 28 | ctor, dtor+wrapper+stub, no-body | table:0x004d17a8, Struct, Const, Enum +13 |
| `0x004d17a8` | 37 | -- | 3 | 3 | no | 36 | ctor, dtor+wrapper+stub | IntProperty, BoolProperty, FloatProperty, StrProperty +9 |
| `0x004d2400` | 31 | -- | 1 | 1 | no | 3 | dtor+wrapper+stub | XHVVoiceSubsystem |
| `0x004d34d8` | 32 | -- | 2 | 2 | no | 3 | dtor+wrapper+stub, no-caller | ObjectExporterT3D |
| `0x004d3af0` | 33 | -- | 2 | 2 | no | 4 | ctor, dtor+wrapper+stub | TextBufferFactory |
| `0x004eaea8` | 31 | -- | 1 | 1 | no | 6 | dtor+wrapper+stub | NullRenderDevice, D3DRenderDevice |
| `0x004fa390` | 34 | -- | 2 | 2 | no | 7 | dtor+wrapper+stub, no-caller | ControlChannel, FileChannel, VoiceChannel, ActorChannel |
| `0x004fc4f0` | 30 | -- | 3 | 3 | no | 13 | ctor, dtor+wrapper+stub | table:0x004fc588, Level, DemoPlayPendingLevel, NetPendingLevel |
| `0x004fc588` | 30 | -- | 2 | 2 | no | 8 | ctor, dtor+wrapper+stub | DemoPlayPendingLevel, NetPendingLevel |
| `0x004fcfe0` | 36 | -- | 1 | 1 | no | 6 | dtor+wrapper+stub | ChannelDownload, HTTPDownload |
| `0x005040d0` | 30 | Engine | 2 | 2 | no | 5 | ctor, dtor+wrapper+stub | GameEngine |
| `0x00517860` | 30 | -- | 1 | 1 | no | 7 | dtor+wrapper+stub | table:0x005178d8, IndexBuffer, SkinVertexBuffer |
| `0x005178d8` | 30 | -- | 1 | 1 | no | 11 | dtor+wrapper+stub | VertexBuffer, VertexStreamVECTOR, VertexStreamCOLOR, VertexStreamUV +1 |

**24 tables, 44 store sites in 42 bodies, 2 of the sites in no symbol-map body.** None of the bodies that install one of these tables is a registration thunk target, and one body stores 1 table more than once.

**6 of the 24 tables carry a name from the caller rule**, the join in the `proposed class` column and not this sweep's measurement; the rest are that tool's `constrained` verdict (a caller set whose common ancestor is not one class), its name-clash case, or a table with no named caller at all.

**40 of the 42 installing bodies are reached, and the shape is a base class, not isolated code: 16 are called from a class constructor (13 of the 24 tables have at least one) and 24 from a class destructor.** 103 named classes call an installer body from their own constructor. The other edges are 23 forwarder wrappers (a body whose first call targets the installer), 85 tail jumps of which 84 come from code the symbol map does not cover, and 1 call from no map body at all; 2 bodies have no inbound edge.
<!-- /generated:class-orphan-tables -->

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
synthetic self-test (18 assertions, no dump) and `--check-doc` rebuilds the block from
`inventory/native_island.csv` -- including the hole's bounds and the tail-jump split, which travel in the
artifact's own `census` row so a page rendered from the CSV cannot disagree with the measurement.

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
| the 61 stubs an `E8` calls / a store or push names | 0 / 0 |
| the 61 stubs a dword outside `.text` names | 61 (75 refs) |
| ...of those refs, in a class-table slot (492 mapped + 232 unmapped tables) | 0 of 103 |
| ...whose next dword is a `FuncInfo` header `0x19930520` | 39 of 75 (55 of the 103 refs all 84 carry) |
| ...whose previous dword is the `0xffffffff` unwind terminator | 74 of 75 (102 of 103) |
| installers the 84 tail jumps name | 24 of the 24 (the 61 name 18) |

| gap | bytes | stubs | stub shapes | sites | edges in from outside | edges inside |
|---|---|---|---|---|---|---|
| `0x003f28b8`..`0x003f6d70` (hole) | 17592 | 1589 | eh-handler:325,forward:200,other:1064 | 23 | 0 | 4 |
| `0x003fc208`..`0x00400340` | 16696 | 1551 | eh-handler:293,forward:165,other:1093 | 23 | 0 | 0 |
| `0x004012a8`..`0x00403020` | 7544 | 700 | eh-handler:200,forward:91,other:409 | 9 | 0 | 0 |
| `0x00400a08`..`0x004012a0` | 2200 | 194 | eh-handler:53,forward:29,other:112 | 6 | 0 | 0 |
| `0x00406338`..`0x00409100` | 11720 | 1078 | eh-handler:189,forward:173,other:716 | 5 | 0 | 0 |
| `0x003f27e7`..`0x003f28b0` | 201 | 12 | eh-handler:7,forward:5 | 3 | 0 | 0 |
| `0x003f6d78`..`0x003f71a0` | 1064 | 86 | eh-handler:33,forward:27,other:26 | 3 | 0 | 0 |
| `0x003f7d78`..`0x003fa140` | 9160 | 787 | eh-handler:176,forward:95,other:516 | 2 | 0 | 0 |
| `0x00400348`..`0x00400a00` | 1720 | 152 | eh-handler:56,forward:20,other:76 | 2 | 0 | 0 |
| `0x00403028`..`0x004042e0` | 4792 | 419 | eh-handler:106,forward:52,other:261 | 2 | 0 | 7 |
| `0x00409108`..`0x0040ab70` | 6760 | 608 | eh-handler:167,forward:67,other:374 | 2 | 0 | 0 |
| `0x0040ab78`..`0x0040c2c0` | 5960 | 549 | eh-handler:182,forward:41,other:326 | 2 | 0 | 0 |
| `0x003f71a8`..`0x003f7d70` | 3016 | 252 | eh-handler:81,forward:52,other:119 | 1 | 0 | 0 |
| `0x00404f98`..`0x00406330` | 5016 | 483 | eh-handler:38,forward:189,other:256 | 1 | 0 | 0 |

All 61 sites outside the hole sit in stubs, 61 of which the walker decodes as a forwarder, and `E8`/`E9` edges entering the gaps from outside total 0. Across the whole 84-site population the only channel that names a stub is a `.rdata` dword (103 refs), and none of them is a class-table slot, mapped or unmapped: they sit in exception descriptors -- the word in front of one is the unwind terminator on 102 of the 103 refs, and 55 stand immediately in front of a `FuncInfo` header. So the 84 are compiler exception plumbing rather than dispatch entries, no branch reaches any of them, and the 24 installers keep their 103 constructor callers as the only reachable evidence.
<!-- /generated:native-island-gaps -->

What this does not say: a descriptor names *where* a stub's address is stored, not what the unwinder does
with it. Whether the Xbox exception dispatcher can call one of these forwarders is a question about the
runtime rather than about the image, and nothing here measures it -- the claim is that no branch reaches
them, no store names them, and no class table holds them. `--check` is a synthetic self-test (18
assertions, no dump, writes nothing) and `--check-doc` rebuilds the block from the committed artifact.


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
to its ancestor, a two-store body whose last store is its own, a guarded stub followed through its call, an
`int3`-bounded body that must not inherit its neighbour's store -- and `--check-doc` rebuilds the block from
the committed artifact; both run in `make ci`.

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
