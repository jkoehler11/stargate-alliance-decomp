# The symbol map (`symbols/xbox.map`)

Status: emitted and checked. **17,425 symbols** — 16,933 functions plus 492 class tables — every one
inside the image's own sections, and `gdb` resolves them from the assembled form. This is PLAN 4.10.

The function count is not a constant: it was 14,973 when this page was written and has grown as the map's boundary
repairs split bodies that a first pass had merged (the naming percentage moves with it — `docs/STATUS.md` carries the
current pair). Quote the artifact, not this paragraph, when a number matters.

## What is in it

| artifact | what it is | in git |
|---|---|---|
| `symbols/xbox.map` | `address name size module`, sorted by address, one line per symbol | yes |
| `symbols/xbox.sym.s` | one absolute global symbol per name, assembled into an object file a debugger loads | no (regenerated) |

```
make symbols      # writes symbols/xbox.map and symbols/xbox.sym.s, then assembles the .s
```

The names are the build's own, from the same places the native track measured them:

- **file attribution** from the assert string a function references (`Core_UnProp`, `Engine_UnActor`),
  1,300 functions;
- **native table names** from the runtime `GNatives` dump (`execAdd_IntInt`, 273) and the 83 slots no
  shipped package declares;
- **dispatch and override names** derived from the build's data (`ucpp_Register_<Class>`, 40
  registrations, plus `appAssertFailed` and `appMallocBeforeInit` named from the strings they log);
- **`_exp`**, a CRT intrinsic stub named from the dispatch entry it hands `__cintrindisp1`
  (`tools/crt-dispatch-names.py`; the entry's own length-prefixed name is the evidence);
- **`Vtbl_<Class>`** for the 466 class tables named by the class registrations (`docs/NATIVE_CLASSES.md`);
- **`ctor_<Class>` / `dtor_<Class>`** (463 + 465) for the functions that install one of those tables with a
  constructor's or a destructor's shape (`tools/class-ctors.py`, `docs/NATIVE_CLASSES.md`); the 8 stores
  that fall in no mapped function (listed with the test each failed in
  `inventory/xbe_store_seed_residual.csv`), the overloads of a class with several constructors and the 26
  classes no registration literal names keep `FUN_*`;
- **`<Class>_lookup`** (199) for the generated intrinsic-lookup walkers: each walks one `{UTF-16 name,
  handler}` table, the table's keys are `int[AU]<Class>exec<Func>`, and over both halves every table states
  exactly one class -- so the class is the name the copy does not otherwise carry. Two copies that bind one
  class are numbered `_2`, `_3` … by ascending address (112 of the 199), no base name collides with a name
  the build already carries, and the module is empty because a class is not a translation unit
  (`tools/gap-body-families.py`, `inventory/gap_walker_names.csv`; `docs/systems/CLASS_REGISTRATION.md`);
- everything else keeps Ghidra's `FUN_000xxxxx`, which is an honest unknown rather than a guess.

## Loading it against the original XBE

```
as --32 -o symbols/xbox.sym.o symbols/xbox.sym.s          # or: make symbols
gdb -batch -ex "symbol-file symbols/xbox.sym.o" \
    -ex "info address FUN_0004bf20" \
    -ex "info address Vtbl_ByteProperty"
```

Measured output of exactly that:

```
Symbol "FUN_0004bf20" is at 0x4bf20 in a file compiled without debugging.
Symbol "Vtbl_ByteProperty" is at 0x4e10c0 in a file compiled without debugging.
```

To read the running build, attach to the emulator's GDB stub (`docs/XEMU.md`) and the names stay
valid, because the image is loaded at its linked base:

```
xemu … -s &                                             # stub on localhost:1234
gdb -batch -ex "set architecture i386" -ex "symbol-file symbols/xbox.sym.o" \
    -ex "target remote localhost:1234" -ex "x/3i 0x0004bf20"
```

That last step is the recipe, not a measurement taken here: this task verified the file loads and
resolves, not a live attach.

## What the map does and does not claim

- **Every address is inside the image.** `make symbols` passes `--xbe` and the tool fails if a symbol
  falls outside the section table: measured **17,425 of 17,425 inside, 0 outside**.
- **Names are file-level, not unique.** 133 names are used by more than one function — the assert
  attribution names a function by its source file, so `Core_UnTemplate` covers 308 of them,
  `Engine_UnActor` 97, `Core_UnClass` 69. The `.map` keeps the build's own text; the assembled `.s`
  gives every duplicate an `_<address>` suffix (1,160 symbols affected) so a debugger can address
  them separately, and the count is printed on every run.
- **8,051 functions are still `FUN_*`.** They are in the map — a debugger needs the address even
  without a name — and they are the engine bodies with no naming evidence (`docs/NAMING.md`), not a gap in
  this file. The library sections are named from a locally supplied XDK 5849.17 set (`make xdk-lib-names`,
  `make xdk-call-names`, `make xdk-lib-residue`, PLAN 4.2): **2,855 of the 3,109** bodies there, the rest of
  the library names landing in `.text` (`inventory/xdk_lib_names.csv` records the section per row). The
  class-construction pass took the remainder from 12,935: 915 functions carry the class whose object they
  build.
- **No types, no variables, no source lines.** The map carries code addresses and class-table
  addresses only; there is no debug information in this build (`RSDS` absent, PLAN §0).

## Body boundaries: the repairs, and the file that carries them

Ghidra infers where a function ends, and its inference is wrong in three ways that a census reads as fact: a body
**split** at an assert block that nothing calls, a body that **stops short** of its own last store, and a body it
**never made** because nothing calls it. `symbols/xbe_boundaries.csv` is the reviewed list of those repairs — one
row per repair, with the instructions that settle it — and `tools/symbol-boundaries.py` applies them to
`symbols/xbox_functions.csv` in its own right:

```
make boundaries        # apply symbols/xbe_boundaries.csv to symbols/xbox_functions.csv (no dump, no Ghidra)
make symbols           # then regenerate the map and the assembler form
```

| verb | what the row says | validation |
|---|---|---|
| `resize` | this row ends at the address the evidence measured, not at Ghidra's edge | positive; may not run into the next row |
| `drop` | this address is not a body, it is interior code of the row named in `target` | must land strictly inside that row's repaired span — which may be an `add` in this same file, the pairing a body split at an assert block needs |
| `add` | a body the map has no row for; `target` carries its name | may not land inside any row, and needs a matching `xbe_overrides.csv` row — that file is what creates the function |
| `rename` | the name this row's own instruction settles; `target` carries it | the map must have the row; moves no edge, so it cannot join an overlap |

**2,221 rows** are in the file: 1,780 `add`s, 53 `rename`s, 369 `resize`s and 19 `drop`s, and 313
of the resizes are one family -- the row that stops on its own epilogue (`tools/symbol-extents.py`). Each is one measured
thing, and most of the adds are one family: 537 slot-named bodies, 590 register-once thunks and 5 `_atexit`
destructor registrations, the registration table's 268 native bodies, 595 generated stubs, and the 243 the
declaration walk named after the class that declares each slot. The rest are one at a time:
the `Core_UnTemplate` row at `0x0002f820` folded into the audio body it belongs to (`0x0002f13c`, 2,282 bytes),
the pump at `0x0002feb0` given its real end (`0x3137f`), the body at `0x0002fa30` -- slot 33 of
`XBoxAudioSubsystem`'s table, which calls `XboxAudio_FlushSounds` over `[this+0xd8]` -- added, eight other rows
that stopped inside the slot load or the `rdtsc` pair of a scope close resized to their own closing `ret`, the
CRT's `sinh` stub added with `ULevel::MultiLineCheck` and one 63-byte BSP getter, the registration table's 268
native bodies and the six a false split sat inside, four more false splits the sweep found from a row the map
already had (a `resize` plus the `drop` of the fragment inside it), and four rows that stopped short. The map is
**16,935 functions and 4,260,865 bytes**, 8,051 of them still `FUN_*`.

`--check` runs in `make ci` through the tool contract: it re-applies the file to the committed csv in memory and
fails if the bytes differ, so a Ghidra re-import that undoes a repair is red instead of silently reverting the map.
It also refuses a boundary row whose `function_size` in `symbols/xbe_overrides.csv` disagrees with the map — that
column was written by the registration pass and read by nothing — and refuses an `add` with no row in that file,
because `XbePost.java` builds a function from an override row and an `add` alone is a body the next import cannot
see (U37).

## The extents the map stops short of

A `size` is Ghidra's inference like every other boundary, and the map's largest extent defect is a family
rather than a correction: a row whose last instruction is not a terminator. A body does not fall out of its
own end, so the bytes after such a row are its own epilogue, and the repair is a `resize` to where they
close. `tools/symbol-extents.py` measures the population over every `.text` row and writes the resizes into
`symbols/xbe_boundaries.csv` (`make symbol-extents`, then `make boundaries`).

<!-- generated:symbol-extents -->
**726 of the map's 13478 `.text` rows do not end on a terminator: 12752 close on one, and 648 of the rest leave a tail the map never counted** -- read by decoding every row at its own head and classifying its last instruction (`inventory/symbol_extents.csv`).

**The rule.** A body does not fall out of its own end: what follows a `pop ebp` or an `add esp,0x10` is the same function's epilogue. So a row whose last instruction is not `ret`/`jmp`/`int3` is unfinished, and the tail is measured three ways. **85 rows carry a gap head at their end** -- the head rule's own sweep closes it, so the head's measured size is the tail (**6599 bytes**). **228 more have no head there**, and a decode from the row's own end reaches a terminator with nothing but fill (`0xCC`/`0x90`) between it and the next map row (**2005 bytes**). **309 rows are cut mid-instruction by a size that is a byte count rather than a boundary**: the decode is read across the cut, and the row ends where the completed instruction lands when that is a terminator itself (**8**) or where the run behind it reaches its first terminator (**272**, 2608 bytes). The first terminator closes the run when only fill follows it, when it is a `jmp` -- a tail call, its target code elsewhere -- or when it is a `ret` with a pad of two or `CC` behind it (**33 rows, 325 bytes**), because only a run of two or more `int3` bounds a body. The code behind a close is not the body's own code, so a close with code behind it is read three ways: **12 rows** end where the next instructions are alignment padding, **7** where they open a body of their own, and **3** where they read the same frame -- the shared epilogue an early return jumps into -- and the body runs on to the next close (**339 bytes**). Together **648 rows and 11895 bytes**: 207 of them grow by one instruction or less (a row that stops on `pop ebp`/`add esp,N` before its own `ret`), and the largest is `0x002a002a`, a 20-byte row continued by 4501 bytes.

**The control is the same rule over data.** Decoding from **955** fixed addresses in the image's data sections finds a `ret` with only fill after it **2** time(s) -- 0.2% -- because that shape is what code has and data does not. The population it is compared against is the 648 rows above, whose tails are code by construction (the row's own head decodes into them).

