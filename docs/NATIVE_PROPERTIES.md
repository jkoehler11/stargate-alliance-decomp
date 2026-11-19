# The property system in `default.xbe`

Status: measured from the build (PLAN 4.3, property half). What is established here is the
**type space**: the reserved name indices the property type names occupy, the fifteen property
classes and their sizes, the field offsets a consumer is seen reading, and the shape of the
routine that walks a struct's properties. The tagged tag record (`FPropertyTag`) is measured in both directions (§6): the reader at
`0x0003ceb0`, the writer at `0x0003cd70`, and the independent parser that reads this build's
checkpoints agrees with both.

Everything below comes from decompiled bodies exported with
`tools/ghidra/ghidra_scripts/ExportAt.java`; those files are game-derived and stay under `$WORK`.
What the repository carries is the derived measurement, `inventory/property_classes.csv` and
`inventory/property_types.csv`, produced by `tools/property-map.py`:

```
python3 tools/property-map.py --exports $WORK/property --exports $WORK/property2 -o inventory
```

## 1. The property type names are reserved name indices 1..16

`0x0005c080` (`UnName.cpp`, assert `Initialized==0` at line `0x9d` — the routine whose file
attribution comes from that assert) initialises the name system: it clears 4,096 dwords at
`0x006ba0c8`, sets the initialised flag at `0x006be0c8`, and then registers 325 names, each at an
**explicit index** rather than by position:

```
iVar5   = thunk_FUN_003e67ed(L"ByteProperty", L"NameEntry");   // wide-string length
puVar2  = alloc(iVar5 * 2 + 0xe);                              // 0xc header + wide name + NUL
*puVar2 = 1;                                                   // name index
puVar2[1] = 0x4000000;                                         // name flags
puVar2[2] = 0;                                                 // hash chain
thunk_FUN_003e68ad(puVar2 + 3, L"ByteProperty");               // wide name at +0x0c
FUN_0005be10(puVar2);                                          // insert into the name table
```

That gives a name-entry layout of `+0x00` index, `+0x04` flags, `+0x08` chain, `+0x0c` a wide
name, and an allocation of `2*len+0xe` bytes — the entry is 0xc bytes of header plus the wide
string and its terminator, which is why the length is doubled.

The first seventeen registrations are the property type space, contiguous and in declaration
order. This is the block the tagged format's type comparisons can rely on (name index, not a
string compare):

| name index | name | flags |
|---|---|---|
| 0 | `None` | `0x4000400` |
| 1 | `ByteProperty` | `0x4000000` |
| 2 | `IntProperty` | `0x4000000` |
| 3 | `BoolProperty` | `0x4000000` |
| 4 | `FloatProperty` | `0x4000000` |
| 5 | `ObjectProperty` | `0x4000000` |
| 6 | `NameProperty` | `0x4000000` |
| 7 | `DelegateProperty` | `0x4000000` |
| 8 | `ClassProperty` | `0x4000000` |
| 9 | `ArrayProperty` | `0x4000000` |
| 10 | `StructProperty` | `0x4000000` |
| 11 | `VectorProperty` | `0x4000000` |
| 12 | `RotatorProperty` | `0x4000000` |
| 13 | `StrProperty` | `0x4000000` |
| 14 | `MapProperty` | `0x4000000` |
| 15 | `FixedArrayProperty` | `0x4000000` |
| 16 | `PointerProperty` | `0x4000000` |

Beyond the block the indices are sparse, not a counter: 17–19 are unused, `Core` is 20, `Engine`
21, `Editor` 22, `Gameplay` 23, the value type names follow at 80 (`Byte`), 82 (`Bool`), … and the
highest registration reaches 1200. Two flag values appear: `0x4000400` on `None` and on the value
type names, `0x4000000` on the property classes and package names. The tool recovers 294 of the
325 registrations with a strict pattern; the 1..16 block above is complete, and the unmatched
entries are unrelated names whose code shape differs.

`FName::StaticInit` is the likely identity — the source file, the `Initialized==0` guard and the
4,096-slot table all point at the name system — but that name is an inference from UE2, not
something this build states.

## 2. Fifteen property classes, each with its own static-class constructor

`UnProp.cpp` holds fifteen ~183-byte routines, each of which allocates a class object and fills it
through `FUN_0003af60`, naming its class in a wide literal and passing the **object size** the
class claims:

```
iVar2 = FUN_0003af60(0, 0x70, 0x8000, 0, 0, 0, 0, L"ByteProperty", uStack_4, L"System",
                     0x4084004, &LAB_0007dfe0, &DAT_000624d0);
if (iVar2 == 0) FUN_00058a30("ReturnClass", "…\Core\Src\UnProp.cpp", 0x1af);
```

This is the same class-registration macro the game modules use (PLAN 4.6): outer `System`, a size
literal, class flags, a vtable, then an assert on the result. Measured roster
(`inventory/property_classes.csv`):

