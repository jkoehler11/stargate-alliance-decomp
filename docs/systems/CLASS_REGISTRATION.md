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

Two names in the block below are the committed artifact's, not this page's: the object root's table
`0x004dd2a8` is named `VertexStreamBase` from the literal at its registration site, and no class in
`inventory/classes.csv` has `VertexStreamBase` as its super, while 366 constructors call its installer
-- a contradiction this page does not resolve. The five-argument installer beside it asserts in
`UnObj.cpp` (`0x00073810`, `Core_UnObj`), which is the file `UObject`'s own ctor lives in.

<!-- generated:class-ctor-base -->
| class | ctor | args | recorded call | measured base | base class | verdict |
|---|---|---|---|---|---|---|
| Struct | 0x0003a1b0 | 4 | 0x00062ce0 (VertexStreamBase) | 0x00062ce0 | VertexStreamBase | base-ctor |
| Struct | 0x0003bd30 | 24 | 0x0003b440 | 0x00073810 | VertexStreamBase | base-ctor |
| Struct | 0x0003d560 | 0 | 0x00062ce0 (VertexStreamBase) | 0x00062ce0 | VertexStreamBase | base-ctor |
| State | 0x0003a5f0 | 20 | 0x00062470 (VertexStreamBase) | 0x00062470 | VertexStreamBase | base-installer |
| State | 0x0003d6a0 | 0 | 0x00062ce0 (VertexStreamBase) | 0x00062ce0 | VertexStreamBase | base-ctor |
| Class | 0x0003ad20 | 0 | 0x00062ce0 (VertexStreamBase) | 0x00062ce0 | VertexStreamBase | base-ctor |
| Class | 0x0003ae30 | 4 | 0x0003a1b0 (Struct) | 0x0003a1b0 | Struct | base-ctor |
| Class | 0x0003af60 | 52 | 0x0003a5f0 (State) | 0x0003a5f0 | State | base-ctor |
| VertexStreamBase | 0x00062ce0 | 0 | - | - | - | no-base-call |
| VertexStreamBase | 0x00063a60 | 4 | - | - | - | no-base-call |
| VertexStreamBase | 0x00073810 | 20 | - | - | - | no-base-call |

522 constructors in the image: 509 read a base class off the last call before their table store (1 of those to a body the shape rule calls a destructor), 9 install the table before any call (the base was inlined), 4 end in a body that installs no class table, and 6 of the recorded calls are not the last call and so are not the base the census implied.
<!-- /generated:class-ctor-base -->

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

## Regenerating

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