| the row | its size | last instruction | evidence | tail | repaired size |
|---|---:|---|---|---:|---:|
| `0x002a002a` FUN_002a002a | 20 | `movss  DWORD PTR [esp+0x14],xmm0` | head `0x002a003e` body | 4501 | 4521 |
| `0x003873a0` FUN_003873a0 | 1375 | `add    esp,0x14` | head `0x003878ff` body | 599 | 1974 |
| `0x00378f00` FUN_00378f00 | 142 | `jge    0x378f72` | head `0x00378f8e` body | 539 | 681 |
| `0x00381f90` FUN_00381f90 | 347 | `pop    ebx` | head `0x003820eb` body | 263 | 610 |
| `0x0038c4f0` FUN_0038c4f0 | 1190 | `add    esp,0x10` | head `0x0038c996` body | 229 | 1419 |
| `0x0002be40` FUN_0002be40 | 225 | `inc    esi` | `int3` at `0x0002bf7a` | 89 | 314 |
| `0x000a2f00` Engine_KPhysic | 1911 | `je     0xa3686` | head `0x000a3677` body | 73 | 1984 |
| `0x000bdf70` FUN_000bdf70 | 2026 | `mov    ecx,DWORD PTR [esp+0x10]` | head `0x000be75a` body | 70 | 2096 |
| `0x001e75b0` Engine_UnRenderLight | 9260 | `ret` | `ret` at `0x001e9a22` | 70 | 9330 |
| `0x0006df00` Core_UnObj | 5479 | `cmp    al,0x79` | `ret` at `0x0006f4ac` | 69 | 5548 |
| `0x00241e70` FUN_00241e70 | 2123 | `mov    ecx,edi` | `ret` at `0x002426f8` | 61 | 2184 |
| `0x0006f4b0` Core_UnObjBas | 5552 | `ret` | `ret` at `0x00070a9c` | 60 | 5612 |

