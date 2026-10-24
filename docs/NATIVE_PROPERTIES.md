# The property system in `default.xbe`

Status: measured from the build (PLAN 4.3, property half). What is established here is the
**type space**: the reserved name indices the property type names occupy, the fifteen property
classes and their sizes, the field offsets a consumer is seen reading, and the shape of the
routine that walks a struct's properties. The tagged tag record (`FPropertyTag`) is measured too (§6), from the build's own reader at
`0x0003ceb0` and confirmed against the independent parser that reads this build's checkpoints.

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
| vtable `+0x94` | per-element serialization: `(**(code **)(*child + 0x94))(value, …)` | `0x0003bdc0` |
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
the fork's own special case.

Not measured here: the writer side. The reader is what a rebuilt save must satisfy, and the
`Ar.IsSaving()` half of the same routine exists, but no writer body was decompiled, so this document
claims nothing about how the build *writes* a tag beyond "the same routine, the other branch".

## 7. Not established

- **The writer side of the tag.** §6 measures the reader; the saving branch of the same routine was
  not decompiled, so nothing is claimed about the order the build writes a tag in (PLAN 6.2/6.4).
- **Which class owns `0x00078c20`.** Three classes are 0x74 bytes and two of them are accounted
  for; the constructors of `FixedArrayProperty` and `MapProperty` were not exported.
- **The rest of `UObject`/`UStruct`.** Only the fields a consumer was seen reading are listed;
  `Children` at `+0x3c` is read by `0x0003bdc0` off the struct itself and nothing here claims the
  full layout.
