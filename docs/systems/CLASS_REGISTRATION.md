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

492 tables: 474 named (465 by an unshared constructor thunk, 2 by the hierarchy a shared thunk forces, 0 derived from a nearest common ancestor, 1 from a base store no registration names, 6 from the tree the child supers describe), 0 unresolved, 18 with no registration. 2 tables are reached by more than one registration site, so a write-order rule would name them by whichever site happened to be written last.
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
| `0x004c7958` | 46 | Material | 2 | 2 | no | 40 | ctor, dtor+wrapper+stub | RenderedMaterial, Modifier, Combiner, MultiPassMaterial +32 |
| `0x004c7a10` | 46 | RenderedMaterial | 1 | 1 | no | 17 | dtor+wrapper+stub | BitmapMaterial, ConstantMaterial, TexCoordMaterial, VertexColor +5 |
| `0x004c7ac8` | 46 | BitmapMaterial | 1 | 1 | no | 10 | dtor+wrapper+stub | Texture, ProxyBitmapMaterial, ScriptedTexture, ShadowBitmapMaterial |
| `0x004c7c78` | 34 | CameraEffect | 1 | 1 | no | 16 | dtor+wrapper+stub | SGScreenSniperOverlay, SGScreenMenuPause, SGScreenBlur, SGScreenDOF +6 |
| `0x004c9348` | 30 | -- | 2 | 2 | no | 11 | ctor, dtor+wrapper+stub | table:0x004c93d0, table:0x004d03c8 |
| `0x004c93d0` | 35 | -- | 2 | 2 | no | 4 | ctor, dtor+wrapper+stub | XboxViewport |
| `0x004c94b0` | 30 | -- | 2 | 2 | no | 4 | ctor, dtor+wrapper+stub | XboxClient |
| `0x004cfe68` | 59 | WaterTexture | 2 | 2 | no | 8 | ctor, dtor+code+stub | WaveTexture, FluidTexture, WetTexture |
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

**40 of the 42 installing bodies are reached, and the shape is a base class, not isolated code: 16 are called from a class constructor (13 of the 24 tables have at least one) and 24 from a class destructor.** 106 named classes call an installer body from their own constructor. The other edges are 23 forwarder wrappers (a body whose first call targets the installer), 85 tail jumps of which 84 come from code the symbol map does not cover, and 0 calls from no map body at all; 2 bodies have no inbound edge.
<!-- /generated:class-orphan-tables -->

## The 250 tables no registration reaches: 38 a constructor bounds, 208 only unnamed bodies store

`docs/UNKNOWNS.md` U40 counts 277 gap bodies a class-table slot names and 269 of them in tables nothing
registers. The full population is **250**: the 232 tables `inventory/native_vtable_shared.csv` pairs with
a mapped table by prefix, plus the 18 mapped tables the class map leaves proposed. The installer's own
call site is the route the entry names, and `tools/class-table-installers.py` walks it over every store
`inventory/native_vtable_reach.csv` already censused.