**What this does not repair.** **22** rows still end mid-instruction (`short`): nothing spans the cut in 4, the completed instruction runs into the next row's start in 6, the run behind it does not close in 12, and in 7 the completed instruction ends where the next row starts -- a split, listed below. **0** do not decode at all. **4** rows are already bounded where the map says they end -- a pad of two or more `0xcc` begins exactly there, so the body has no tail and nothing is missing. Of the rest, **38** stop on live code with no close before the next row (**7** a `jmp` with code behind it, **9** a `ret` followed by code that is neither padding, a body's opening nor the same frame, **12** no terminator at all, **10** no byte behind them), and **14** are a body the map already split from its own continuation -- `0x0001f2a0`, `0x00024c80`, `0x002c4f10`, `0x002e0072`, `0x00310020`, `0x003a0020`, `0x003a0044`, `0x003a005a`, `0x003a0061`, `0x003a0068`, `0x003a0069`, `0x003ee1ea`, `0x00410020`, `0x0041004d` -- which is a `drop` repair and needs a review this rule cannot supply.

**The repairs are in the file, not in this tool's output.** `--write-boundaries` appends one `resize` row per continuation to `symbols/xbe_boundaries.csv`, and `tools/symbol-boundaries.py --apply` moves the map; `--check` proves every continuation's repair is still applied, so a Ghidra re-import that reverts one is red instead of silently restoring the short extent.
<!-- /generated:symbol-extents -->

## The bodies no call graph can see: the registration table's 883

A native function has no caller. Its only reference is its row in the registration table, so a disassembly that
builds functions from call edges never makes one — and `symbols/xbox_functions.csv` is that disassembly.
`tools/native-census.py` (PLAN 3.9) has measured the gap since 2026-11-10: of the **883** distinct body addresses
`inventory/native_targets.csv` names, **274** fell outside every row of the map. All 274 were independently
accepted as function starts — each follows a `0xcc`, zero or `ret` boundary, opens on an MSVC prologue, and the
disassembler created a body at it that ends on a flow terminator — and none of them ever became a row.

They are rows now. `native-census.py` gained the third reading a repair needs — **how far each body runs in the
image** — and the two emitters that write it out: `--emit-boundaries` prints the `add` row and
`--emit-creations` the `xbe_overrides.csv` row that `XbePost.java` builds a function from, so a fresh import
keeps the body instead of reporting it missing. **274 of the 274 are in the file**, and **883 of the 883 registration bodies are map rows**: the six
U39 refused are written as an `add` plus the `drop` that folds the fragment inside them, because the fragment
is an assert block the body inlined and an `add` alone would overlap it. Sizes are 107, 274, 197, 212, 401 and
92 bytes.

