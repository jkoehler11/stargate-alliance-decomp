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

## The code: one commandlet, ten switches

`0x00126860` carries `MD5 Database Management` (`0x0050700c`), and it is **not** a console command: it
is slot 30 of `MasterMD5Commandlet`'s class table (the invoker section below has the read). It takes
one wide argument and `__wcsicmp`s it against ten inline literals, each of which calls one routine:

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
`workdir/System`, **writes a database of its own** there (`write-check-data`, below), and then runs

    python3 tools/package-check-quick.py workdir/System --records SRC/System/Packages.md5

against the *source's* records — the two halves stay independent, so the stage proves the placed tree
still matches what the cook recorded rather than agreeing with a file this build just wrote. The stage
passes on the shipped tree — **17 of 17** records whose package the build places reproduce — and fails
when a placed package no longer matches, which is the case a rebuild that moves a name table or an
export offset creates. One number is worth keeping: `System/` holds 18 `.u` files but only **17** of
them have a record; `UPreview.u` (734,635 bytes, GUID `0E8020CB4558E0968B4182926B00E849`) is absent
from the check data the cook wrote, so a stage that counted files instead of records would look one
short for no reason.

## Writing the database, not just reading it

`tools/package-check-emit.py` is the writer half, and it goes through the container writer's own
encoders (`tools/pkg-write.py`) rather than a second implementation of the format:

    python3 tools/package-check-emit.py --dump DUMP          # reproduce the shipped file from its records
    python3 tools/package-check-emit.py --tree BUILD/System --out BUILD/System/Packages.md5 \
        --records SRC/System/Packages.md5                    # record a build tree, read it back
    python3 tools/package-check-emit.py --check              # synthetic self-test, no dump (in `make ci`)

