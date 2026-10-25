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

What it does **not** yet answer is *which object owns which name*: that is the export table, and until the
compact-index entries are decoded and cross-checked, asking this reader for a class's function list would be
inventing structure. That is the next slice, and it is the one that unblocks 4.7's native function records.


## The export table: two encodings tried, neither accepted

The next slice is the export table, and this is where the reader currently stops. The table's position and
size are known — `export_offset` and `export_count` from the header, and both are inside the file for all 18
packages — but the entry encoding is not, and guessing it produced nonsense rather than a result.

Two index encodings were tried against `Core.u` (472 names, 906 exports at `0xFCCD`, 18 imports at
`0xFC2C`), plus a field layout that assumed four compacts followed by flags and two more compacts. The test
each one has to pass is structural, not aesthetic: **parsing the import table must end exactly where the
export table begins.** Neither index encoding does, and the field layout produced 906 entries of nonsense.

| encoding | imports end | should be |
|---|---|---|
| 1-byte, else 4-byte, sign in bit `0x40` (UE1 `FCompactIndex`) | `0xFC95` | `0xFCCD` |
| 1-byte, else 2-byte, sign in the high bit | `0xFC86` | `0xFCCD` |

The raw bytes say why the simple forms fail. At `0xFC2C`:

```
04 4f 07 00 00 00 00 04 | 04 4d 07 ff ff ff ff 45 | 05 04 4d 07 ff ff ff ff
```

There is a four-byte field per entry that is `0` in the first and `-1` in the rest — an outer/parent index,
not a compact one — and the leading pairs (`04 4f`, `04 4d`) read like package/class *name* indices, which
would make each entry a mix of compact and fixed-width fields. A wrong guess here does not fail loudly: it
produced a plausible-looking 906 entries whose class histogram was nonsense (`Class` 248 in a package with 7
classes). That is the failure mode worth remembering — **entry counts matching proves the table's extent,
not the field layout.**

`python3 tools/ue2pkg.py PKG --tables` prints those regions verbatim so the next attempt compares against
bytes instead of re-deriving the same dead ends.

### What a correct decoder has to reproduce

Four independent facts are available to check against, which is why this is worth finishing rather than
abandoning:

1. `inventory/classes.csv` counts classes per package (`StargateGame` 306, `Engine` 367, `Core` 7).
2. `inventory/xbe_class_table_names.csv` names 466 of 492 runtime class tables.
3. `inventory/class_properties.csv` holds 11,496 class/property pairs.
4. The export table must end exactly where the next table starts, in all 18 packages.

Format note for whoever picks this up: `UPreview.u` is version **128** while the other 17 are 130, so a
decoder that works on the majority may still need a branch for it.

## Not measured

* The export and import tables (offsets and counts are parsed; the entry encoding above is unresolved).
* Function-level data of any kind — no bytecode, no `UFunction` records.
* Whether `UPreview`'s version 128 changes the name-table encoding (it does not appear to; its 164 names
  parse).