The same rows answer the other direction, and the answer is a decode rather than the walk. `sweep`,
`sweep_closes` and `sweep_refused` are a **linear decode from each body's start** that accepts an end only where a
complete instruction closes on an unconditional terminator *and* the bytes to the next map row are a tail rather
than code. There are five tails, and they are the population the earlier rule could not read: `pad` (`0xcc` fill
of any length -- a single `int3` in front of the next row is padding the map's own start corroborates), `edge`
(nothing), `align` (MSVC's `8b ff` hotpatch slot, `90`, `0f 1f..`), `table` (a `switch`'s dword case table: every
entry an offset inside the body) and `bytes` (the byte-index table MSVC puts *behind* that dword table when the
case values are dense -- small slot numbers, sixteen or more of them), or a concatenation of those. A `0xcc` run also *ends* the reading,
because what follows a pad is another body: 22 rows sit in a map gap whose next row starts hundreds of bytes
later.

Read against the repaired map the decode reproduces the declared size to the byte on **882 of the 883** rows --
**838** close on a pad, **40** exactly at the next row, **1** on a table behind its pad and **4** on an
`align` + `table` tail (the two `execNativePerform`/`execLocalPerform` dispatchers among them) -- and the 883rd
is the row the decode closed: `0x00046330` (`execEqualEqual_ObjectObject`) declared 111 bytes in front of a
`jcc`, and the rest of its body is a *second* path -- an early `ret imm16` on the shared-reference branch with
code behind it, then the same 81-byte tail its `execNotEqual_ObjectObject` twin has. Counting that early return
as a boundary is what refused the row for three bundles; only a terminator **with a tail behind it** ends a
body, so the row is a `resize` to 192 and **883 of 883** now agree. The walk the sweep replaces could read a
*neighbour's* close: `0x00041000` declares 33 bytes on a 144-byte body, the island said 437, and the decode
says 144.

**The decode is the only extent reading.** `native_census.csv` used to carry a second
(`extent`/`extent_source`/`extent_closes`: Ghidra's `body_bytes` where it closed on a terminator, else the
island walk) beside `sweep`, and the two disagreed on nine rows -- every one of them the island's answer being
a neighbour's close. The columns are gone rather than reconciled: `sweep` is how far the body runs, and
`body_bytes` was already the disassembler's own column. Unifying them moved no row -- all 274 `add` rows
already carried the decode's size -- which is the control that the two readings had agreed where it mattered
and could not both be right where they did not.

A body no table names earns a row the same way. `0x002b7aa0` sits in the gap between two
`D3DDrv_D3DRenderInterface` rows and is a `switch` over D3D error codes whose 24 cases push the code's own name
(`D3DERR_WRONGTEXTUREFORMAT` ... `D3D_OK`, plus `%08X`) into an `FString`; **564 bytes** close on a `ret` at
`0x2b7cd4`, and behind it sit the body's tables -- 12 dwords, then 65 bytes of slot numbers ending
`05 05 05 03 04` -- before the 11-byte `int3` run at `0x2b7d45`. That second table is what three runs could not
read: a reader that knew dwords desynchronised *inside* it, swallowed the pad's first byte, and ran on into the
VM state-stack body at `0x2b7d50` for 2,151 bytes (the island walk, stopping at the first `cc cc`, said 677).
Nothing calls it -- no `E8`, no dword reference at any alignment -- so `native-census --gap-add 0x002b7aa0
--name FUN_002b7aa0` wrote the `add` and the creation row from its own measurement.

The 48 refusals the earlier rule reported were **not** 48 unreadable bodies: 39 of them close on the single
`int3` a two-byte pad rule walked through, and the shape is what settles them. The growth rule keeps the rest
honest -- a row grows only when its declaration was cut mid-instruction, never over a declaration that already
ends on a terminator, which is what stops ten rows from swallowing the body behind them. Eight rows are `resize`
rows and four of those are a false split rather than a short row: `0x00041d70` (32 → 130), `0x000470c0`
(235 → 293), `0x001c6940` (181 → 236) and `0x0020ff00` (371 → 454) each enclose a map row -- two assert blocks and
two starts in the middle of an instruction pair -- so each repair is the resize plus the `drop` of the fragment
inside it. The other four are short rows: `0x00214a30` and `0x002fe5f0` (+7 each) and the two dispatchers (+3
each). **U39's second-repair residual is settled and needed no exception**: `0x00260aa0`'s own `ret 8` ends at 95,
a single `int3` separates it from a stray three-byte `ret 4`, and the decode stops at the `int3` -- the 95 → 99
reading walked through the pad.

## The map's gaps: 595 generated stubs became rows, the split rule learned the jump table, and twelve openings the prologue table never named

The map covers **3,616,192** of `.text`'s 4,227,324 bytes (85.5%), so **11,070 gaps hold 611,067 bytes** --
14.5% of the code section. `tools/gap-heads.py` reads them in three passes this section measures in order:
the head rule, the split rule that ends a merged run, and the reference channels that decide whether a body
can be named at all.

A **head** is an address in a gap with a run of `0xCC`/`0x90` fill in front of it and one of the MSVC
prologue shapes `native-census` classifies the 883 registration bodies with -- or one of the openings
`EXTRA_PROLOGUES` adds, each measured against the map's own rows below. It is a **body** when a linear
decode from it closes on an unconditional terminator *with a tail behind it* -- the same `sweep_body` the
registration census uses -- and an **island** when that close is one or two instructions. **2,599 heads are
left, 2,193 of them bodies and 317 islands**, covering **283,860 bytes** of the 583,843 -- 49.0% of the gap
bytes, against 34.0% before the twelve openings below were read. Two decoder rules the `.text` bodies
needed are newer than that count: a `0x66` operand-size prefix shortens every immediate form, and a
`lea r,[r+0]` is an alignment nop rather than code (both in *What stops the 13* on
`docs/systems/CLASS_REGISTRATION.md`), which recovered four bodies the old read either refused or merged
with the next class's. The rule is a **floor**, not a ceiling: it does not find U38's `sinh` stub, which
opens `ba da d3 5c 00` (`mov edx,<entry>`) and not on a named opening.

**The two tests are not one rule, and the map's own rows are the control that separates them.** Of the
12,918 rows in `.text`, **1,206 have the fill but an opening the shared table does not name** and 181 of the
remainder are behind code instead of fill; with the shared table alone the four cells are **9,218 / 1,528 /
1,753 / 419** (both / fill only / opening only / neither). The "opening only" 1,753 are the boundary rule's
miss, and **1,851 of the 2,172 no-fill rows stand behind a terminator**, which is exactly what the split arm
reads -- so a body behind code is found when its opening is one the prologue test accepts. Three of them
were the interface constructors PR #808 could not list, and **all three open `8b c1` (`mov eax,ecx`), a shape
the shared table has never named**: #808's note that the census "does classify it (`mov-eax`, 185 heads)" read
`b8` (`mov eax,imm32`) as `8b c1`. With `8b c1` in `EXTRA_PROLOGUES` all three are heads, at the same 24, 106
and 17 bytes that bundle measured by hand (`0x000e1700`, `0x001df4f0`, `0x00200600`).

Three controls ride in the same run, because a count of decodes is not evidence:

* **the null population.** The same rule over **3.3 MB** of the image's data (`.rdata` 860,876 bytes,
  `.data` 2,255,828, XNET 77,016, DSOUND 124,556) finds 646 pad-before prologue-shaped byte runs and
  **0 bodies in `.rdata`, `.data` and XNET -- and 403 in DSOUND**. DSOUND is not data: it is the DirectSound
  segment, the map places **427 rows** in it (XNET 413, XGRPH 642, D3DX 379, D3D 302, XPP 242), and the 403
  are its code, which the decoder used to refuse on the same `0x66` immediate forms the `.text` gaps needed
  (`0x0049f360` alone is 12 instructions ending on `ret $0x10`). So the control's zero over DSOUND was a
  property of the decoder, not of the rule, and only the two genuinely data sections support the claim:
  **0 of 216 runs in `.rdata` and 0 of 13 in `.data` decode to a body**, which is the test the twelve
  openings below still pass.
* **recall on bodies that are known to be bodies.** Over the 12,918 rows the map now places, the two tests
  pass on **10,424 (80.7%)** and those rows decode to their declared size on **12,112 (93.8%)**. The four
  cells the two tests make, and the same four with the shared prologue table alone, are in the block below:
  the residue is 322 rows no opening names behind fill and 238 behind code.
* **the naming channel's base rate.** A dword outside `.text` names a *random* gap byte 1.6% of the time
  (33 of 2,000) and a map row's start **30.9%** (618 of 2,000). The pointer rate below is evidence only
  against those numbers: **1,161 of the 2,850 heads** carry one, 701 of those in a class-table slot, against
  a 1.6% chance.

**A terminator with code behind it is a boundary when nothing names that code -- and a `switch`'s case
blocks are named by its jump table.** The head rule used to walk past every terminator, so two bodies with no
fill between them read as one run: `0x00418c30` was **five 21-byte bodies in a 131-byte run**. The walk now
reads every relative branch in the run -- `E9`, `EB`, each `jcc`, `loop`, `jcxz` and the `0F 8x` long forms --
and splits where a terminator's successor is a strong opening (`mov r,imm32`, `push imm32`, a call, a
prologue) that nothing in the run targets: **239 bodies now end at a split** rather than at a tail, and
**1,274 openings** stay inside the body because a branch rejoins them (the `0x00046330` shape,
`execEqualEqual_ObjectObject` returns early into its own tail). An opening a branch *does* target stays
merged; so does one a **jump table** names. The extension below is what moved both counts: 215 and 154 were
the numbers of a rule that could not see `8b c1`, `a1 <imm32>` or `8b 41 <disp8>` as openings at all, so
neither an early return nor a neighbour behind one of them could be counted.