**A constructor's store bounds a table; it does not name one.** A constructor installs its parent's table
before its own (this page's `base-store-ancestor` rule), so a table a single `ctor_FireTexture` stores is
one of `FireTexture`'s **ancestors'** tables -- the table is at least as deep as that class and never the
class itself. 38 tables carry that bound; the other 208 are stored only by bodies no class names, and 4 by
no store at all. The symbol map's `size` column is a **byte count in decimal**, and this census read it as hex
until 2026-11-20: a short body's span then ran past its own end and the store of the body next door was
credited to it, which is where the 39/209/2 this section used to quote came from. Read at the right base the
same rule moves 12 rows' installer sets and three verdicts, and every mangled C++ name the evidence carried
(the UIX screen constructors, `Core_UnObjBas`, `dtor_Movie`) disappears -- none of the 250 is installed by a
body with a C++ symbol. So the U40 route answers the *reachability* half of its question and not the naming half,
which is why no name is written from it: the six names the tree can carry are already proposed on this
page's own join.

<!-- generated:class-table-installers -->
| the tables no name reaches | tables |
| --- | --- |
| stored by a class constructor (so an ancestor of that class) | 38 |
| stored only by bodies no class names | 208 |
| stored by nothing | 4 |
| **total** | **250** |

The constructor-stored tables carry a bound, not a name: 24 distinct classes, and a table a constructor stores is its base's, never its own. 0 of the population share a prefix with a mapped table (``), which is the family the shared census already recorded.

| class whose constructor stores it | tables it bounds |
| --- | --- |
| `SkeletalMesh` | 5 |
| `PackageMapLevel` | 3 |
| `TextBuffer` | 2 |
| `LinkerLoad` | 2 |
| `XboxViewport` | 2 |
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

## Where the 250's stores write: a class table is `[reg]`, and 36 of the 38 are a member's table

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
| named by the registration path (control) | 474 | 474 | -- | -- | -- | -- | -- |
| nameless: a class ctor stores it | 38 | 2 | 9 | 27 | -- | -- | -- |
| nameless: only bodies no class names store it | 212 | 176 | 2 | 11 | 20 | 3 | -- |

**The 474 named tables are the control: 474 of them are written into the object's own slot and none of them into a member.** A class's dispatch table is the pointer its installer writes at `[reg]`. Of the 250 tables no registration names, **189 are written into an object slot at least once, 49 into a member field, and 23 have no object-field store of their own at all** -- 20 of those only into a stack slot, where the table's address is passed or spilled rather than written into a field and 3 only into a global variable, where the object is a singleton in `.data`.

**The member offsets are a class's own layout, not a shared constant**: `0x8` in 2 tables, `0x28` in 24 tables, `0x2c` in 8 tables, `0x38` in 1 table, `0x64` in 2 tables, `0xb8` in 6 tables, `0xc0` in 1 table, `0xd8` in 1 table, `0xf0` in 1 table, `0x108` in 1 table, `0x134` in 1 table, `0x14c` in 2 tables, `0x164` in 1 table, `0x17c` in 1 table, `0x194` in 1 table.** A field that holds a table pointer is a member of the class whose constructor stores it, so the table is that member's dispatch table and its slots are methods of the member, not of the class -- naming it `<Class>_vt<slot>` would claim a class slot the image does not have.

**38 of the 49 member tables have a class whose own constructor stores the field** (SkeletalMesh (5), ControlChannel (2), TextBuffer (2), XboxViewport (2), PackageMapLevel (2), TcpipConnection (2)), so the member's owner is known even though the table's class is not: these are `<Class>`'s field, and the name the map lacks is the member's, not a class's.

**What this settles for U40.** 36 of the 38 tables a class constructor stores are member tables (27 only a member, 9 the object slot as well), so the `at-least-<base>` bound those tables carry is a bound on the *owner* of a member, not on a class's dispatch table; the reading that a ctor's table is its own or its base's holds only for the 2 that write `[reg]` at all. And 176 of the 212 plain-only tables are written into the object slot by a body no class names, which is the shape of a constructor the map has not named rather than of a table nothing installs.
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
- **`install` counts a stack slot.** Twenty of the 250 are written only at `[esp+disp]`/`[ebp+disp]`, where
  the address is spilled or passed and nothing owns it, so `tools/native-vtable.py`'s `installs` column is
  an upper bound on installations and the previous section's "2 stored by nothing" is 23 in this reading
  (20 stack-only, 3 with no `C7 /0` store at all). The gap between the two counts is the addressing bytes:
  `mov [reg+disp32],imm32` is ten bytes, and `tools/class-orphan-tables.py` read its immediate one byte
  short until this bundle -- which is why the twelve `mov [esi+0xb8],<index table>` stores of the
  LinkerLoad/LinkerSave family were invisible to it.

What this does not say: the partition is the form of the store, not a proof about `this` -- a `[reg+0x28]`
store whose base is an argument rather than the object would read the same. The named population is what
makes it evidence, and it is the part that cannot be argued with: 474 of the 474 class tables the
registration path names are written at `[reg]`, and not one of them only into a member.

## What a member table holds: an interface vtable, and five shapes that repeat

The store-field partition leaves **49 tables whose stores write a member field**, and the reading that
follows from the store's form -- a field that holds a table pointer holds an interface object's table --
is settled by the tables themselves rather than by the store. `tools/class-member-tables.py` reads each of
the 49 at its address and censuses the slots: every slot is a dword into `.text`, so it resolves to a row
of `symbols/xbox_functions.csv` and its body's size says whether the slot is an implementation (a body
longer than 8 bytes), a stub (`ret`, `xor eax,eax; ret`) or `__purecall`. The control is the class table:
**all 492 tables the class-map census covers -- the 474 the registration path names among them -- share one
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