The package is fully parameterised by the records: seven names in front of the record names
(`None`, `Core`, `Engine`, `Package`, `Class`, `PackageCheckInfo`, `md5` — the last two are the export
class and the field's own name, both in the shipped table), two imports (`Core.Class`,
`Engine.PackageCheckInfo`, which is what makes every export's `class = -1` resolve), one
`PackageCheckInfoN` export per record with flags `0x00070004`, and the same
`names < payloads < imports < exports` region order `--layout` measures on every shipped package.
Each payload is the same 78-byte frame, measured from all 168 shipped records rather than derived:

    00 | 21 <32 uppercase hex> 00 | 01 | 21 <32 lowercase hex> 00 | 01 | 00 x7

`21` is the compact `FString` length (32 characters plus the NUL), so the two fields are the package's
GUID and its quick digest; the single `00`/`01` bytes around them and the trailing `01 00 00 00 00 00
00` are constant in all 168, and their meaning is **not** measured — which is why the frame is written
verbatim. (The name table also carries a bytes-vs-text trap worth knowing: the GUID text is four
`%08X` dwords of the *little-endian* values at `0x24`, so it must be packed back per dword —
`bytes.fromhex` reverses all four and moves the header GUID with a one-line diff at `+0x24`.)

**The proof is byte identity.** `--dump` records every package the shipped file names, in that file's
own order, and must emit **20,111 bytes identical to the original** — the same standard `pkg-relay`
holds the container to. Measured on the dump: 168 of 168 records reproduce, 499 packages carry a UE2
header, and the emitted file matches to the byte.

**The use is a build tree.** `--tree` records every package in a directory, in name order (a
deterministic rule, since the cook's order is a GUID order nothing in a build reproduces), derives the
database's own header GUID from the records unless a source file supplies it, writes the file, then
**reads its own output back**: record count, order, every payload at its own offset, every export
resolving to `PackageCheckInfo`, and the imports reading as `PackageCheckInfo`/`Engine`. On the hybrid
tree that is **18 records for 18 placed packages, of which 17 are records the cook's file already has
and one is new** — `UPreview.u`, the package the cook never recorded. Pointing the quick checker at
that file finds 18 of 18, so the database is usable in place of the cook's:

| measurement | value |
|---|---|
| records emitted for the shipped dump's database | 168 of 168, 20,111 bytes, byte-identical |
| records emitted for the hybrid tree | 18 (17 shared with the cook's file, 1 new: `UPreview.u`) |
| read back through `ue2pkg` + the record reader | 18 of 18, all exports resolving |
| the quick checker run against our own file | 18 of 18 records reproduce |

So a build no longer has to be added to the database with `-c` against a patched build, or run with the
check bypassed, whichever way its layout moves: it writes the file it needs. What remains open in 6.2
is the **runtime** question — which path invokes the check on a load — which needs the xemu probe in
U5 / 7.3, not another read of the image.

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

## Which path invokes the check at runtime (measured 2026-11-14)

`tools/xemu/check-probe.sh` arms `bp-args.py` on the three readers that carry the `Packages.md5` string
(`0x00121490`, `0x00121730`, `0x00125a90`), the `MD5` exec dispatcher they sit behind (`0x00126860`) and the
records class's own body (`0x00187350` `Engine_UnPackageCheckInfo`), and holds the window open across
power-on, the title, the map list and a loaded mission. `--control-self` arms the address the CPU was
executing when the stub was sampled: that arm is in the game's mapping by construction, so a window where
the control fires and nothing else does is a true negative.

| boot | window | control | `0x00187350` | three readers | `0x00126860` |
|---|---|---|---:|---:|---:|
| 1 | title -> map list (the load's `pad=a` was swallowed, 0 px) | `0x0007fbae` fired | 2 | 0 | 0 |
| 2 | title -> map list -> `M00` loaded and settled (82,366 px; the frame reads O'NEILL's HUD and "Press the X button to open the door") | `0x003e6527` fired | 2 | 0 | 0 |

**The two stops are a constructor, not a check.** Both boots stop at `0x00187350` from the same return
address, `0x00013ec7`, which is inside `Core_UnObjBas` `0x00012620` -- the first caller the static table
lists. The body allocates the class (`push 0x520`; `call [eax]` through the operator-new slot at
`0x005bf510`), constructs through `0x0003af60`, and asserts through `PackageCheckInfo.cpp` when that returns
null. So boot builds the records class -- twice, the class object and its default -- and never opens the file.

**What this answers, and what it does not.** Nothing on the path from power-on to a loaded level reads
`Packages.md5`, so a rebuilt or re-laid-out package needs no database entry to be played, and the check
needs no patch for normal play. The readers are reachable only through `0x00126860`, which is a
*commandlet* entry point rather than a console command (next section), so there is no typed arm to try
as well as no load path. The armed bytes' read-back is not evidence either way -- the stub answered
`OK` for every break and the bytes read back `83`/`83`/`6a`/`6a`/`6a` on both boots, which is this
stub's documented behaviour -- so the control is what proves the arms landed.

## The invoker is a class-table slot, and the console never had the command (measured 2026-11-14)

The earlier version of this page called `0x00126860` "the `MD5` exec dispatcher" and left 6.2's last arm open on
it: type `MD5` on the console and the check runs. It is not a console command. The only dword in the image that
holds its address is `0x005046d8`, which is slot 30 (`+0x78`) of the class table at `0x00504660` -- the table
`inventory/xbe_class_table_names.csv` names `MasterMD5Commandlet` -- and the address has no `E8` call site
anywhere:

    $ python3 tools/vtable-reach.py DUMP/default.xbe --va 0x126860
    0x00126860
      call  none (no E8 rel32 targets this address)
      ref   0x005046d8  .rdata
      slot  MasterMD5Commandlet table 0x00504660 slot 30 of 31 -> dispatch offset +0x78
      vcall 110 candidate site(s) for +0x78 (offset only, class not implied)

So the ten `-a -c -s ...` literals are not console switches but the **`Parms` argument of a commandlet entry
point**, which is exactly the shape the body shows: one wide argument `__wcsicmp`ed against ten literals. A UE2
`Commandlet` is started by the engine launcher from its own command line (`ucc <name> -a`), and this build is
started from a disc by the Xbox dashboard -- nothing hands it one. The family around it is the same machinery
(`ServerCommandlet`, `UModUnpackCommandlet`, `ExportCacheCommandlet`, `CompressCommandlet`,
`DecompressCommandlet`), and `StreamCommandlet extends Commandlet` is declared in `Engine`'s own script text, so
the classes ship without a way to name them on this platform.

**What the vcall column does and does not say.** There are 110 `call [reg+0x78]` sites in the image and none of
them implies a class, so the invoker is "whichever code holds a `MasterMD5Commandlet`" and that question is not
answered here. What *is* answered is the one this page needed: the address is a virtual member, so no console
name binds to it -- a console command is resolved by name against the class's exec table, not through slot 30.

**One console boot agrees, and its frames are not the evidence.**
`tools/xemu/restart-level.sh --command "md5 -s" --arm 0x126860,0x125a90` booted the disc, attached the USB
keyboard before the title, loaded `M00` (the mission frame moved 20,706 px and the settled frame 6,705 px, and
reads O'NEILL's HUD), opened the console, typed the line one `sendkey` per character and submitted it with
`ret`. The control arm fired (`0x002b7112`), so the window is not an arm that never landed; the dispatcher slot
and the reader `0x00125a90` stopped **zero** times; and `04-typed.png` is byte-identical to `03-gameplay.png`,
which is this disc's documented behaviour (the stock `Engine.Console` draws no input line). That boot therefore
cannot separate "the keys never reached the console" from "there is no such command" -- the slot is what
decides it.

**Consequence for 6.2.** No load path opens `Packages.md5`, and there is no console binding to type either, so
both arms are closed: a rebuilt package needs no record to be played and nothing in the build can add one.

## Not measured

* **What the record frame's filler bytes mean** — the `00`, the two `01`s and the seven trailing
  zeros are identical in all 168 records, so they are reproduced verbatim and not explained. Reading
  them would mean decompiling the `0x00125a90` record builder for field widths, which buys nothing
  while no record in the corpus varies.
* ~~**Invoking** the check by hand~~ -- **closed, as unreachable rather than untested**: `0x00126860`
  is slot 30 of `MasterMD5Commandlet`'s class table and has no `E8` call site, so the check runs as a
  commandlet and this disc has no command line to launch one with. The load path is measured above
  (zero reads of the file from power-on to a loaded `M00`), and the console boot above stopped
  the slot zero times.