That second channel is this bundle's correction. A `switch` compiles to `jmp [reg*4+<table>]`, an *indirect*
branch no relative read sees, and MSVC 2005 lays the table out inline in `.text` as consecutive absolute
addresses: **144 such runs hold 1,510 entries**. The previous bundle applied the relative rule to the map's
own 12,321 rows and read **59 of them as two bodies**, calling that the same defect a `resize` plus an `add`
repairs. It is not the same defect: **348 of the 59 rows' 385 openings (90%) are entries of a `.text` jump
table**, so those rows are `switch` dispatches and a `resize` would cut a function at its own case block. The
rule consults the table now. Over the gap population the same channel names **0 of its 154 split heads**,
which is what makes those splits bodies; the 13 it did name merged back (172 splits / 2,140 heads before the
adds, 159 / 2,127 after them).

**Seventeen rows are still cut once the table is read, and every one of them is unnamed.** With the twelve
openings admitted the relative rule alone reads **86** rows as two bodies, and **348 of their 384 openings
are entries of the 144 inline jump tables**, so 17 remain; none of the 17 opens on anything a class-table
slot names, so they stay whole -- "nothing names it" is not evidence that it is a body. (The one repair this
rule did earn was `0x0014c260`: a 3-byte `ret 4` that `IMusicActor`'s class table names at slot 123, behind
a row an earlier bundle had *extended* to 179 bytes by walking through the row's own `ret 8`. That resize was
replaced rather than quietly dropped, and the row now ends at 176 bytes.)

**One gap is a generated block, not missed code, and it is 595 rows now.** `0x004100c8`..`0x00418cc0` holds
**590 register-once thunks** -- `push <&item>; push 0xff; call FUN_00046c60; add esp,8; mov <flag>,al; ret`,
466 of 21 bytes and 124 of 24, each with its own 4-aligned `.data` item (`0x5c29e0`..`0x5c667c`) and its own
flag byte -- and **5 `_atexit` destructor registrations** (`push <dtor>; call _atexit; pop ecx; ret`). The
helper is a 92-byte body the map already has: it seeds 4,096 dwords at `0x6313e8` with `0x404a0` and refuses
a second call, which is what makes the block a translation unit's static initialisation rather than 600
unrelated functions. Every one of the 595 is a row now, named from the only thing it names:
`static_init_<item>` for the 590 (the `.data` object the thunk registers) and `dtor_<destructor>` for the 5,
each with its `xbe_overrides.csv` creation row. They left the census because they are rows -- 2,127 heads to
1,527, 1,850 bodies to 1,249 at that point -- and took the map from **15,778 to 16,374 functions** (4,177,607
to 4,190,426 bytes of code). The block is not exhausted by them: the census now reads **183 more bodies**
between `0x004100c8` and `0x00413c40`, 135 of 89 bytes opening `8b 0d <imm32>` and 47 of 47 opening
`a1 <imm32>`, each with its own single `.data` pointer and no `E8` -- the same translation unit's earlier
registration table, which the shared prologue table could not open.

**The prologue test was the other half, and it is measured the same way.** 1,528 of the map's rows sit behind
fill and open on something the shared `native-census` table does not name, so the census walked past 1,528
known body starts that had a boundary in front of them. Ranking those rows by their first instruction gives
the candidate list; what decides each one is the same two counts everywhere in this page -- how many **map
rows** it opens, and how many **pad-bounded openings in the data sections** it also matches. `a1 <imm32>`
(`mov eax, ds:[imm32]`) opens 841 rows and 8 data openings; `8b 0d <imm32>`, `8b c1`, `8b 41 <disp8>`,
`d9 44 24` and the rest open 20 to 85 rows and **zero or one**; and `a3 <imm32>` (`mov ds:[imm32],eax`),
which looked like the mirror of `a1`, opens **no row at all** against 7 data openings, so it is not a
prologue and the tool refuses to carry it. The twelve that pass are `EXTRA_PROLOGUES`, their counts are
re-measured every run, and `--check` fails if one of them stops opening a map row.

Admitted there, they add **1,271 heads, 1,246 of them decoding** (the rest refuse to close), and 421 of the
new bodies are named by no channel -- the residue this page's last paragraph counts. They also take the
class-table slot count from 279 to **701 bodies in 724 tables**, because an opening the table refused was
also a body the *slot* channel could never be compared against. And they settle PR #808's open question
directly: the three interface constructors it read by hand (`0x000e1700`, `0x001df4f0`, `0x00200600`, 24, 106
and 17 bytes) are heads now, at exactly those sizes, because all three open `8b c1`.

<!-- generated:gap-heads -->
| measure | value |
|---|---|
| `.text` | `0x00011000`..`0x004190fc` (4227324 bytes) |
| gaps between map rows / bytes they cover | 11419 / 537299 (4017 gaps under 8 bytes) |
| heads the pad-before + prologue rule finds | 2195 |
| ...decode to a body / are a 1-2 instruction island | 1875 / 239 |
| ...refused (`no-close` / `undecoded`) | 36 / 45 |
| bytes the bodies cover | 255605 (48.0% of the gap bytes) |
| runs holding an inner strong opening (a branch rejoins it, so it stays merged) | 1139 in 945 runs |
| bodies a split ends (a terminator no branch in the run targets) | 190 |
| bodies in a generated family (`register-once` / `_atexit`) | 0 / 0 |
| named by a dword outside `.text` | 807 |
| ...of those, in a class-table slot (373 bodies; 724 tables, 5 of them class-named) | 365 |
| named by an `E8`/`E9` branch | 66 |
| named by a `68` push | 717 |
| named by any channel / by none (of them bodies / islands) | 1573 / 541 (470 / 71) |
| boundaries: the gap's start / fill / a split | 130 / 1875 / 190 |
| heads a measured opening adds (not in the shared prologue table) / of them decode | 817 / 801 |
| ...of those, named by no channel | 209 |
| control: map rows passing the head rule / decoding to their size | 10889 of 13478 / 13349 |
| ...the two tests apart: both / fill only (prologue miss) / opening only (boundary miss) / neither | 10889 / 322 / 2031 / 236 |
| ...the same four cells with the shared prologue table alone | 9270 / 1941 / 1823 / 444 |
| ...of the no-fill rows, the successor of a terminator (what the split arm reads) / of nothing | 1954 / 313 |
| ...rows the measured openings name that the shared table refuses: behind fill / behind code | 1619 / 208 |
| ...whose declared span the split rule reads as two bodies: relative branches alone / with the inline tables | 98 / 21 |
| ...split points that reading means / of those, entries of a `.text` jump table | 423 / 382 |
| ...of the rows still cut, a class-table slot names the opening / nothing names it | 0 / 21 |
| inline jump tables in `.text` (aligned runs of >=3 code addresses) / entries | 144 / 1510 |
| control: the same rule over `.rdata`, `.data`, XNET, DSOUND (646 head-shaped runs, 3.3 MB of data) | 0 / 0 / 0 / 403 bodies |
| base rate: a dword outside `.text` names a random gap byte / a map row's start | 30 of 2000 (1.5%) / 653 of 2000 (32.6%) |