**The 49 member tables are interface vtables, and their slot 0 is not a class slot.** All 492 class tables the class-map census covers (474 of them named by the registration path) share one slot-0 body, `0x00062650`; none of the 49 member tables carry it, and their slot-0 values are 36 distinct bodies. A class dispatch table starts with the entry every class table starts with; an interface object's table starts with its own first method, which is why no slot here can be named `<Class>_vt<slot>`.

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
`dtor_` rows. The control is the 474 tables the registration path already names: 982 of their 990
installer bodies carry a `ctor_<Class>`/`dtor_<Class>` symbol, and the shape rule lands on the same
class for **978 of them (99.6%)**, so it is a measured reading and not an assumption about the 208.

<!-- generated:class-table-installer-shapes -->
| population | tables | installer bodies | returns `this` (ctor) | installs before any call (dtor) | neither | ends in a tail `jmp` | names one table only |
|---|---|---|---|---|---|---|---|
| named | 474 | 990 | 507 | 475 | 8 | 0 | 923 |
| plain-only | 208 | 347 | 192 | 106 | 49 | 1 | 226 |
| no-store | 4 | 0 | 0 | 0 | 0 | 0 | 0 |

**The control is the population the registration path already names.** Of the 990 installer bodies behind the 474 named tables, 982 carry a `ctor_<Class>`/`dtor_<Class>` symbol and the shape rule agrees with that class on 978; 507 bodies return `this`, 475 install the table before any call and 8 read as neither.

**Of the 208 plain-only tables, 192 installer bodies return `this`, 106 install before any call and 49 read as neither; 226 of the bodies install exactly one table, which is a self-install.** 97 tables are installed only by a body that returns `this`, 29 only by a destructor-shaped body, 56 by both, 26 by bodies the rule reads as neither and 0 name no installer body at all.

**A destructor never returns `this`, and the class-ctors signature only matches `mov eax, <reg>`.** Its second form is a load from the frame (`mov eax,[esp+N]` / `[ebp+N]`): 2 of the 106 dtor-shaped installer bodies behind the plain-only tables return `this` that way; the other 104 carry the destructor shape, which an initializer that installs the table into an object it was handed -- and returns something other than `this` -- has as well. That is why the verdict is a reading and not a proof: 29 tables have only such installers, and the artifact names them one body at a time instead of sweeping them into a constructor count.
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

`tools/class-table-installer-shape.py` split the 250 tables no registration names by the shape of the body
that installs them and left the naming to "the body, not the class map". `tools/class-ctor-evidence.py`
reads each of those bodies once and records the three channels a constructor still offers: the literals it
pushes, the map's own names among the functions it calls, and the parent constructor its first call reaches.
`inventory/class_ctor_evidence.csv` carries one row per table of the 724-table population, with the
population's split and the literal, callee, parent and chain columns.

<!-- generated:class-ctor-evidence -->
| population | tables | installer bodies | a printable literal | an `ini:` literal | a literal that is a class name | a literal that is its own class | a named callee | a base-caller edge |
|---|---|---|---|---|---|---|---|---|
| named | 474 | 990 | 26 | 2 | 16 | 0 | 450 | 453 |
| at-least | 38 | 128 | 13 | 2 | 5 | 0 | 31 | 1 |
| plain-only | 208 | 347 | 57 | 3 | 17 | 0 | 138 | 54 |
| no-store | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

| the 208 plain-only tables, by what installs them | tables | a printable literal | a named callee | a base-caller edge |
|---|---|---|---|---|
| installed-by-ctor | 97 | 20 | 55 | 28 |
| dtor-only | 29 | 4 | 16 | 7 |
| ctor-and-dtor | 56 | 15 | 49 | 17 |
| unclassified | 26 | 18 | 18 | 2 |

