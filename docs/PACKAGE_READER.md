# Reading the packages: the first slice

Four open tasks in this repository come down to one missing capability — a reader for the game's Unreal
Engine 2 packages. Maps (5.2), the ucpp function records (4.7), the sound effects inside `.uax` (5.3) and the
digests in `Packages.md5` (1.4) all wait on it, and UELib needs a .NET runtime this environment does not
have.

`tools/ue2pkg.py` is the first slice: the header and the **name table**, which parse cleanly and can be
checked. The export table is deliberately not guessed — the tool reports what it parsed and refuses the rest.

    python3 tools/ue2pkg.py DUMP/System              # every package: counts + status
    python3 tools/ue2pkg.py DUMP/System/Core.u --grep Order

## What parses, and how it was checked

For all 18 shipped packages the name count parsed **equals the count in the header** — 18 of 18, no
partials. That is the check that matters: the reader either reproduces the package's own declared count or
says why not.

| package | version | names | exports | imports |
|---|---:|---:|---:|---:|
| `Engine` | 130 | 10,406 | 17,980 | 120 |
| `StargateGame` | 130 | 8,029 | 13,425 | 2,014 |
| `StargateContent` | 130 | 6,970 | 7,732 | 2,796 |
| `GUI` | 130 | 2,116 | 3,682 | 213 |
| `UWindow` | 130 | 1,388 | 3,446 | 97 |
| `GamePlay` | 130 | 1,201 | 1,910 | 270 |
| `StargateVehicles` | 130 | 1,848 | 2,544 | 594 |
| `StargateMenus` | 130 | 1,016 | 1,809 | 269 |
| `Sharpeffects` | 130 | 856 | 1,015 | 234 |
| `UDebugMenu` | 130 | 652 | 1,194 | 309 |
| `Stargateeffects` | 130 | 585 | 481 | 190 |
| `Core` | 130 | 472 | 906 | 18 |
| `IpDrv` | 130 | 445 | 534 | 80 |
| `AmbientCreatures` | 130 | 313 | 367 | 118 |
| `Editor` | 130 | 299 | 384 | 42 |
| `Fire` | 130 | 173 | 110 | 15 |
| `UnrealEd` | 130 | 161 | 143 | 32 |
| `UPreview` | **128** | 164 | 87 | 89 |
| **total** | | **37,094** | **57,749** | **7,500** |

`inventory/package_names_summary.csv` holds that table. The full name list (37,094 identifiers) is
regenerated on the dump side and not committed — it is a deterministic function of the game files, and the
repository's rule is that game-derived bulk stays out.

`UPreview` is version **128** while every other package is 130. It is also the one package UELib could not
name a build for (`inventory/packages.csv`), so the two independent signals agree: it is the odd one out.

## Format notes worth keeping

* Name entries are **length-prefixed strings whose length includes the terminating NUL**, followed by four
  flag bytes. Reading the length as "characters" silently swallows the next entry's first byte, which is
  exactly how this reader failed on its first run (18 packages, zero names parsed, "non-printable name at
  0x45").
* Flags seen in `Core.u`: `0x00070010`, `0x00070004`, `0x00070000`. The low bits differ per name — the
  pattern is consistent with the engine's name-flags word, but nothing here establishes the bit meanings,
  and this file does not claim any.
* The first names are `None`, `!=`, `==`, `System`, `Core`, `-`, `*`, `-=`, `*=`, `/` — the operator and
  punctuation vocabulary comes first, which is why a name-index offset assumption that starts at `0x44`
  looks wrong at a glance but is right.

## What it unlocks

The name table answers a question the class graph could not: **what identifiers exist in a package**. For
example `StargateGame` contains `SGOrderAttack` … `SGOrderUse`, plus `NumOrders`, `SGPlayerOrders` and
`ConcentrateShootOrder_General` — evidence of the order system beyond the class names.

It now also says *which object owns which name*, because the export table is decoded: every export carries a
class, a super, an outer, a name and a byte range in the file. Asking for a class's functions is a filter on
that table rather than a guess.


## The export and import tables: solved, and how

Both tables now parse, and the export one is validated against an independent artifact: the number of
`Class` exports per package equals the class count UELib produced, **18 packages, 2,319 classes, exact per
package**.

```
package              ver    parsed   classes.csv
AmbientCreatures     130        18      18  OK
Core                 130         7       7  OK
Engine               130       367     367  OK
StargateGame         130       306     306  OK
UPreview             128         4       4  OK
...                                        18/18 OK, 2,319 total
```

### The layouts

```
ImportEntry  ClassPackage(index)  ClassName(index)  Outer(int32)     ObjectName(index)
ExportEntry  Class(index)         Super(index)      Outer(int32)     ObjectName(index)
                                                    Flags(int32)     SerialSize(index)  SerialOffset(index)
```

`inventory/package_exports_summary.csv` holds the per-package counts.

### Two details that cost six failed attempts

**The index encoding is not the one UE1 documentation leads you to.** It is: bit 7 is the sign, bit 6 means
"more bytes follow", and the value is 6 bits in the first byte then 7 bits per continuation byte —
`ReadIndex` in `dpjudas/surrealengine`'s `Docs/PackageFormat.md`. Every earlier attempt used a 1-byte-or-4
scheme and mis-signed negatives.

**`Outer` and `Flags` are plain 32-bit integers, not index-encoded.** That is the field that made the parse
stay *plausible* while being wrong: entry counts matched, and the values were nonsense, so the only thing
that could catch it was a semantic check. The check that settled it was the class histogram — a class
package should be mostly `Function`, `ObjectProperty`, `FloatProperty`, `StructProperty`, and
`StargateGame`'s histogram is exactly that (3,273 / 2,684 / 1,712 / 1,278) — not the byte count.

### What the boundary check was good for

The import table has to end exactly where the export table begins (`0xFCCD` in `Core.u`). That test is what
proved the import layout correct before the export layout was even close, and it is why `--tables` exists:
raw bytes, no interpretation. Two of the rejected candidates are recorded in this file's git history rather
than here, because they are not worth remembering — the *method* is: exact boundary, then semantics.

## Not measured

* Object *contents*: the tables say where each export's data begins and how long it is, but nothing reads a
  property stream or a function's bytecode yet. That is the next slice, and it is what 4.7 needs.
* Function-level data of any kind — no bytecode, no `UFunction` records.
* Whether `UPreview`'s version 128 changes the name-table encoding (it does not appear to; its 164 names
  parse).