| gap | heads | bodies | first head |
|---|---:|---:|---|
| `0x004100c9`..`0x00413c40` | 182 | 182 | `0x004100d0` |
| `0x0040c2c8`..`0x00410020` | 158 | 157 | `0x0040c2c8` |
| `0x003f28b8`..`0x003f6d70` | 87 | 87 | `0x003f28b8` |
| `0x000e031f`..`0x000e06a0` | 56 | 56 | `0x000e0320` |
| `0x003fc208`..`0x00400340` | 47 | 47 | `0x003fc208` |
| `0x00025b3d`..`0x00025d50` | 33 | 33 | `0x00025b40` |
| `0x002f5176`..`0x002f5330` | 27 | 27 | `0x002f5180` |
| `0x000dff86`..`0x000e0100` | 23 | 23 | `0x000dff90` |

| opening this census adds | map rows it opens | pad-bounded data openings |
|---|---:|---:|
| `global-load-eax` | 1089 | 8 |
| `global-load-ecx` | 78 | 0 |
| `global-load-edx` | 23 | 0 |
| `self-return` | 93 | 0 |
| `getter-disp8` | 158 | 1 |
| `getter-disp32` | 89 | 0 |
| `fpu-arg` | 76 | 0 |
| `sse-load` | 58 | 0 |
| `zero-return` | 74 | 1 |
| `add-reg-imm8` | 26 | 0 |
| `push-arg` | 33 | 0 |
| `bare-ret` | 30 | 4 |

373 of the 1875 bodies sit in a class-table slot; 367 of those are in the 232 tables no registration names, and 541 are named by no channel at all: nothing calls them, nothing points at them, and the map has no row for them.

The split rule does not hold on the map's own rows the way it holds on the gaps: 98 rows read as two bodies with relative branches alone, and **382 of their 423 openings are entries of the 144 inline jump tables** (`switch` case blocks, reached by an indirect branch no relative read sees) -- so they are one body each, and the rule now consults the table. Over the gap population the same channel names **0 of its 190 split heads**, which is what makes the 190 splits bodies rather than case blocks.
<!-- /generated:gap-heads -->

**What is left is what no channel names.** 279 bodies still sit in a table slot -- **264 of them in the 246
tables no registration names**, so there is no class to build a name from -- and **399 are named by no
channel at all**: nothing calls them, nothing points at them, and the map has no row for them. The pointer
channel names 369 of 1,451, a `68` push 651, a branch 46.

**The `<Class>_vt<slot>` names carry their class's module now, and the residue is the native classes.** 265 of
the 538 `<Class>_vt<slot>` rows resolve to a module through the package their class is declared in
(`inventory/classes.csv`, matched case-insensitively because the assert labels and the package names differ:
`StargateGame` / `stargategame`): `Engine` 156, `stargategame` 40, `Gui` 24, `stargatevehicles` 17, `Fire` 16,
`Gameplay` 10, `IpDrv` 2. The other **273** name classes -- `XboxViewport`, `XboxClient`, `TcpipConnection`,
`XBoxAudioSubsystem`, `XHVVoiceSubsystem` -- that no shipped package declares (they live in the native
`XboxDrv`/`IpDrv`/`XboxAudio`/`XboxLive` packages, which ship no `.u`), so `classes.csv` has no package for them
and they stay `(other named)`. The route to those is the native class list the installer registers, not a
naming rule.

`gap-heads --check` is dump-free: it re-derives the gaps from the committed map, reconciles the census row
against the rows it carries, requires every split to abut the row it split from, refuses a family or a
boundary it does not define, holds the map-row control's four buckets to the row count, and **refuses a cut
row whose opening a class-table slot names** (that one has to be repaired and re-measured, not shipped).
`--selftest` covers 18 cases (the head, the island, the refusal, the split, the rejoined early return, five
abutting bodies, both families, the `switch`'s case blocks and the table index, the gap bounds, the census
round trip and the marker refusal). `--emit-adds`/`--apply-adds` write a population's rows with their creation
rows, and `--emit-repairs`/`--apply-repairs` write a cut row's `resize` + `add` pair, replacing an earlier
resize instead of refusing it.

## Jump thunks: what each one jumps to, and the name that follows

Seventy-five rows of the map are not bodies. They are 2, 5 or 6 bytes that transfer control, and the map named each
one after its target **by address** — `thunk_FUN_003e67ed` — which is a name that stops being true the moment the
target earns a name of its own. `tools/thunk-targets.py` (`make thunk-targets`,
`inventory/thunk_targets.csv`) decodes each row's own instruction and settles it:

| encoding | bytes | rows | what it is |
|---|---:|---:|---|
| `e9 rel32` | 5 | 72 | near jump |
| `eb rel8` | 2 | 2 | short jump — `0x003e7b74` is `eb 00`, a hotpatch slot that jumps onto the next instruction; `0x003e9a2c` is `eb 0a`, onto the body ten bytes on |
| `ff 25 disp32` | 6 | 1 | `0x003978d0` is `jmp dword ptr [0x005ccae8]`, and that slot holds `0x003d8db0` — an entry in the image's own pointer table, not a direct jump |

**47 of the 75 land on a body that has a name**: 34 C++-mangled XDK methods (14 `UIXNamespace` — `CLiveEngine`'s
`Render`, `SetInput`, `GetNotifications`, `Reboot`; 8 `XHVNamespace` — `CXHVEngine`'s `EnableProcessingMode`,
`RegisterLocalTalker`, `VoiceMailStop`; 3 `CXo`, 3 `D3D`), three `_X…`/`_x…` entries (`_XcspFreeEverything@0`,
`_XcSHAUpdate@12`, `_x_corr`), nine CRT/other (`_wcslen`, `_wcscpy`, `__wcsupr`, `_convolve`, `_jpeg_destroy`,
`_png_free`, `_MidEstPitch`, `_USBD_Init@8`, `?…`) and `_exp` (the CRT dispatch stub below), and the rule carries
that name across the jump: `0x00036cb0` becomes `thunk__wcslen`. The other 28 land on a body that is still `FUN_*`
and keep `thunk_FUN_<address>`, because inventing a name for the target is not what a thunk tells you.

