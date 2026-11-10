# The package check file, measured

`System/Packages.md5` is the game's own integrity list, and PLAN 1.4 asks which shipped packages
satisfy it. None of them do, and this page now says exactly what the file holds and everything that
has been ruled out — because the first version of it was reading the file wrong in a way that
guaranteed a mismatch.

    python3 tools/package-check.py DUMP/.../System -o inventory
    python3 tools/package-check-pairs.py DUMP/.../Stargate_SG-1_-_The_Alliance-2005-12-15 -o inventory
    python3 tools/package-check-quick.py DUMP/.../Stargate_SG-1_-_The_Alliance-2005-12-15 -o inventory
    python3 tools/package-check-quick.py BUILD/System --records DUMP/.../System/Packages.md5

## What the file is

An **Unreal Engine 2 package**, not text:

| field | value |
|---|---|
| tag | `0x9E2A83C1` (UE2 package) |
| version / licensee | 130 / 4 |
| package flags | `0x00000001` |
| name table | 175 entries at `0x44` |
| exports | 168 at `0x43D3` |
| imports | 2 at `0x43C5` (`Core`, `Engine`) |
| size | 20,111 bytes |

Its 168 exports are `PackageCheckInfo0` … `PackageCheckInfo167`. Each one is 78 bytes and has a
**fixed layout with no filename field**:

```
00 21 <32 uppercase hex> 00 01 21 <32 lowercase hex> 00  00×7
```

So there are two 32-hex values per record. The name table's `PackageCheckInfoN` names are the record
index, and 168 records for 18 shipped `.u` files means the list covers the whole game's package set,
not the scripts in `System/`.

## The first field is the package's own GUID, so the records do pair with files

This page used to say that pairing a record with a filename was impossible because the record has no
name field. It is not: `0x001219d0`, the full-MD5 pass, prints a package's first value as
`GUID = [%08X%08X%08X%08X]` after reading the header at `+0x24`, and **every record's uppercase field
is exactly that text for the file that carries it**. `tools/package-check-pairs.py` measures it:

| measurement | value |
|---|---|
| records / distinct GUIDs / distinct digests | 168 / 168 / 168 |
| package files in the dump with a UE2 header | 499 (467 distinct header GUIDs) |
| records whose GUID a shipped package carries | **168 of 168** |
| records whose digest is `md5(file)` of that package | **0** |

So the pairing is complete and one-to-many only where the dump holds copies: 8 header GUIDs are
shared by several files (the largest covers 17, e.g. `Animations/sg1_anim2.ukx`), which are the
duplicated/copy packages (`M05ATex.utx`, `M16Tex.utx`, `xx_old_m17tex.utx`, …) whose headers were
never regenerated. The record order is therefore not an assumption: it is the GUID order the cooker
wrote, and each record names a package the dump still ships.

The digest column is a different value: it is **not** `md5` of the file it is filed against. That is
not a mystery any more either — see the transform below.

## What has actually been ruled out

| search | scope | result |
|---|---|---|
| plain `md5` of the file as shipped | **all 4,003 files in the dump** | 0 matches |
| 89 range/zeroing variants of `md5` | `Engine.u`: starts at 0/4/8/12/16/32/36/52/64/`hdr`/`names`/`imports`/`exports`, end trims of 4–64 bytes, GUID / version / tag / header-size zeroed | 0 matches |
| 8 name- and data-shaped variants | **489 package files** (`.u`, `.unr`, `.uax`, `.utx`, `.usx`, `.ukx`): `md5(name)`, lower, upper, data+name, name+data, trailing-NUL strip, data+own-md5, `data[0x44:]` | 0 matches |
| `md5(file)` for every file whose GUID a record carries | **the 168 paired files** | 0 matches |
| 1,360 framings of the header and name-table bytes | 18 `System/*.u`, slices `0x00`/`0x04`/`0x08`/`0x0C` … `0x20`–`0x44`, with and without the raw name table | 0 matches |