**The control answers what a constructor literal is, and it is not a name.** 26 of the 990 installer bodies behind the 474 named tables push a printable UTF-16 literal; 16 of those tables push a literal that is a class name, and **none of the 474 pushes its own class**. The 2 `ini:` literals name the class that *declares* the config property -- `DemoPlayPendingLevel` pushes `ini:Engine.Engine.DemoRecordingDevice` and `NetPendingLevel` `ini:Engine.Engine.NetworkDevice` -- so a literal is a field default or a config section, and the class it can name is the declaring one.

**Of the 246 nameless tables -- 208 plain-only and 38 whose table a constructor bounds -- 70 push a printable literal and 169 call a named function.** 5 push an `ini:` section, and that form does name a class: `0x4c93c4` (`ini:Engine.Engine.Canvas`, `ini:Engine.Engine.Input`); `0x4c93c8` (`ini:Engine.Engine.Canvas`, `ini:Engine.Engine.Input`); `0x4c93d0` (`ini:Engine.Engine.Canvas`, `ini:Engine.Engine.Input`); `0x4fc610` (`ini:Engine.Engine.DemoRecordingDevice`); `0x5139f4` (`ini:Engine.Engine.NetworkDevice`). The other 65 are field defaults and data addresses -- the commonest are `Engine` (21), `new` (15), `XboxLiveOnlineTask` (15), `Core` (9), `Official.ini` (5), `SavePackagesAsOfficial` (5) -- and 22 of the 246 push a class name (a package, or a class they register), never their own. So the literal channel places 5 of the 246 by config class and describes the fields of the rest, and names none of them.

**The named callees place a table even when nothing names it.** 169 of the 246 nameless tables call at least one of the map's own `Core_*`/`Engine_*` rows; 31 signatures are shared by two or more of them and cover 116 tables, the largest being 33 tables that call the same set. A signature is not a name -- two classes in one subsystem share it -- which is why it is reported as a family and not as a label.

**The base-caller edge is the parent's constructor, and the named population holds it to the script tree.** A table's own installer is the body whose *last* store is that table, and its first call that reaches another table's installer is its parent constructor, followed through the null-check thunk. On the named tables the edge is the class's script `super` on **167**, a further ancestor on **211** (a script-only class installs its table from a body that calls its nearest native base), and on **53** the class has no script chain; **0 contradict**. Of the 246 nameless tables 55 get an edge -- 7 to a named class, 48 to another nameless table -- and 9 climb a chain to a named class, the deepest 3 levels.

**The 4 tables with no installer body are not tables: each is a window into a dword run, or a run the census cut short.** The store site each records lies in no body of the map, and the run of address-like dwords around it is tiled by tables the census already lists: `0x4f70a0` (2 slots) dword run 0x004f6ff8+44; this address at offset 42; 3 listed table(s) in the run, 2 with an installer body; the recorded store site 0x000e1706 lies in no body of the map; `0x5172ec` (5 slots) dword run 0x005172ec+27; this address at offset 0; 1 listed table(s) in the run, 0 with an installer body; the recorded store site 0x001df51e lies in no body of the map; `0x5181dc` (3 slots) dword run 0x005181d0+6; this address at offset 3; 2 listed table(s) in the run, 1 with an installer body; the recorded store site 0x00200606 lies in no body of the map; `0x53f7c4` (5 slots) dword run 0x0053f770+31; this address at offset 21; 2 listed table(s) in the run, 1 with an installer body; the recorded store site 0x003313ce lies in no body of the map; 4 of 5 slots are not 4-aligned, so they are in-function offsets.
<!-- /generated:class-ctor-evidence -->

The 97 tables a constructor alone installs -- the population the previous read left -- are the
`installed-by-ctor` row of the second table: 20 of them push a literal, 55 call a named body and 28 have a
base edge, so the channel is thinner there than in the population as a whole and still says nothing about a
name. What it does say is in the artifact, one table at a time.