One row is a **chain**: `0x0007e4f0` is `jmp 0x0033e788`, which is `jmp 0x00343fae`, which is `jmp dword ptr
[0x004c6cc4]`. The artifact records the terminal hop and the hop count (2), so the chain resolves to a body instead
of to another stub. Each row's rename is a `rename` row in `symbols/xbe_boundaries.csv` (`--emit-boundaries` prints
them), so the map carries it without a Ghidra round and `--check` stays the thing that catches a re-import undoing
it; after the rename pass the map has **28** `thunk_FUN_*` rows left, all of them on unnamed targets.

### The 26 unnamed targets, by the shape their own bytes show

"Where does this go" is only half an answer when the destination has no name, and the other half is a fact the
bytes carry: what *kind* of thing the target is. The artifact therefore records a `target_kind` and the instruction
it was read from, and `--report` prints the table below. Only a target's own edges are read — its first
instruction, and for a forwarder the operation immediately before its `ret` — because a byte scan for a virtual
call finds the one a real body makes in its middle as readily as a stub's only work (measured 2026-11-20: a
`ff 53 08` in the argument setup of a 101-byte SEH body read as a forwarder until the rule was anchored to the
return).

<!-- generated:thunk-targets -->
| terminal target | thunks that reach it | shape the target's first instruction shows |
|---|---:|---|
| `0x0001b310` | 2 | body |
| `0x000624f0` | 1 | vdispatch: `mov eax, [ecx]; jmp [eax+0x20]` |
| `0x00064f00` | 1 | body |
| `0x00075c30` | 1 | body |
| `0x000c4dc0` | 1 | body |
| `0x000ef1a0` | 1 | body |
| `0x0017a250` | 1 | body |
| `0x00195150` | 1 | body |
| `0x001ca680` | 1 | body |
| `0x001d4d10` | 1 | body |
| `0x00278780` | 1 | body |
| `0x002c4ee0` | 1 | body |
| `0x0032fc55` | 1 | vcall: `call [reg+0x24]`, then return |
| `0x00343fae` | 2 | chain-jump: `jmp dword ptr [0x004c6cc4]` |
| `0x00344480` | 1 | body |
| `0x0034451b` | 1 | body |
| `0x003830f0` | 1 | body |
| `0x00398b50` | 1 | adjustor: reads `this` from the stack, replaces it, and tail-jumps |
| `0x003d8db0` | 1 | call-forward: one `call` and no other work, then return |
| `0x003da760` | 1 | body |
| `0x003e7b76` | 1 | body |
| `0x0049ff29` | 1 | body |
| `0x004a0372` | 1 | body |
| `0x004a03c4` | 1 | body |
| `0x004a0416` | 1 | body |
| `0x004a0e0a` | 1 | body |

**26 distinct targets** behind the 28 unnamed thunk rows: body 21, chain-jump 1, vdispatch 1, vcall 1, adjustor 1, call-forward 1.
<!-- /generated:thunk-targets -->

The five non-`body` shapes are **compiler furniture that never had a name in the source**: a chain hop
(`0x00343fae` is itself an indirect jump), a virtual-dispatch trampoline, a `this`-adjustor, a vtable installer and
a one-call forwarder. Naming those means naming the shape, not the body, so the map keeps `FUN_*` and this page
says which is which. The 21 `body` targets are where a naming pass has something to work with — and the biggest
family among them is a *negative* result worth recording: five 82-byte bodies in the `DSOUND` section
(`0x0049ff29`, `0x004a0372`, `0x004a03c4`, `0x004a0416`, `0x004a0e0a`) share one shape — a call to
`?DirectSoundEnterCriticalSection@@YGHXZ`, a test of `[0x004bd190]`, one more call, then a critical-section exit —
and all five are listed in `inventory/xdk_lib_residue.csv` as `no counterpart` in `dsound.lib`. They are the fork's
own API layer compiled into the DirectSound segment, so no library name exists to match and each one's identity
lives in the callee it wraps, not in its own bytes.

`--check` needs no dump: it reconciles every row with the map (every `thunk_*` map row has a row in the artifact and
no others do), with the rule (the name is `thunk_<target>` or `thunk_FUN_<terminal>`), with the encodings
(3-byte bodies are not a thunk, a 5-byte row is not an indirect jump) and with the shapes (`target_kind` is in the
fixed vocabulary, and a `body` never carries evidence or a furniture kind none). It also refuses a page whose
generated block has drifted from the csv. `--selftest` decodes synthetic bodies: a forward and a backward `rel32`,
both `rel8` forms, the indirect slot, a `call` and a bare `ret` refused, a truncated `rel32` refused, the three-hop
chain, and the seven target shapes. The artifact regenerates from the dump in under a second.

## CRT dispatch stubs: `exp`, and the `sinh` nobody calls

Two rows of the image are ten bytes that hand a table entry to `__cintrindisp1`:

```
ba ac d3 5c 00     mov edx, 0x005cd3ac     ; the entry at 0x005cd3ac is 03 "exp", flags, four code pointers
e9 38 47 00 00     jmp 0x003ee17a          ; __cintrindisp1
```

That is how MSVC compiles a single-argument transcendental intrinsic when the CRT keeps its own implementation:
the *entry* carries the name, the stub only supplies it. `tools/crt-dispatch-names.py` (`make crt-dispatch-names`,
`inventory/crt_dispatch_stubs.csv`) finds both stubs, reads the length-prefixed name out of the entry each one
passes, and reports the map row it has:

| stub | entry | name | map row | thunk onto it |
|---|---|---|---|---|
| `0x003e9994` | `0x005cd3da` | `sinh` | **none** | none |
| `0x003e9a38` | `0x005cd3ac` | `exp` | yes (was `FUN_003e9a38`) | `0x003e9a2c`, an `eb 0a` hotpatch slot |

So the map gains **`_exp`** — a name from the build's own data, not from the shape — and its hotpatch thunk
becomes `thunk__exp`. The `sinh` stub at `0x003e9994` sits in the 54-byte gap between `__ui64toa` (ends
`0x003e997a`) and `__aullrem` (`0x003e99b0`), and nothing in the image jumps or calls it: it is a body the
analysis never created, and creating one moves the map's function count, which is the boundary thread's work
(U36), not a rename. It is recorded in the artifact as `in_map = no` so the next session starts from the address
rather than from the observation.