The digest is not a hash of any shipped file under any framing tried, so the earlier conclusion —
"regenerating the check is out of reach" — was about the whole file. The code says it never hashed
the whole file.

## The code: one console command, ten switches

The strings are still in the image, and the dispatcher is `0x00126860` (`MD5 Database Management`,
`0x0050700c`). It takes one wide argument and `__wcsicmp`s it against ten inline literals, each of
which calls one routine:

| switch | literal VA | routine | prints / does |
|---|---|---|---|
| `-a` | `0x004eb8d4` | `0x00125a90(0)` | adds every package it finds to the existing database |
| `-c` | `0x00507004` | `0x00125a90(1)` | same, after `FUN_0006db70` creates a new database |
| `-s` | `0x00506ffc` | `0x00121490` | lists the database: `GUID: %s [rl=%i]`, `id %i: %s` |
| `-r` | `0x00506ff4` | `0x00121730` | sets a package's revision level, then re-saves the file |
| `-f` | `0x00506fec` | `0x001219d0` | `Performing full MD5 on %s` → `MD5 = [%s]` |
| `-q` | `0x00506fe4` | `0x00122430` | `Performing Quick MD5 on %s` → `MD5 = [%s]` |
| `-g` | `0x00506fcc` | `0x001220e0` | prints one package's `GUID = [%s]` |
| `-l` | `0x00506fc4` | `0x00121030` | `%s links to code` / `%s does not link to code` |
| `-j` | `0x00506fdc` | `0x001260b0` | `Dumping MD5 for Master Server` (server upload) |
| `-w` | `0x00506fd4` | `0x001264b0` | the same dump in web format |

`0x0050700c`'s own banner is `MD5 Database Management`; the file is read through
`FUN_00073550(…, L"Packages.md5", …)` and written by the engine's `SavePackage` (`0x0006df00`),
which is why the database is itself a package with `PackageCheckInfo` exports rather than a text
list. `0x00187350` is the `Engine_UnPackageCheckInfo` class factory, and the loader's own message,
`[INVALID PACKAGE] MD5    : %s`, lives in `Engine_UnLevel` (`0x00161392`).

## What the transform is

The two passes are different algorithms, and the database is written with the **quick** one
(`0x00125a90` builds each record through `0x00121cf0`, the same routine `-q` prints):

* **Full** (`0x001219d0`) reads the file in `0xFFFF`-byte chunks into one buffer and hashes it —
  `md5(file)`, nothing else. That is what the 168 digests are *not*.
* **Quick** (`0x00121cf0`) opens the package, reads only its header with `0x00051720`, appends
  thirteen header dwords (flags, the three count/offset pairs, the GUID, the generation block),
  then one 8-byte record per name-table entry, and hashes that buffer with `FUN_00058b40` — an MD5
  final over a memory buffer (`0x00058a80` appends; `0x00058df0`/`0x00058e10` append a dword,
  `0x00058e30` a word). The digest is then printed as 16 × `%02x`, lowercase; the GUID as
  4 × `%08X`, uppercase — which is exactly the case convention the records use.

So the check is a **header-and-name digest, not a content digest**: a package rebuilt by copying its
payloads keeps its header GUID (the writer copies it) and its name table, which is why all 168
records still pair with the dump's files even though not one digest matches `md5(file)`.

### The buffer, measured

`tools/package-check-quick.py` rebuilds that buffer and reproduces the record's second field for
**168 of 168** packages (`make package-check-quick`, `inventory/package_check_quick.csv`):

| bytes | content |
|---|---|
| 52 | the header dwords at `0x00`-`0x33` as stored: tag, version\|licensee, package flags, name count and offset, export count and offset, import count and offset, and the four GUID dwords |
| 8 per generation | the generation records at `0x38` (export count, name count); the generation **count** is not hashed |
| 8 per name entry | the entry's **flags dword**, then the **first two UTF-16 units** of its name |