| address | class | object size | class flags | vtable |
|---|---|---|---|---|
| `0x00077860` | `Property` | 0x6c | `0x8001` | `LAB_00034650` |
| `0x0007c7b0` | `ByteProperty` | 0x70 | `0x8000` | `LAB_0007dfe0` |
| `0x0007c870` | `IntProperty` | 0x6c | `0x8000` | `LAB_0007dff0` |
| `0x0007c930` | `PointerProperty` | 0x6c | `0x8000` | `0x7e000` (no thunk) |
| `0x0007c9f0` | `DelegateProperty` | 0x70 | `0x8000` | `LAB_0007e0b0` |
| `0x0007cab0` | `BoolProperty` | 0x70 | `0x28000` | `LAB_0007e010` |
| `0x0007cb70` | `FloatProperty` | 0x6c | `0x8000` | `LAB_0007e020` |
| `0x0007cc30` | `ObjectProperty` | 0x70 | `0x18000` | `LAB_0007e030` |
| `0x0007ccf0` | `ClassProperty` | 0x74 | `0` | `LAB_0007e040` |
| `0x0007cdb0` | `NameProperty` | 0x6c | `0x8000` | `LAB_0007e050` |
| `0x0007ce70` | `StrProperty` | 0x6c | `0x8000` | `LAB_0007e060` |
| `0x0007cf30` | `FixedArrayProperty` | 0x74 | `0x8000` | `LAB_0007e070` |
| `0x0007cff0` | `ArrayProperty` | 0x70 | `0x8000` | `LAB_0007e080` |
| `0x0007d0b0` | `MapProperty` | 0x74 | `0x8000` | `LAB_0007e090` |
| `0x0007d170` | `StructProperty` | 0x70 | `0x8000` | `LAB_0007e0a0` |

`Property` is the only one with flags `0x8001`; `BoolProperty`, `ObjectProperty` and
`ClassProperty` are the three whose class flags differ (`0x28000`, `0x18000`, `0`). The sizes are
the classes' own claims — no accepted allocation was observed to confirm one, so treat them as
declared rather than measured-as-allocated.

The property classes have **no package exports**: `Core.u` carries 7 class exports against 643
property exports (`inventory/packages.csv`). Their class objects are built by this C++ code at
startup and never deserialised from the package, which is why the roster exists only in the XBE.

## 3. Field offsets a consumer reads

The walk at `0x0003bdc0` iterates a struct's property list and calls each property's serializer,
and the destruction path at `0x000796b0` walks the same fields from the other side. Together they
establish:

| offset | what the code does with it | evidence |
|---|---|---|
| `+0x2c` | next property in the chain: `local_18 = (int *)local_18[0xb]` ends the loop | `0x0003bdc0` |
| `+0x34` | element count: `0 < local_18[0xd]` bounds the per-element loop; `< 1` skips it | `0x0003bdc0`, `0x000796b0` |
| `+0x38` | element stride: `local_18[0xe] * iVar7 + local_18[0x12]`, and `[this+0x38] * i` when destroying | `0x0003bdc0`, `0x000796b0` |
| `+0x3c` | flags: masked with `0x1000`, `0x2000`, `0x20000000`, `0x80000`; `0x4000008` is OR-ed in by `ClassProperty`'s serializer | `0x0003bdc0`, `0x00078730` |
| `+0x48` | value offset: added to the element address; asserted non-zero before destruction | `0x0003bdc0`, `0x000796b0` |
| `+0x6c` | the "inner" pointer of the wide classes: stored by the array-shaped constructor, read by `ClassProperty` and by the destruction path | `0x00078c20`, `0x00078730`, `0x000796b0` |
| `+0x70` | the second pointer of the wide classes: the count written by the array-shaped constructor, `ClassProperty`'s `MetaClass` (asserted by name after serialising it) | `0x00078c20`, `0x00078730` |
| vtable `+0x94` | a two-argument **comparison** of a value against another: the walk calls it once, with
two addresses, and branches on the returned `bool` (`0x0003c340`) | `0x0003bdc0`, `0x0003c340` |
| vtable `+0xa0` | the per-class **value serialiser** (`SerializeItem`), which the array classes dispatch on
their own `Inner` | `0x00078a56` (§7) |
| vtable `+0xc0` | per-element destruction: `(**(code **)(*element + 0xc0))(value)` | `0x000796b0` |

The two-pointer constructor at `0x00078c20` asserts `!Inner`, `Property`, `InCount>0` and then
writes `[this+0x6c] = Inner` and `[this+0x70] = InCount`. That pair fits the 0x74-byte classes
(`ClassProperty`, `FixedArrayProperty`, `MapProperty`) — two pointers past the base — but which of
them owns this exact constructor is not established; `ClassProperty`'s own serializer is a
different routine (`0x00078730`).

## 4. The routine that walks the properties (`0x0003bdc0`)

Its first assert is `Ar.IsLoading() || Ar.IsSaving()` (`UnClass.cpp:0x20f`), tested as two dwords
at `[archive+0x10]` and `[archive+0x14]`. It then walks the struct's children from `[this+0x3c]`
and, for each property whose flags are clear and whose count is positive, computes the element
address and calls `SerializeItem`. Two branches matter:

- **Nested structs are recursed into only in the text path**, or for a property flagged `0x80000`:
  `if ((archive[0xf] == 0) || ((property[0xf] & 0x80000) != 0))`. The archive flag at `+0x3c`
  therefore selects between the tagged (text) property stream and the binary one, and this is the
  same routine for both.
- The tagged path **resolves the tag's name against the struct**: `Core_UnClass("Core")` lazily
  fetches the `Class` class object, the walk tests membership by climbing a chain from
  `[this+0x24]` via `+0x28`, and a name read is compared with `__wcsicmp`. That is where
  `"Property %s of %s not found"`, `"Property %s of %s is not serializable"` and the type-mismatch
  message come from.

Measured names for the helpers it calls: `0x0003ceb0` reads the next tag name into a local buffer,
`0x0003cd70` writes one, `0x0003cb90` touches the FString it compares — the three are used in
pairs on both sides of the same stream, which is consistent with a tag writer/reader pair but does
not establish the tag record's field order.

**A tag whose name the object does not declare costs the tag, not the object.** The tagged path reads a tag
(`0x0003ceb0`), then searches the property list at `[this+0x64]`: each entry's name index is at `+0x20` and the
next entry at `+0x4c` (`0x3be62`-`0x3be7a`). When nothing matches it takes the not-found branch at `0x3be94`,
formats `Property %s of %s not found` (the string is at `0x4d4c14`, pushed at `0x3beb1`) and falls into the
same handler the type-mismatch and array-bounds refusals use (`0x3c155`, reached by the tail `jmp` at
`0x3bebc`). That handler logs `Skipping %i bytes of type %i` (`0x4d4a20`, pushed at `0x3c187`) — unless the
name is `XLevel` (`0x4d4aac`, compared at `0x3c165`), the fork's silent case — then reads the tag's `Size`
bytes one at a time (`0x37040` called from the loop at `0x3c19a`, the count taken from the tag's own `Size`
field) and jumps back to the tag loop at `0x3be40`. So an unknown name is logged twice, as not found and as
skipped, and every later tag in the object is still applied: the object is not refused, truncated or left
half-read. The body is 1,540 bytes per `symbols/xbox_functions.csv`, so this is the whole routine.

This is the case a map hits when a class revision drops a property: `ACTION_SetIMusic`'s five `EndType` tags
are declared by no class `GamePlay.u` ships, and the walk above is why the placements still load
(`inventory/tag_origin.csv`, `docs/MAP_ACTION_PARAMETERS.md`). The skip needs only the tag's own `Size`, which
is why a stale tag is harmless where a stale *type* is not: a mismatch takes the same handler, so a name that
is present with the wrong type is skipped exactly like an absent one.

## 5. Fork behaviour worth carrying into 4.8

- `0x0003a220` carries `"DecoHack: skipping property %s"` next to `"Binary-serialized struct
  property %s of %s does not exist on disk.  Skipping."` — a Perception-specific tolerance in the
  struct-property path, and the only `DecoHack` string in the image.
- There is **no `GPropPointers`** in this build: the string does not occur, and no global array of
  property class pointers is referenced. The class objects come from the fifteen per-class
  constructors above. PLAN 4.3's wording ("the property/`GPropPointers` tables") described the
  expectation, not something the image contains; what exists is this roster.
- The property type names are name indices rather than strings because native code compares
  indices; that is also why the type name is worth recording here instead of re-deriving it from
  `Core.u`.

## 6. The tag record (U10)

A checkpoint is a package whose exports carry *tagged* properties, so the record that frames each
property is the thing a rebuilt save has to reproduce. The reader is `0x0003ceb0`
(`UnClass.cpp`), called from the walk in §4 with an archive and a tag buffer:

```
0x0003ceb0(archive, tag):
    read int32 into tag+0x04                     ; Name  (an FName index; 0 = None ends the list)
    if (tag+0x04 == 0) return                    ; end of this object's property list
    copy name into the message buffer            ; for the "Property %s of %s ..." diagnostics
    read byte into tag+0x01                      ; Type
    tag+0x00 = Type & 0xf                        ; property type (low nibble)
    if ((Type & 0xf) == 10) read int32 into tag+0x08     ; StructName, only for a struct property
    switch (Type & 0x70) {                       ; Size class (bits 4-6)
      0x00: Size = 1        0x10: Size = 2      0x20: Size = 4
      0x30: Size = 12       0x40: Size = 16
      0x50: read byte      0x60: read uint16    0x70: read int32
    }                                            ; stored at tag+0x0c
    if (Type's high bit is clear || (Type & 0xf) == 3) tag+0x10 = 0   ; no array index on disk
    else read a compact int into tag+0x10        ; ArrayIndex
```

which gives this on-disk order:

| # | field | encoding |
|---|---|---|
| 1 | **Name** | int32 FName index; `0` (`None`) terminates the object's property list |
| 2 | **Type** | one byte: low nibble = property type, bits 4–6 = size class, bit 7 = "array index follows" (for a bool property that bit is the value instead) |
| 3 | **StructName** | int32 FName index, present only when the type nibble is 10 |
| 4 | **Size** | implied by the size class (1, 2, 4, 12 or 16) or an explicit 1-, 2- or 4-byte value |
| 5 | **ArrayIndex** | compact int, present only when bit 7 is set and the type is not bool |
| 6 | **value** | `Size` bytes, decoded by the property's own serializer |