**The literal channel is closed by its own control, and closed in a specific way.** A constructor literal is
a field default or a config section, and the class it can name is the class that *declares* the property,
never the object's class: `DemoPlayPendingLevel` pushes `ini:Engine.Engine.DemoRecordingDevice` and is not
`DemoRecordingDevice`. Across the 474 named tables **no constructor pushes its own class's name** while 16
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
nameless table it says which of the 250 is above which.

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
| population | tables | installer body called directly (`E8`) | a caller class names the owner | no direct caller | no installer body |
|---|---|---|---|---|---|
| named | 474 | 471 | 101 | 1 | 2 |
| at-least | 38 | 37 | 9 | 1 | 0 |
| plain-only | 208 | 161 | 16 | 27 | 20 |
| no-store | 4 | 0 | 0 | 0 | 4 |

**The control: on the 474 named tables the callers are the derived classes, and their deepest common ancestor is the table's class on 62, one of its descendants on 39 and an unrelated class on 2.** 368 named tables are called only from bodies with no `ctor_`/`dtor_` symbol, and 1 have no direct caller at all.

**Of the 246 nameless tables, 45 are built inside a class's own constructor -- 22 by a group of sibling classes, so the table is the base their constructors and destructors reset to, and 23 by a single class whose own table is a different address, a second table that class builds. 153 are called only from bodies with no class symbol and 28 are never called directly.**

**Of the 58 tables whose installer is destructor-shaped or neither, 10 have callers that carry a `ctor_`/`dtor_` class symbol, and there the callers are the sibling destructors that reset the pointer to this table; 28 are called only from bodies with no class symbol, 12 are never called directly and 8 have no installer body.**
<!-- /generated:class-installer-callers -->

**The direct call is the rule, not the exception, and the control is the class's own lineage.** Of the
474 named tables, 471 have an installer body that some `E8` in `.text` calls, and 101 of the 103 whose
callers carry a `ctor_`/`dtor_` class symbol land the deepest common ancestor on the table's class (62) or on
one of its descendants (39) -- **101 of 103, the two strangers being the `Material`/`RenderedMaterial`
families whose common base has no script row.** Only two named tables have no direct caller at all, so the
installer is reached by a call and not by a pointer except in the exception. What the 368 named tables
called only from unnamed bodies say is the other half: their callers are real bodies the symbol map still
has no name for, which is U40's population met from this side.

**Of the 246 nameless tables, 45 are built inside a class's own constructor.** Twenty-two are called by a
group of sibling classes -- `AudioSubsystem`/`Input`, `D3DRenderDevice`/`NullRenderDevice`,
`ChannelDownload`/`HTTPDownload`, `IndexBuffer`/`SkinVertexBuffer`, the five `VertexStream*` classes,
`TcpNetDriver`/`TcpNetDriverXbox`/`DemoRecDriver`, `TcpipConnection`/`TcpipConnectionXbox`/`DemoRecConnection`,
`Level`/`NetPendingLevel`/`DemoPlayPendingLevel`, `ActorChannel`/`ControlChannel`/`FileChannel`/`VoiceChannel`
-- so the table is the base their constructors and destructors reset to, and the group names the family
around it. Twenty-three are called by exactly one class that **already owns a different table**: `Texture`
(18 slots at `0x0051ffd8`, its own table `0x004cfba0`), `StaticMesh` (two 9-slot tables and its own
`0x0051dd28`), `Cubemap` (15 against `0x005205c8`), `VertMesh` (11 against `0x00520fd8`),
`SkeletalMeshInstance` (9 against `0x0051c188`), `SkeletalMesh` (four 2-slot tables against `0x0051c310`),
`D3DRenderDevice` (65 against `0x0052b460`), `XboxViewport` (1, 2 and 56 slots against `0x004c9618`),
`XboxClient`, `GameEngine`, `TextBufferFactory`, `ObjectExporterT3D`, `LinkerLoadGame`/`LinkerSaveGame` and
`XHVVoiceSubsystem`. Those are not class tables the packages forgot: the class is named and the array is a
second one its constructor writes -- 12 at `[reg]` and 11 into a member field at least once, so
`inventory/class_table_store_fields.csv` is the discriminator between a second class table and a member's
interface, and the 12 written only at `[reg]` are the residue a per-body read still has to name.

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
other 48 split 28 called only from unnamed bodies, 12 never called directly and 8 with no installer body,
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
| named by the registration path (control) | 474 | 345 | 100 | -- | -- | -- | -- | 29 | -- | -- | -- |
| nameless: a class ctor stores it | 38 | 12 | 5 | 2 | -- | 1 | -- | 17 | 1 | -- | -- |
| nameless: only bodies no class names store it | 212 | 17 | 34 | 11 | 27 | 32 | 5 | 62 | 20 | 4 | -- |

