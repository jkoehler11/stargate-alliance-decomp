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
  0x45"). The length is also an **index**, not a single byte — see the section below, which is the second
  time this one field produced a reader that looked right on 18 packages.
* Flags seen in `Core.u`: `0x00070010`, `0x00070004`, `0x00070000`. The low bits differ per name — the
  pattern is consistent with the engine's name-flags word, but nothing here establishes the bit meanings,
  and this file does not claim any.
* The first names are `None`, `!=`, `==`, `System`, `Core`, `-`, `*`, `-=`, `*=`, `/` — the operator and
  punctuation vocabulary comes first, which is why a name-index offset assumption that starts at `0x44`
  looks wrong at a glance but is right.

## The name length is an index, not a byte

The length in front of each name is decoded with the same `ReadIndex` as the tables (bit 6 = "another byte
follows"), not as one byte. Below `0x40` the two readings are identical, which is why a byte rule parsed
**every name in all 18 `System/*.u` packages** — 37,094 identifiers, no name there reaching 63 characters —
and was still wrong. It showed up two packages into a 490-package sweep:

| package | declared names | byte rule | index rule |
|---|---:|---|---|
| `Maps/M17D.unr` | 3,735 | 146 then `non-printable name at 0x9d6` | **3,735**, table ends `0x13b2c`, payloads at `0x13b31` |
| `Maps/M21A.unr` | 4,019 | 395 then `non-printable name at 0x1dbe` | **4,019**, table ends `0x153f7`, payloads at `0x153f8` |
| `Sounds/M01A_sounds.uax` | 29 | 0, `non-printable name at 0x41` | 0, same refusal (a different encoding: see below) |

The name that breaks the byte rule is a 63-character one — both maps carry a comma-joined list as a single
name (`None,_18WH,_18C,_19,_copy,floor18_WH,floor18_WH01,floor18_WH01i`). Its length byte is `0x40`, and the
byte after it is `0x01`: `0x40` is the index codec's continuation flag, so the length is `0 | 1 << 6` = 64
and two bytes are consumed. A byte rule consumes one, the name then starts one byte late, and every entry
after it is shifted until a zero byte appears where a length should be — which is exactly the `length 0`
this project kept seeing at `0xa1a` and `0x1e02` (the offsets belong to the *flags dword* of the entry one
byte earlier, which is what a one-byte shift looks like).

`Sounds/M01A_sounds.uax` is **version 127, licensee 34** and writes its name lengths as a plain `uint32`
(`05 00 00 00 "None\0"`), so it is a different engine revision rather than another index case: 1 of the 490
packages, and the only one. Its name table stays unread; nothing else in the dump is at 127/34.

### What the fix changed in the artifacts

Nothing in the script tree — every `System/*.u` name table is byte-identical, so `package_names_summary`,
the class tables and everything derived from the 18 packages are unchanged (checked by `make ci`). The two
maps did change, and in a way that closes a recorded mystery:

* `inventory/map_export_classes.csv`: **6,478 → 6,658 rows**, and the two `?` rows are gone. **2,528 actors
  that had no class name now have one** — `M17D` 988 (`StaticMeshActor`, `Mover`, `Trigger`, `PlayerStart`,
  `SGC_SecurityDoor`, …) and `M21A` 1,540. The actor total is unchanged at 194,190, so nothing was gained by
  losing rows, only names resolved.
* `inventory/map_property_coverage.csv`: property blocks located in `M17D` 1,840 → **1,954** and `M21A`
  1,905 → **1,960** (a class name is what lets the locator find the block's start).
* `inventory/actor_property_coverage.csv`: `ReachSpec` **29,665 → 30,539** resolved values, `StaticMeshActor`
  32,191 → 32,193.

It also answers a line that had been sitting in PLAN 5.2 as "recorded, not explained": `M21A`'s **1,540
exports whose class index does not resolve** were exports whose class name lived in the 3,624 name-table
entries the byte rule never reached.

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
* `Sounds/M01A_sounds.uax`'s name table (version 127 licensee 34, `uint32` lengths). One package of 490; the
  rest of its tables are equally unread, and nothing else in the dump is at that version, so there is no
  corpus to infer the rest of the 127/34 layout from.