Two things make this citable rather than plausible:

- **The type nibble is the reserved name index.** The property type names occupy name indices 1..16
  in declaration order (`inventory/property_types.csv`, §1), and each property class's `vtable+0xcc`
  returns exactly that value: the shared accessor is `mov eax,[ecx+0x24]; mov al,[eax+0x20]; ret`
  (the byte the class object stores at `+0x20`), and `ClassProperty` overrides it with
  `mov al,5; ret` (`0x0007dbb0`). So the tag's type byte and the property class agree by
  construction.
- **An independent parser of the same bytes agrees field for field.** `tools/uelib`
  (`UDefaultProperty.DeserializeTagUE1`, selected because this build is version 130 and the refactor
  lands at 223) reads: name reference, `info = ReadByte()` with `InfoTypeMask = 0x0F`,
  `InfoSizeMask = 0x70`, `InfoArrayIndexMask = 0x80`, a struct name when the type nibble is
  `StructProperty`, a packed size (`0x00..0x40` = 1/2/4/12/16, then byte/word/int), and an array
  index only when the mask bit is set — the same order, the same masks, the same size classes. That
  parser reads all 9 checkpoints and 19,080 tags of this build with zero failures
  (`docs/formats/sgs-checkpoint.md`).

The walk in §4 then uses these fields: it looks the name up in the object's property list
(`property+0x20` is the name index it compares against), refuses a type mismatch
(`"Type mismatch in %s of %s: file %i, class %i"`), bounds-checks the array index against `ArrayDim`
(`"Array bounds in %s of %s: %i/%i"`), compares a struct tag's struct name with the property's
(`"Property %s of %s struct type mismatch %s/%s"`), and warns when the bytes consumed by the value do
not match `Size` (`"Tag %s of %s size mismatch.  Serialized %d but expected %d"`). Bytes it cannot use
are skipped with `"Skipping %i bytes of type %i"`, and a property named `XLevel` is skipped silently —
the fork's own special case. **A tag the object does not declare is the same skip**: the name search fails,
the walk logs `"Property %s of %s not found"`, and the tag's `Size` bytes are consumed before the next tag is
read (§4) — so a save or a map written against a class revision with one more property still loads.