Read out of the code, not from the bytes: `0x00062110` (the archive's `FNameEntry` reader) puts the
flags dword at `+0x04` and the wide name at `+0x0C` of its local, and `0x00121cf0` appends
`[entry+0x04]` through `0x00058e10` (four bytes) and the words at `+0x0C` and `+0x0E` through
`0x00058e30` (two bytes each), onto the MD5 context `0x000564b0` which `0x00058b40` finalizes.
`--walk` prints the eight bytes per entry, so the framing is checkable by eye: `Core.u`'s `None`
entry is `100407044e006f00` — flags `0x04070410`, then `N`, `o`.

Two consequences are worth stating plainly, because they decide what the check can be used for:

* **The digest is deliberately coarse.** Only the first two characters of each name are covered, so
  `Actor` and `Actress` hash the same. A record that reproduces therefore does not mean the file is
  intact — that is what the full pass (`-f`, `0x001219d0`) is for.
* **A relayout still changes it.** The three count/offset pairs are inside the buffer, so a rebuild
  that moves an export changes the digest even when no payload byte differs. That is exactly the case
  a regenerator has to serve, and it can: the record is a function of the header and the name table
  alone, so a rebuilt package's record is computable from the file rather than from the original.

## The check runs on a build tree

`--records` points the same join at another check-data file, which is what makes the stage usable on
a build: `tools/build.sh --target hybrid` places the writer's **18 assembled packages** into
`workdir/System`, copies `Packages.md5` beside them, and then runs

    python3 tools/package-check-quick.py workdir/System --records SRC/System/Packages.md5

against the source's records. The stage passes on the shipped tree — **17 of 17** records whose
package the build places reproduce — and fails when a placed package no longer matches, which is the
case a rebuild that moves a name table or an export offset creates. One number is worth keeping:
`System/` holds 18 `.u` files but only **17** of them have a record; `UPreview.u` (734,635 bytes,
GUID `0E8020CB4558E0968B4182926B00E849`) is absent from the check data the cook wrote, so a stage that
counted files instead of records would look one short for no reason.

## What that leaves for PLAN 6.2

Regeneration needs nothing from outside the dump any more: a package's GUID and its digest both
come out of the file the writer produced, and `build.sh --target hybrid` already runs the stage over
the tree it places (above). What is left is the writer half — writing a `Packages.md5` package of our
own through the container writer (`docs/PACKAGE_WRITER.md`) rather than copying the original. Until
that exists, a build that changes a name table or a layout has to be added to the database with `-c`
against a patched build, or run with the check bypassed; a build that keeps both header and names
needs nothing at all.

## Where the check lives in the image

    python3 tools/image-strings.py --topic package-check --xbe DUMP/.../default.xbe -o inventory

| function | bytes | evidence | callers |
|---|---:|---|---|
| `0x00187350` `Engine_UnPackageCheckInfo` | 176 | `PackageCheckInfo` / `CheckInfo` (and the assert path `PackageCheckInfo.cpp`) | `0x00012620`, `0x00125310`, `0x00127590`, `0x00132b10`, `0x00132e70`, `0x00147b40` |
| `0x00121490` | 670 | `Packages.md5` | `0x00126860` |
| `0x00121730` | 479 | `Packages.md5` | `0x00126860` |
| `0x00125a90` | 1,542 | `Packages.md5` | `0x00126860` |
| `0x00132300` | 80 | `Packages.md5` | — |
| `0x00139030` `Engine_UnGame` | 3,348 | `Packages.md5` | — |

Six functions carry the check's vocabulary, and the two families are connected: `Engine_UnPackageCheckInfo`
reaches the reader at `0x00125a90` at depth two, and `Engine_UnGame` appears at depth two above it.
So the check is not dead code — a class exists for the records, and the game class is two calls away
from the code that opens the file.

Artifact: `inventory/package_check_code.csv` (`make package-check-code`, `--check` in `make ci`).

## Not measured

* **Which** path invokes the check at runtime: the code is present and reachable from `Engine_UnGame`,
  but whether it runs on every load, on a menu, or only on a console command needs the probe in U5 /
  7.3 territory. The `MD5` console command is the writer, not necessarily the verifier.