**The control: of the 474 named tables, 345 are installed with the caller's own `this` and 100 with a base level's.** A destructor (and a constructor) level writes its own table at `[reg]` and then calls the next level's with the same object, so a caller that owns a different table is the evidence that the callee's table is an ancestor's. 29 of the named tables are mixed -- a class constructed both as a member and as a temporary by different callers reads that way, which is a fact about its callers rather than about the table.

**Of the 250 nameless tables, 29 are installed with a caller's own `this`, 39 with a base level's, 13 through a member's address, 27 into a frame slot, 33 into an allocation and 5 into a global.** A frame-slot or allocation install is an object built in place -- a temporary, an argument passed by value, or an element of a local array -- so those tables belong to classes the build only ever instantiates as a local; a global install is a singleton in `.data`, constructed behind a one-shot flag. None of the four is a table nothing installs, which is what the store's own form made them look like. The 20 tables `inventory/class_table_store_fields.csv` calls `frame-only` -- their own store writes a stack slot, so the address is spilled rather than installed -- split when the storing body's callers are read: 4 into a frame slot, 2 into an allocation, 2 into a global, 1 with the caller's own `this`, 6 mixed and 5 with no direct caller.

**A second table is a different object, not a second array: of the 23 tables the caller census reads as a second table, 12 are written only at `[reg]` and 11 at least once into a member field.** Read at the call site, the 12 `[reg]` ones resolve to 5 base levels, 4 members of the caller, 1 to a slot or an allocation and 2 mixes -- **not one of them is the owner's own object**, which is what "the class builds it a second time" would have required. The base levels are the same tables `inventory/class_table_parent_edges.csv` records as those classes' `parent_va`, reached from the call sites instead of from the destructor thunk.

**The member offset is the same measured twice.** For 6 second tables the store's own `[reg+disp]` and the caller's `lea ecx,[reg+disp]` are both in the image, and they agree on 6 and differ on 0: 0x0051bc08 `0x164 0xd8`, 0x0051bc10 `0x108 0x134`, 0x0051bf18 `0x194 0xc0`, 0x0051bf20 `0x14c`, 0x0051bf28 `0x17c`, 0x0051d854 `0x14c`.

**5 tables are a module singleton.** 3 of them are stored into the global they live in -- `0x005174a8` into 0x7ab9e8 and 0x7aba2c, `0x00517588` into 0x7ab9d8 and 0x7aba1c, `0x0053a194` into 0x7b9360 -- by a body behind a one-shot flag (`test`/`or` on a byte beside the object, then the store), so the object is a `static` in `.data`, not a class instance. The other 2 are never passed anything but a global's address as `this`, so the object is one too -- it is simply built somewhere this read cannot follow.

**The register-carried install is real but rare: 1 of the 724 tables has one, and none of them is a table without an immediate store at all.** The pair `mov reg,<table>` / `mov [reg2(+disp)],reg` puts the address in the first instruction, so a census that reads the store's own operand sees a register; here the channel adds nothing to the population, which is the negative that keeps the immediate census honest rather than a route still to walk.

**What this settles for U40.** The `[reg]` store is the object's own slot *relative to the body that writes it*, so the store's form alone cannot separate a class table from a member's: in 78 of the 250 nameless tables the store lands on an object that is not the caller's own, and 39 of those are an ancestor class's table, which is a name the caller supplies. What is left needing a per-body read is the 79 mixed tables -- a receiver whose `ecx` comes out of arithmetic (`lea ecx,[eax+esi*4]`), an inlined constructor, or two callers that disagree -- and the 21 tables no body calls directly.
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
that comes out of arithmetic beyond one `lea` (`lea ecx,[eax+esi*4]` is reported as `indexed`, and the two
tables it reaches are left in the mixed group), and it does not decide whether a mixed table is two real
construction sites or one caller the decoder mistook -- that is a per-body read of the kind the last two
sections have been doing, and the artifact keeps every call site's resolved chain so the next read starts
from the row rather than from the census.

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