**The on-disk writer is the reader, and the corpus can test it.** `0x0003cd70` has exactly one caller
in the whole image -- `0x0003c368` in the walk below -- and the call right after it (`0x0003c373`) enters
`0x0003ceb0` again, so the routine that *reads* a tag is the routine that *writes* one. That makes the whole
record testable on shipped data: the fields this build's own reader recovers are re-encoded with the writer's
rules and compared with the bytes the file holds (§6's second subsection).

### The writer, `0x0003cd70`

The save side is a separate routine, and it fills the same fields in the same order, so the layout is
now measured in both directions:

```
FUN_0003cd70(&tag, archive, property, arrayIndex, &value):
    tag[1] = property->vtable[0xcc]()          ; type id, byte
    tag[0] = same                              ; kept in the struct's first byte too
    *(int*)(tag+4)  = property[0x20]           ; Name: the property's name index
    *(int*)(tag+8)  = 0                        ; StructName
    *(int*)(tag+0xc) = 0                       ; Size, filled below
    *(int*)(tag+0x10) = arrayIndex
    s = struct_of(property);
    if (s) *(int*)(tag+8) = *(int*)(s+0x6c)+0x20 ; StructName, for a struct property
    measure = serialize value into a scratch archive   ; Size is measured, not derived
    *(int*)(tag+0xc) = measured_size
    size class:  1 -> 0x00   2 -> 0x10   4 -> 0x20   12 -> 0x30   16 -> 0x40
                 < 0x100 -> 0x50 (1-byte size)   <= 0x10000 -> 0x60 (2-byte size)   else 0x70 (4-byte)
    tag[1] |= size_class
    if (arrayIndex != 0 || bool_property_value) tag[1] |= 0x80
```

Three things follow that the reader alone could not settle:

- **`Size` is the measured serialized length**, not the property's element size: the writer runs the
  value through a scratch archive and stores the bytes it produced. That is why `0x0003bdc0` warns on
  `"Tag %s of %s size mismatch.  Serialized %d but expected %d"` — the number is an actual byte count.
- **The size-class thresholds are exact**: under `0x100` the size goes on disk as one byte (`0x50`),
  at most `0x10000` as two (`0x60`), more as four (`0x70`), and the five implied classes cover
  1/2/4/12/16. A rebuilt writer must choose the class the same way or the reader will consume the
  wrong number of bytes.
- **Bit 7 means "extra field follows", and for a bool property it carries the value**: the writer sets
  it when the array index is non-zero, or when the property is a bool whose value is true, which is
  exactly what the reader checks before reading an array index.

### The on-disk half: the walk fills a tag and hands it back to the tag routine

`0x0003bdc0`'s save branch, decompiled (`~/sg1/work/module-export/Core-800/0003bdc0_Core_UnClass.c`), is three
calls in order -- the element serializer through the property's own vtable, then the tag:

```
iVar4 = (**(code **)(*local_18 + 0x94))(iVar4 + param_3, iVar6);   ; the value's serializer
...
FUN_0003cd70(piVar1, local_18, iVar7, iVar9);   ; 0x0003c368: fill the tag struct for this property
FUN_0003ceb0(piVar1, local_14);                 ; 0x0003c373: write it -- the reader's own routine
Core_UnClass(piVar1, local_18, iVar9, 0);       ; 0x0003c384: 0x00038c30, the value
```

The three addresses are the byte scan's, not the decompiler's
(`python3 tools/vtable-reach.py <xbe> --va 0x3cd70 --va 0x3ceb0 --va 0x38c30`): `0x0003cd70` has **one** `E8`
site in the image, `0x0003c368`, nine bytes before the `0x0003c373` that re-enters `0x0003ceb0`; the reader
has two, `0x0003be4e` on the load path and that `0x0003c373` on the save path. So the tag bytes are the
reader's own operators -- the name and the struct name through the archive's vtable `+0x1c`, the type byte and
the explicit sizes and the array index through `0x00037040`/`0x00037080`/`0x00037110` -- and a rebuilt save has
to satisfy the same routine it was read by.

**The value writer is called twice per tag, and that is where `Size` comes from.** `0x00038c30` has four call
sites: three in the walk's own body (`0x0003bf73`, `0x0003c1ed`, `0x0003c384`) and one at **`0x0003ce00`, inside
`0x0003cd70` itself** -- the measuring pass, which serialises the value into a scratch archive so the tag can
record how many bytes it produced, and then the emit at `0x0003c384` writes them. `0x00038c30` folds a bool's
value into the tag's type byte and otherwise dispatches `[property+0xa0]`, so the value is each property
class's own serializer output.

The corpus half of that claim is the block below: **every property tag in the 73 map packages and the 9
checkpoints re-encodes to the bytes the file holds**, and the per-type table is what the value half looks like
from the outside -- the width of each type's serialized value, which is the part of the value a rebuilt writer
must produce before any of its internal layout can matter.

<!-- generated:tag-encodings -->
**Every one of the 1,212,640 property tags counted here re-encodes to the bytes the file holds**: 1,212,640 headers over 137,427 blocks in 73 map packages and 9 checkpoints; the per-file rows below count each block's terminating zero index as well, so they sum to the tags plus the blocks.

The walk uses the shipped locator and then the writer's rules as a second test: 1,536 block(s) it chose hold a tag those rules cannot produce (20,691 tag(s)), and 1,517 of those payloads carry a later start whose whole block re-encodes -- so the encoder also settles a block start the count-of-names rule gets wrong. The counts below are over the blocks that agree.

The rule that reproduces them: the **name** is a compact index -- 1,024,134 tags in one byte and 188,506 in two or more, so a four-byte int32 reader (what §6's table said) would desync on 188,506 of them; the **type byte** is the property type OR-ed with the size class the measured `Size` picks, implied widths first and the explicit classes after; the **array index** is packed one byte to `0x7f`, two to `0x3fff`, four above, and the shipped corpus writes 3,243 one-byte, 6 two-byte and 14 four-byte index(es), largest 1,073,741,823.

| size class | tags | what it says on disk |
|---|---|---|
| `0x00` | 334,672 | implied 1 byte |
| `0x10` | 216,891 | implied 2 bytes |
| `0x20` | 222,954 | implied 4 bytes |
| `0x30` | 207,403 | implied 12 bytes |
| `0x40` | 737 | implied 16 bytes |
| `0x50` | 229,790 | one explicit size byte |
| `0x60` | 193 | a two-byte size |

Two consequences for a rebuild. **A bool's `Size` is zero**, not one: the value rides in bit 7 and the writer measures an empty value, so a bool lands in the explicit one-byte class (`0x50`) -- 33,923 bool tags read true and 46,304 false with no value byte. And **the explicit classes are only reached when no implied width matches**: 0 tag(s) write a width of 1/2/4/12/16 in an explicit class and 0 non-bool tag(s) set bit 7 with a zero array index, both of which the writer's rule forbids. 319,288 struct tag(s) carry a struct name.

| property type | tags | `Size` on disk, commonest first |
|---|---|---|
| Object | 346,615 | `1` (188,757), `2` (157,533), `4` (280), `16` (28) and 8 more |
| Struct | 319,288 | `12` (207,032), `13` (67,423), `25` (17,550), `14` (15,882) and 26 more |
| Int | 138,232 | `4` (137,561), `1` (607), `16` (23), `2` (14) and 9 more |
| Name | 124,764 | `1` (68,393), `2` (56,162), `4` (159), `16` (32) and 6 more |
| Float | 82,623 | `4` (82,122), `1` (430), `16` (39), `2` (16) and 8 more |
| Bool | 80,227 | `0` (79,105), `1` (881), `4` (119), `2` (71) and 8 more |
| Byte | 46,782 | `1` (46,467), `4` (263), `16` (28), `2` (10) and 6 more |
| Array | 38,636 | `3` (7,798), `5` (5,836), `7` (4,807), `9` (3,246) and 120 more |
| Invalid | 25,121 | `1` (24,858), `4` (174), `12` (41), `16` (33) and 4 more |
| Str | 7,745 | `1` (2,492), `11` (667), `8` (538), `29` (430) and 257 more |
| Class | 630 | `1` (437), `4` (149), `16` (21), `2` (10) and 4 more |
| Rotator | 547 | `1` (233), `4` (188), `2` (83), `16` (32) and 7 more |
| String | 532 | `1` (317), `4` (169), `16` (20), `12` (11) and 7 more |
| FixedArray | 427 | `1` (197), `4` (176), `16` (37), `2` (9) and 6 more |
| Map | 248 | `1` (103), `4` (91), `16` (22), `2` (18) and 6 more |
| Vector | 223 | `4` (101), `1` (64), `16` (30), `2` (21) and 2 more |

**The value's width is its serialized length, so a type has a modal width and a long tail.** Object `1` on 188,757 of 346,615 (54.5%); Struct `12` on 207,032 of 319,288 (64.8%); Int `4` on 137,561 of 138,232 (99.5%); Name `1` on 68,393 of 124,764 (54.8%); Float `4` on 82,122 of 82,623 (99.4%); Bool `0` on 79,105 of 80,227 (98.6%). `Struct` is the members' own bytes -- 30 widths, 207,032 of its 319,288 tags at `12` and 67,423 at `13` -- and `Str` grows with the string, its one-byte values being a length prefix and no characters.

**Most of the tail is ordinary**: a reference's second byte (`Object` is `1` on 54.5% and `2` on 45.4% of its tags), a struct's members, a string's characters. What is left is a residue carrying a width its own class cannot write, and every one of those headers still re-encodes: 607 `Int`(s) and 430 `Float`(s) at `1` where the class writes 4, 881 bool(s) at `1` where a bool writes nothing at all, and 217 of the 223 `Vector` tags -- whose class writes 12 -- sitting at `4` (101), `1` (64), `16` (30). A header is only self-consistent with itself, so a desynchronised parse survives the header test; the value's width is the only second opinion the shipped corpus offers, and this is where it disagrees.

The checkpoints are the control: the nine `.sgs` files hold 20,296 tags over 1,458 blocks in this walk, against the 19,080 `Sg1Tool save` records `docs/formats/sgs-checkpoint.md` tabulates. The walk reads 1,216 more, every header it counts re-encodes to the file's own bytes, and the older table is lower on all nine files, so the two readers disagree about how many tags the saves hold rather than about whether these tags are there. 218 block(s) end without a zero index: the payload ends past the last complete tag, which is the shape a save's last object takes.
<!-- /generated:tag-encodings -->

## 7. Not established

- **The value's internal layout.** The tag is settled in both directions and the value's *width* per type
  is measured, but not the bytes inside it: a `Str`'s length prefix, an `Array`'s count and element stride, a
  `Struct`'s member order and a bulk-data tag's chunk list are all "`Size` bytes, the class's own serializer"
  here. A rebuilt value needs them, and `0x00038c30`'s dispatch through `[property+0xa0]` is the body that
  writes them.
- **Which class owns `0x00078c20`.** Three classes are 0x74 bytes and two of them are accounted
  for; the constructors of `FixedArrayProperty` and `MapProperty` were not exported.
- **The rest of `UObject`/`UStruct`.** Only the fields a consumer was seen reading are listed;
  `Children` at `+0x3c` is read by `0x0003bdc0` off the struct itself and nothing here claims the
  full layout.

## 8. What a class default object's own bytes hold (measured 2026-11-07)

A class default is an ordinary `UObject` whose own `Class` is the class — not a pointer inside the class object, so
it is found by filtering `GObjObjects` (`tools/xemu/fields.py --instances`), and which of a class's objects is the
default has to be read off **contents**. `GameHud` is the worked example: two objects carry its class, the one whose
fields are package-asset pointers plus the declared `SkipMatineeTextString` (`+0x258`, `'Press A to Skip'`) is the
default, and the one holding a widget tree built during the load is the live HUD.

| what | measurement |
|---|---|
| the class object | `0x01886c00` on seven boots — the only address in this question that does not move |
| the default's extent | at least `+0x5500`; the first foreign object is a `Sound` at that offset |
| a live instance's extent | at most `+0x600`; a `ParticleMaterial` starts there and four more follow every `0x300` |
| pointer/array rows, first `0x600` | 37 on the default (all `StaticMesh`/`Texture`/`Shader` at `0x01xxxxxx`), 65 on the live HUD |
| a `Float` default (`1.0`) | **not** in the default's first `0x4000` as a run: exactly one `0x3f800000`, at `+0x180c` |

Two lessons a consumer inherits. **An object has no serialized size**, so a field window means nothing until
`--near` bounds it with the next object's address — and the two `GameHud` rows bound at `0x5500` and `0x600`, so a
window that is safe on the default reads four neighbours on the live HUD. And **a scalar default is not a layout**:
`1` and `1.0` are the commonest dwords an object holds, so a single hit proves nothing and a *run* of one value at
consecutive offsets is the weakest shape worth calling a measurement, which is why `--find` prints runs.

## 9. A `UProperty` is an object in `GObjObjects`, and `Offset` is a runtime field (measured 2026-11-08)

A class's declared fields are **not** reachable from its class object. `GameHud` (`0x01886c00`, identity re-read
this boot) holds, in its first `0x1400` bytes, `Super` at `+0x28` (the chain reads `GameHud` -> `Interaction` ->
`Interactions` -> `Object`, three links), its own meta-class `Class` at `+0x624`, a `Landed` override at `+0xd74`,
and a `Function` (`PlayHUDSound`) at `+0x3c` — the offset the native reader (`0x0003bdc0`) calls `Children`, whose
`Next` chain stops after that one link. Every other dword in the window is a scalar, a zero, or a class pointer:
`tools/xemu/fields.py --object 0x01886c00 --bytes 0x28,0x48` prints the window, and a scan that walks each dword as
a candidate `Children` head and follows `+0x28` as `Next` finds **no** chain of field objects at all (the longest
are the `Super` chain and the `Function` tails). Two corrections follow from that one read:

- **`+0x28` on a class object is `Super`, not `UField::Next`.** The measurement is the chain itself: three links
  ending at `Object`. `NEXT_OFF` stays right for a `UProperty` (it is what `find_field_chain` follows), but a
  class's parent is at the same offset, so a scan that treats `+0x28` as `Next` reports the parent class as a
  "field chain of 3".
- **`--prop-name`-style naming off the class object cannot work here.** `UITextBox`'s class object gave the same
  shape earlier (one link, `EatStr`, a `Function`), and the scan accepted it only because "every link ends in
  `Property`" holds vacuously for a one-link chain.

What *is* true is that a `UProperty` is an ordinary object: it has a name, a `Class` whose name ends in `Property`,
and an `Outer` that names the class declaring it. So the object array is the index, and `fields.py --prop-index`
reads it:

    # which classes declare `DisplayText`, and does any of them sit on UITextBox's `Super` chain?
    python3 tools/xemu/fields.py --port PORT --at 0x006be0d8 --object 0x00bcea00 \
        --prop-name DisplayText --prop-at <calibrated Offset position> --prop-in <live window>

`--prop-up` walks `Super` (at `+0x28`) from `--object`'s class, so an inherited field resolves for a subclass's
instance. The first hit measured on this route is `DisplayText` at index 1647 (`StrProperty`, `Outer`
`SGInteraction`) — a different class than the `UIWidget` field U15 wants, which is exactly why the `Outer` /
ancestor test is part of the tool rather than a name match.

**`Offset` is not serialized.** The package carries the property's name, kind and flags; the linker writes the
offset into the live object. The dword position is calibrated against a value this project already read off an
object's bytes: `GameHud`'s class default holds the declared `SkipMatineeTextString` `FString` at `+0x258`, so the
position inside that `UProperty` object whose dword is `0x258` is `Offset` for every property. `--prop-at` takes
that position and `--prop-in` the instance to read.

**The field list is the objects outered to the class, and that one is cheap (measured 2026-11-08).** A class
object links no fields, but a `UProperty` is an ordinary object whose `Outer` names the class that declares it, so
`fields.py --object <class object> --children` reads the declaration list out of `GObjObjects`: `GameHud` has
**158** declared objects, including the three `FloatProperty` scales (`DialogueFontScale` 26,915,
`NotificationFontScale` 26,918, `InteractionFontScale` 26,920) and `SkipMatineeTextString` (26,928, a
`StrProperty`), and the live row and the class default both hold `'Press A to Skip'` at `+0x258` with different
`Data` pointers, so `InitProperties` copies the string into each instance.

The same question asked *by name* is a trap worth naming: `Reader.object()` resolves the class name with an
uncached `u32(cls + 0x20)` before it reads the object's own name, so a name filter over 48,646 objects is 48,646
stub round trips -- one such walk had not finished after five minutes and was killed, while the `Outer` test (a
page-cached `try_u32` for every non-match) answered in about 80 s on the same stub. Ask a cached dword first.

**`Offset` was uncalibrated when this section was written; §10 has the calibration.**

## 10. `UProperty::Offset` is `+0x48`, and the HUD's windows are named by property (measured 2026-11-08)

One loaded-`M00` boot (`prp3`, gdb `12,573`; `GObjObjects` `0x041e0000`, 48,646 objects, `GNames` 41,265) reads the
position and then uses it. The calibration is two *independent* value sets sitting in one dword position, which is
what makes it a measurement rather than a coincidence:

| property | address | dword at `+0x48` | the object offset that value is already known to be |
|---|---|---|---|
| `SkipMatineeTextString` (`StrProperty`) | `0x019c5400` | `0x258` | the `FString` `'Press A to Skip'`, on both `GameHud` rows |
| `DialogueFontScale` (`FloatProperty`) | `0x019c59b0` | `0x168` | the `0.85` config value on the class default |
| `NotificationFontScale` | `0x019c5860` | `0x19c` | the `0.8` config value |
| `InteractionFontScale` | `0x019c5780` | `0x1d0` | the second `0.85` |
| `DialogueWindow` / `InteractionWindow` / `NotificationWindow` | `0x019c9d10` / `0x019c9ca0` / `0x019c9c30` | `0x50` / `0x58` / `0x54` | the three window slots |
| `UIWidget.DisplayText` (`StrProperty`) | `0x019c2450` | `0x394` | `DisplayText`'s slot on a `UITextBox` |

`tools/xemu/fields.py` therefore defaults `--prop-at` to `OFFSET_AT = 0x48`: `--prop-name`/`--prop-value` name a
property's value slot with no calibration read, and the selftest uses the default so the position cannot drift
silently.

**The declaration order is not the offset order.** `GameHud`'s 158 declared objects list `DialogueWindow` (index
26,717), `InteractionWindow` (26,718) and `NotificationWindow` (26,719) in that order, but the linker's `Offset`s are
`0x50`, `0x58` and `0x54`. Reading a window by its position in the list would have put `InteractionWindow` where
`NotificationWindow` is; reading each property's own `Offset` names it. On the live row `0x09362100` all three slots
resolve to `UITextBox` objects — `0x097c7300`, `0x097c6900` and `0x097c6e00` — whose class chain is `UITextBox`
(`0x0197a800`) -> `UIWidget` (`0x0197b400`) -> `Actor` -> `Object`. The class default `0x0682ab00` holds `0` in all
three slots, because `GameHud.uc:493`, `:511` and `:522` spawn the windows after the class default is built, so a
class-default read can never answer a question about them.