## Per-module address lists, and why there are no module ranges

PLAN 4.5 wants the image split per module. `inventory/function_modules.csv` (`make module-split`) is that split, per
function: address, name, size, module, what attributed it, and which contiguous `run` of that module it falls in.
`--check` recomputes the per-module counts and **fails if they disagree with `inventory/xbe_functions_by_module.csv`**,
so the two cannot drift apart.

| module | functions | bytes | runs | span | coverage |
|---|---:|---:|---:|---:|---:|
| `(unnamed)` | 13,037 | 3,105,722 | 8,817 | 7,925,899 | 39.2% |
| `Engine` | 586 | 293,401 | 558 | 2,510,518 | 11.7% |
| `Core` | 574 | 386,721 | 570 | 3,244,777 | 11.9% |
| `(dispatch: declared)` | 273 | 83,301 | 269 | 2,876,663 | 2.9% |
| `(other named)` | 216 | 23,029 | 114 | 4,500,825 | 0.5% |
| `(dispatch: slot only)` | 83 | 9,761 | 81 | 2,801,033 | 0.3% |
| `Gui` | 55 | 13,308 | 54 | 126,657 | 10.5% |
| `stargategame` | 32 | 5,612 | 29 | 51,539 | 10.9% |
| `D3DDrv` | 22 | 15,540 | 22 | 90,521 | 17.2% |
| `IpDrv` | 22 | 7,543 | 22 | 86,338 | 8.7% |
| `XboxLive` | 13 | 7,148 | 13 | 11,536 | 62.0% |
| `stargatevehicles` | 12 | 2,521 | 12 | 38,340 | 6.6% |
| `XboxAudio` | 9 | 9,556 | 9 | 24,304 | 39.3% |
| `Fire` | 9 | 2,558 | 9 | 31,157 | 8.2% |
| `Gameplay` | 9 | 1,604 | 6 | 2,468 | 65.0% |
| `XboxDrv` | 5 | 775 | 5 | 4,916 | 15.8% |

**The table is a measurement, and it refutes 4.5's premise.** "Module → address range" does not exist in this build:
`Engine`'s 586 functions sit in **558 separate runs** across 2.5 MB and cover 11.7% of that span; `Core` is 574 runs
across 3.2 MB at 11.9%. Mean functions per run is 1.0–1.1 for every module. This is the same fact `docs/ARCHITECTURE.md`
states as "code is ordered per translation unit rather than per module" — quantified, and with a consequence: a module
is a *set* of addresses, and any per-module work has to be driven by a list, not a range.

That is what the tool emits:

```sh
python3 tools/module-split.py --lists "$WORK/export"
#   $WORK/export/Engine.txt — 586 addresses, one per line
# then one exporter pass per module, since ExportAt takes an @file:
#   ExportAt.java @$WORK/export/Engine.txt $WORK/dec/Engine
```

Only real modules get a list. The three `(…)` buckets are ways a function can carry a name without a module — dispatch
names, GNatives slots and MSVC-mangled symbols — and exporting them as a "module" would be inventing one.

**The COMDAT-duplicate route over the gap bodies yields 2, and the naive version of it says 10 (2026-11-21).** Asking
whether an unnamed gap body is the same code as an already-named function is cheap: compare the bytes with the operands of
`E8`/`E9`/`0F 8x` zeroed. Over the 2,034 gap bodies of 12 bytes or more that matches **10**. Eight of those are wrong, and
the mask is why: two `jmp` thunks to *different* targets both reduce to `E9 00000000`, so a comparison that hides the
operand cannot tell a copy from a lookalike. Requiring the rel32 targets to be equal as well leaves **2** --
`??0XAsmCD3DXDwStack@XGRAPHICS@@QAE@XZ` at `0x00186420` (copy of `0x0049b6c5`) and `??0Buffer@XGRAPHICS@@QAE@XZ` at
`0x00347975` (copy of `0x0047dc50`) -- and both are now rows. The route is exhausted at that yield: the other 2,032 gap
bodies are code nothing names and nothing duplicates.

**The CRT-message route is empty, and the bodies it would name are already named (2026-11-21).** A runtime body often
carries its own message, so an unnamed function referencing `R6025` or "floating point support not loaded" would be
identifiable without a byte match. Scanning all **7,448** unnamed functions for an immediate that points at a CRT-shaped
string finds **0**. The reason is not that the messages are absent: `R6025 - pure virtual function call` is in the image at
`0x00575cac`, but its **only** reference is at `0x005cd48c`, which is `.data` -- the string is reached through a pointer
table, and an immediate scan cannot see it. The bodies behind those messages are already named by other routes:
`__purecall` (`0x003e5501`, 20 bytes), `__matherr` (`0x00344e79`, 3 bytes) and `___crtFindFirstFileW` (`0x003ee7d4`, 93
bytes). A table-walking version of this scan would still be possible, but it would be looking for names that exist.

**Other SDK builds do not name these bodies, and the reason is version rather than luck (2026-11-21).** Three further Xbox
SDK builds were fetched from the same archive.org collection and measured against the gap population. `5849` (2003-12) and
`5933` (2004-02) are installers whose CAB carries only **12** libraries -- `d3dxof`, `dxguid`, `xgraphics`, `xonservr`,
`D3DX8` and their debug variants, with no `libc`, `d3d8` or `dsound` -- and both name **0** gap bodies. `5558.2` (2003-06)
is a complete tree: **66** library files, 55 distinct names including `libc`, `libcmt`, `d3d8`, `dsound`, `dmusic`,
`xapilib`, `xnet` and `xonline`. It also names **0** gap bodies, and 5 on the whole map, all of them `xgraphics` or
`D3DX8` -- a set the 2005-03 tree already names. So the masked-body match is version-sensitive: the **5849.17** set of
2005-03 is the one this build linked (the XBE header lists `XAPILIB 1.0.5849` and `D3D8 1.00.5849.1`), and other builds are
not a route to more names. The trees live at `~/sg1/work/xdk/versions/<build>/setup/XDK/lib` and are never committed; the
four `MicrosoftXboxSDK*` zips the collection also offers are Preview Pipeline tooling and contain **no** libraries at all.