**`DisplayText` holds real text, and the frame draws the same string.** `UIWidget` declares 65 objects and
`DisplayText` is `0x019c2450` at `Offset 0x394`; read on the live windows of that boot:

| window | `DisplayText` | Count |
|---|---|---|
| `DialogueWindow` `0x097c7300` | `'General, would you mind getting the door for me sir?'` | 53 |
| `NotificationWindow` `0x097c6e00` | `'New Objective : Open the door for the doctor'` | 45 |
| `InteractionWindow` `0x097c6900` | `'default'` | 8 |

The notification is **not** the 27-character `NO NOTIFICATION TEXT SET!!!!` placeholder that `GameHud.uc:1034`
substitutes for an empty argument, so `SetNotifications` was given real text; and the same boot's frame
(`$WORK/xemu/prp3/02-mission.png`) draws that identical line at the bottom of the instruction panel. The text
reaches the field and the field reaches the screen: the console's "draws nothing" reading does not extend to the
notification path (U15).

## 7. The value serialiser is slot `+0xa0`, and a delegate is `{object, FName}` (U18)

`inventory/property_serialisers.csv` (`tools/property-serialisers.py`, `make property-serialisers`) censuses the 14
property classes this build constructs -- `Property` itself has no constructor row -- at three offsets of their own
tables. Every table is **53 slots**, split at its own install store, and every one of the three offsets has its own
target in every class:

| offset | what it is | per-class targets | what the bodies do |
|---|---|---:|---|
| `+0x2c` | `Serialize`, the tag writer | 14 of 14 | 12 dispatch an archive virtual, 1 calls one helper, 1 other |
| `+0x94` | a two-argument **comparison** | 14 of 14 | 11 call nothing (`cmp`/`sete` and `ret 8`), 2 dispatch a virtual, 1 calls a helper |
| `+0xa0` | `SerializeItem`, the value | 14 of 14 | 6 call one archive helper, 4 dispatch an archive virtual, 2 reach `+0xa0` on their `Inner` |

**A property class proves `+0xa0` from inside another one.** `FixedArrayProperty`'s body (`0x00078a20`) loops its
elements and ends `call DWORD PTR [edx+0xa0]` on the property in its own `+0x6c` (`Inner`), pushing the archive, the
element address and the count (`0x00078a56`). A per-class body that recurses into a *different* class's reader at the
same offset is a value reader, not a tag writer -- which is what `+0x94` is: the walk at `0x0003bdc0` calls it once,
at `0x0003c340`, with two addresses, and branches on the `bool` it returns.

**Delegates.** `DelegateProperty`'s `+0xa0` (`0x00077f00`, 30 bytes) is `Ar->[+0x18](Value)` and then
`[that]->[+0x1c](Value+4)`. `ObjectProperty`'s whole serialiser is that first call (`0x00078660`, 17 bytes) and
`NameProperty`'s is the second (`0x00078840`, 17 bytes), so a `delegate` value is an **object reference at `+0` and
an `FName` at `+4`** with no third field: the writer's answer to U18, and the field order the payload readings tiled
as two compact indices (39 of 42). `StructProperty`'s body dispatches `+0x98` instead, which is `UStruct`'s property
walk at `0x0003bdc0`.
