# The UnrealScript VM in `default.xbe`

Status: `GNatives` located (4.3) and **read out of a running build** (4.4): the table has no bytes in
the file, so it was dumped from xemu over the GDB stub. 356 of 4,096 slots are registered at the main
menu, all 273 declared native indices among them. The other half of 4.3 is now measured: the binder
that fills `UFunction::Func`, `UObject::ProcessInternal` at `0x00048bf0` (the bytecode loop) and
`FFrame::Step` at `0x000398e0` (the expression decoder) and `UObject::ProcessEvent` at
`0x0004bf20` — see *The body of a script function*. The property half of 4.3 is measured separately —
`docs/NATIVE_PROPERTIES.md`: the type names are reserved name indices 1..16, fifteen property classes
with their sizes and vtables, and the `+0x2c`/`+0x34`/`+0x38`/`+0x3c`/`+0x48`/`+0x6c`/`+0x70` fields
the walk at `0x0003bdc0` reads. `ProcessEvent`'s dispatch slot is measured too: slot 21
(`+0x54`) of 492 packed tables in `.rdata`. Still open: the tagged tag record (U10), and the 102
assert sites that still have no function (`docs/UNKNOWNS.md` U8).

## The dispatch table

| Field | Value |
|---|---|
| Address | `0x006313e8` |
| Entries (`EX_Max`) | 4,096 (`0x1000`) |
| Section | `.data`, past the end of its initialised bytes — the image stores nothing for it |
| Initial contents | filled at startup with one default stub, then patched per native |
| Index → address map in the file | **none**; the bytes are built at runtime |
| Static references to slots | 3,243, covering 256 distinct slots: 1,428 indexed accesses (`8b 0c 85 …`, `ff 14 95 …`), 776 direct `call [slot+N*4]`, 233 writes (`mov [slot], reg`) |

### How it is identified (`tools/gnatives.py`)

The build names the array and its bound in the asserts compiled around the interpreter — the strings
`GNatives[iNative]!=0` and `iNative<EX_Max`, referenced from the same routine:

```
0x3a083  cmp    ax,0x1000              ; iNative<EX_Max, EX_Max = 4096
0x3a0a0  movzx  eax,[esi+0x84]         ; FFrame::iNative
0x3a0a7  mov    ecx,[eax*4+0x6313e8]   ; GNatives[iNative]
0x3a0ae  test   ecx,ecx                ; assert "GNatives[iNative]!=0" (0x3a0c1)
0x3a0d0  mov    edx,[ecx*4+0x6313e8]
0x3a0d7  mov    [esi+0x90],edx         ; FFrame::Code = GNatives[iNative]
```

The tool derives that from the file rather than from a constant written by hand: it finds the assert
string, takes its single code reference, and reads the indexed-load displacement and the
`cmp ax,imm16` bound out of the window around the site. Output: `inventory/gnatives_table.csv`.

Two consequences worth carrying:

- **`FFrame` layout comes free**: `+0x00` vtable, `+0x84` `iNative` (word), `+0x90` `Code` pointer.
  `FUN_00058a30` — called 2,245 times from 1,270 functions, always as `(expression, file, line)` —
  is the engine's check/verify helper used by the asserts above (`docs/UNKNOWNS.md` U6).
- **Script index space is global**: 274 `native()` declarations over 18 packages carry 274 distinct
  explicit indices (no collisions), which is why a single table can serve every package. That is
  still true and independent of the address question.

## Reading the table from a running build (U7 resolved)

The table is filled at startup, so the emulator is the oracle. `docs/XEMU.md` has the exact commands;
the short version is `xemu -config_path drive5.toml -s` and then one `gdb` command against the stub:

```
gdb -batch -ex "set architecture i386" -ex "target remote localhost:1234" \
    -ex "dump binary memory runtime.bin 0x6313e8 0x6353e8"
```

16,384 bytes (`EX_Max` x 4), taken ~55 s after launch with the prototype at its main menu.

| Measurement | Value |
|---|---|
| Slots held by the startup default stub `0x000404a0` | 3,740 of 4,096 |
| Registered slots | 356 |
| Distinct addresses among them | 356 (no slot shares an implementation) |
| Entries outside `.text` | 0 |
| Declared indices registered | **273 of 273** (the 274th, index 2, is the editor-only claim in U3) |
| Registered slots with no declarer | 83 |

The map is `inventory/gnatives_slots.csv` (index, address, name, evidence) and
`symbols/xbe_dispatch.csv` (address, name) — the second is applied by `XbePost.java` as evidence
overrides, which is how the dispatch targets get names in Ghidra. The raw 16 KB dump is guest memory:
it lives under `$WORK/gnatives/runtime.bin` and is never committed; only the derived address map is.

**Why the registration stops at 356 — and why 356 is the whole table.** Natives are registered with the class
that declares them, and the front end has loaded every native class before its own menu: a second dump taken with
`M00` loaded holds the **same 356 slots with the same bodies, 0 gained** (next section). Registration therefore
finishes at startup, a mission adds nothing, and the 83 slots no package declares are engine-internal execs, not
natives waiting for a map. The 273 declared indices are covered for the same reason.

### A second dump, after a mission loads (U7 closed)

`tools/xemu/gnatives-mission.sh 10` is the whole recipe: boot to the title, hold the guest, `Continue`, ten d-pad
presses and `A` (index 10 is `M00`, `tools/xemu/map-index.py`), gate on `tools/xemu/frame-diff.py` proving the
frame left the menu, then read the 16 KB array over the stub. `make gnatives-mission` joins that read with this
page's menu-time map (`tools/gnatives-mission.py`, artifact `inventory/gnatives_mission.csv`, one row per live
slot with a `change` column).

| Measurement | At the main menu | After `M00` loads |
|---|---|---|
| live slots (not the startup stub) | 356 | **356** |
| distinct bodies among them | 356 | 356 |
| body in the same slot as the menu dump | — | **356 of 356** |
| slots gained / lost / replaced | — | **0 / 0 / 0** |

Every slot holds the same pointer, so the table is complete before the front end's menu and a mission load
registers nothing. That is U7's last open question answered, and it also settles how to read the 83 slots with no
declarer: they are registered at startup by the engine, not deferred to a map.

Two ways a second read lies, both measured on 2026-11-10:

- **The table is a `.data` address, and a physical read of that number is a different page.** A `mem.py --dump`
  in the stub's default *physical* mode put the array at physical `0x7dd3e8`: 353 of the 356 menu bodies matched,
  and from slot 3,846 on — `0x635000`, the array's last page — the window held **zeros**, which reads exactly like
  "the load zeroed the tail of the table". The linear space is the space the address belongs to:
  `tools/xemu/mem.py --virtual` returns 356 of 356. (`gdb`'s own `dump binary memory` reads linearly by default,
  which is why the first dump never met this.)
- **A boot that never left the menu still hands out its stub.** One attempt's presses were swallowed at a 135 s
  boot window (the highlight frame moved 331 px against a 710 threshold, then 0 px on the mission shot); its stub
  would have served the menu's table again. The script gates on `frame-diff.py` before it reads, and its boot
  window is `SG1_BOOT` (default 150).

The dump stays under `$WORK/mission-gnatives/index-NN/` — guest memory, never committed; only the derived map is.

**Validation.** The three operator natives behave like operator bodies: at `execNotEqual_ObjectObject`
(`0x000463f0`), `execAdd_IntInt` (`0x00041a50`) and `execAndAnd_BoolBool` (`0x00041120`) the code takes
the `FFrame` from `[esp+0xc]`, reads the operand byte at `[frame+0xc]`, advances that pointer and
pushes the argument — the standard UE2 exec prologue for reading an immediate. `execConcat_StrStr`
(`0x00049e10`, 318 bytes) is the largest of the operator group, as a string join should be. Nothing
here is inferred from ordering: every address came out of the runtime table at the index the script
declaration names.

## The same table, read statically: 233 slots the image writes itself

U7's option 2 -- follow the `mov [slot], reg` sites -- is measured now, and it needs no boot
(`tools/gnatives-writes.py`, `make gnatives-writes`, artifact `inventory/gnatives_write_sites.csv`, `--check` in
`make ci`):

| Measurement | Value |
|---|---|
| store sites into `GNatives` in the image | **233**, each writing a different slot |
| slots they cover | 233 of the runtime table's 356, every one of them inside it (0 static-only) |
| of those 233, store the address the runtime dump holds at that slot | **233 (100%)** |
| runtime slots with no static store | **123** |
| slot range covered | 0 to 543, not one contiguous block |
| store sites inside a symbolised function | 2 of 233 |

All 233 share one form -- `mov [disp32], ecx` (`89 0d`) -- and one shape: the value is loaded by the instruction
that *ends* at the store (`mov ecx, [0x005c2xxx]`), a pointer table in `.data`, and then stored into
`GNatives[slot]`. Resolving that second hop through `Xbe.read_va` is what makes the values comparable, and they
agree exactly: the same numbers, one read from the file and one from guest memory.

Two things the numbers settle. **The static route validates the runtime dump over two thirds of itself and cannot
complete it:** 233 of 356 slots are visible, all 233 right, and the 123 that are missing are registered by a fill
through `mov [ecx+eax*4], edx`, whose modrm byte encodes no absolute address -- no store-instruction scan can see
them, whatever it is written to look for. And **the stores live outside the function map**: 2 of 233 sites fall
inside a symbolised function, because they are compiler-generated setters across `0x0040cb1e`-`0x00413ab2`, one per
slot. Each setter writes its slot twice -- the value into `GNatives[slot]`, and **the slot index itself** into one
dword at `0x006353e8` (`c7 05 e8 53 63 00 <index>`). **All 233 annotate the slot, 231 of them as an immediate**
(slots 2 through 543 are all there) and the remaining two -- slots 0 and 1 -- through a register
(`89 15 e8 53 63 00`), where the setters' other branch writes the *body* instead. So the index never had to be
inferred from the displacement a store targets: the image spells it out, and the two agree in **231 of 231** cases.
`0x006353e8` is not a mirror of the table: the region it starts takes **614 stores over 9 offsets**, of which offset 0
is these 233 annotations and **307 at `0x00635560`** (mostly from functions no symbol names) are some other structure.
The artifact reports the annotation as a `slot_literal` column and `--check` refuses a row whose literal disagrees
with its slot.

## The slots nobody declares: 66, 67 and 68 are token bodies

`GNatives` is indexed by the front end's one-byte token as well as by `native(index)` number, and the live table shows
the split: of the 356 registered slots, **56 sit below `0x60`**, **16 fill the `0x60`-`0x6F` band** as 48-byte
trampolines (`0x000464f0`-`0x000467c0`, one per slot), and **284 are at `0x70` and above**, where the first declared
index is 112 (`execConcat_StrStr`, `Core:Object`). Three of the low band -- **slots 66, 67 and 68** -- are the only
store-written entries nothing declares: no package names index 66, 67 or 68, and `inventory/gnatives_records.csv`
finds no `{name, body}` record for them, so neither the script side nor the record side can name them. Their
registration is ordinary and not different in any way: setters at `0x0040cd02`, `0x0040d6c2` and `0x0040d722` store
`0x000404b0`, `0x000409a0` and `0x00040a30` and annotate them with the literals 66, 67 and 68 at `0x0040ccec`,
`0x0040d6ac` and `0x0040d70c`.

**The numbering is the stock one, fixed by three bodies whose bytes admit no other reading:**

| slot | body | how it reads | stock token at that number |
|---|---|---|---|
| 11 | `0x00040690` | `c2 08 00`: `ret 8`, never touching `FFrame::Code` | `EX_Nothing` `0x0B` |
| 29 | `0x00040a60` | consumes one dword from `FFrame::Code` (+0x0c) into `Result` | `EX_IntConst` `0x1D` |
| 30 | `0x00040a80` | consumes one dword through `movss` into `Result` | `EX_FloatConst` `0x1E` |
| 96-111 | `0x000464f0`-`0x000467c0` | 48 bytes each: push a `.data` dword, read a byte, jump through the table | the `EX_ExtendedNative` band |

With the low band in the stock `EExprToken` numbering, **66, 67 and 68 are tokens `0x42`, `0x43` and `0x44`**, and
each body is read here rather than inferred:

**66 (`0x000404b0`, 92 bytes).** Reads a dword from `FFrame::Code` and compares it with `0x64`. Unequal: it steps
`Code` **back one byte** and returns *without writing a result*. Equal: it consumes that dword, a second, a third and
one byte -- 13 bytes -- and hands them to the first virtual of the global object at `0x005fe148`
(`mov ecx,[0x5fe148] / mov edx,[ecx] / call [edx]`) as `(Context, Stack, Result, x, y)`. That global is empty in the
file and filled at runtime. Two bodies outside the table (`0x0040510`, `0x0040590`) evaluate one expression and then,
**if the next byte in the code stream is `0x42`**, consume it and tail-call `GNatives[66]` with a null result, taking
the expression's value out of one dword at `0x005ff178`. So token `0x42` is emitted *after* an expression, answers
nothing itself, and is the only token whose handler is also reached by fixed slot from another exec.

**67 (`0x000409a0`, 131 bytes).** Reads two dwords, treats the first as a pointer and reaches `Context + [first+0x48]`
for an 8-byte pair; when the object in that pair is non-null it calls `[vtable+0x30]` and clears the pair if that
answers non-zero, otherwise it calls `0x00062ff0` and re-enters through **`[vtable+0x54]`** -- the `ProcessEvent`
dispatch slot measured in 4.3 -- with `(Stack, Result, ...)`. It resolves a member/interface operand and forwards a
call.

**68 (`0x00040a30`, 35 bytes).** Reads one dword `v` and writes an **8-byte** result, `{ (v ? Context : NULL), v }` --
the only 8-byte result in its neighbourhood, where the const bodies that follow (29, 30) write four bytes each.

None of the three is an `Object` slot: the 230 that are were matched to `Object`'s live slots by body, and these have
no declarer at all. `symbols/xbe_dispatch.csv` therefore keeps them as `Native_66`, `Native_67`, `Native_68` with the
behaviour above recorded instead of a name guessed from their operands. What is left is a front-end question: nothing
in a shipped package references indices below 112, so naming them needs a dump taken while a mission's scripts run.

## The other side of the blind spot: the record that carries the body

A slot with no store site is not a slot without evidence. Every native body also sits in a `{pool name, body}`
record in the initialised part of `.data` -- 1,104 records over 203 per-class blocks, 883 distinct names
(`tools/native-table.py`, `inventory/native_targets.csv`) -- so pairing the runtime table's values with those
records changes the question from "which instruction writes this slot" to "which record carries this body"
(`tools/gnatives-records.py`, `make gnatives-records`, `inventory/gnatives_records.csv`, `--check` in `make ci`):

| Measurement | Value |
|---|---|
| storeless slots that are a record's body | **113 of the 123** |
| slots named by neither a store nor a record | **10** |
| stored slots that are also recorded | 230 of 233 (the record and the store name the same body) |
| records whose body more than one slot holds | 0 |

A record is `{name pointer, body}` and the body has to land in `.text`, so a live slot whose value is a record's
body was filled from that record. Those 113 are written by a walk over the record block, not by a per-slot store,
which is exactly why a store-instruction scan cannot see them -- the instrument's blind spot, not missing data.

**The 10 that remain** are slots 302, 333, 384, 385, 501, 503, 509, 511, 528 and 561. Each value does appear as a
dword in `.data`, but the dword beside it is not a binder-pool name (`0x00000000`, the float `0x3f000000`, or
another `.text` address), so those 8-byte pairs are not `{name, body}` records and this pass cannot name them. Nine
of the ten sit in two neighbourhoods (`0x00104840`-`0x00112980` and `0x0020cb70`-`0x0020cbf0`), which is where a
second registration shape would live if the build has one.

## The third shape: the stub that pushes its own slot number

The record pass left ten slots unnamed and inferred a mechanism from the `.data` it could see. The mechanism is
visible in `.text`, and it covers **all 123** storeless slots, not 113 of them (`tools/gnatives-stubs.py`, `make
gnatives-stubs`, `inventory/gnatives_stubs.csv`, `--check` in `make ci`). Every one of them has its own registration
stub:

    0x004145a0  68 98 2d 5c 00   push 0x005c2d98     ; &<the dword in .data that carries the body>
    0x004145a5  68 10 02 00 00   push 0x00000210     ; the slot index, as a literal: 528
    0x004145aa  e8 b1 26 c3 ff   call 0x00046c60     ; the registrar
    0x004145af  83 c4 08         add  esp, 8
    0x004145b2  a2 58 ae 7a 00   mov  [0x007aae58], al

| Measurement | Value |
|---|---|
| stubs of that shape in `.text` | **127** candidates, **123** after three semantic rules |
| candidates rejected | 4, each pushing `0x006c2c50` where a slot number goes (an address, not an index) |
| slots the 123 name | one each, **none** shared with a store site |
| stubs that store what the guest holds at that slot | **123 (100%)** |
| store sites (233) + stubs (123) | **356 of 356** live slots, none left |
| stubs of the same shape that push `-1` instead | **467**, and none of their 467 bodies is a live slot value |

**The registrar is read too, because that is where the write happens** (`0x0046c60`): it fills all 4,096 slots with
the startup sentinel `0x000404a0` once (`mov dword [0x635560], 1` guards the `rep stosd`), returns **without
storing** when the pushed index is `-1`, and otherwise runs `mov edx, [ecx]` / `mov [eax*4+0x6313e8], edx` with
`ecx` the pushed `&body`. That scaled store is exactly the fill `tools/gnatives-writes.py` cannot see by
construction -- and the index is not lost at all, it is the stub's second `push` immediate. The 467 `-1` stubs are
the other half of the same file: the registrar discards them, their bodies (467 distinct `.text` addresses) are in no
live slot, and those natives arrive through the name path instead (`tools/native-binder.py`).

**The ten are ordinary members of that set.** 302, 333, 384, 385, 501, 503, 509, 511, 528 and 561 are registered by
the stubs at `0x00417850`, `0x004186b0`, `0x00417810`, `0x00417830`, `0x004145e0`, `0x00414600`, `0x00414620`,
`0x004145c0`, `0x004145a0` and `0x00414640`, each carrying its index. So the correction to the section above is about
the **mechanism**, not the count: the 123 are registered one stub per entry, not by a walk over the record block --
which is why the record pass could only reach the 113 whose bodies a record also carries, and why the ten it could
not reach were never a different shape in `.data`. Their bodies are pushed as addresses of dwords in a bodies-only
`.data` array (`0x005c2d98`, `0x005c43ac`, `0x005c5bd0`), and no `{name, body}` record sits near those.

One caveat stays open: an index names the slot, not the function. The ten have no script declarer in any package
(the 83 engine-internal execs of U7), so their names stay `Native_<index>` until a dump after a mission load
registers the classes the front end does not touch.

## The name side: 467 stubs that push `-1`

The registrar's `-1` branch is not dead code. The same stub shape appears **467** more times in `.text` with `6a ff`
(`push -1`) where the index goes, and all 467 reach the same registrar (`tools/gnatives-name-stubs.py`, `make
gnatives-name-stubs`, `inventory/gnatives_name_stubs.csv`, `--check` in `make ci`):

| Measurement | Value |
|---|---|
| stubs that push `-1` | **467**, each pushing a different `.data` dword |
| bodies they push | 467 distinct `.text` addresses, **none** of them a live slot value |
| bodies also registered by an indexed stub | **0** -- the two stub sets are disjoint |
| bodies that carry a `{pool name, body}` record | **467 of 467**, and the 467 names are distinct |
| bodies whose record matches an index-less declaration | **467 of the 523** |
| blocks the stubs were generated in | 34, the largest 56 stubs, 8 of one stub |

So 467 of `native_targets.csv`'s 1,104 records are the **name side** of these stubs -- one record per stub, no name
twice -- and the registrar discards the call (`cmp eax,-1; jz ret`), which is why none of the 467 bodies is in the
table at the main menu. Those natives are the ones resolved by name later, the path `tools/native-binder.py` reads
from the other side. The 123 indexed stubs and these 467 share **no** body: the two halves of the stub file are
registration by slot and registration by name.

The 56 index-less declarations with no `-1` stub are **41 `Object`**, 13 `BrushBuilder` (the editor-only brush
class), 1 `MaterialFactory` and 1 `UPreviewSkinnedButton`. The tool prints that composition rather than folding the
remainder into the total.

### The 56 without a stub: three channels, and two packages the build never loads

That remainder is measured too (`tools/native-channels.py`, `make native-channels`, `inventory/native_channels.csv`,
`--check` in `make ci`): one row per index-less declaration, joining the `-1` stub, the `{name, body}` record and the
class's store-registered slots, and asking the image itself whether the binder literal exists. All 523 resolve, and
none of them is missing from the registration file:

| Channel | Declarations | What carries them |
|---|---|---|
| stub + record (a `-1` stub) | **467** | the stub, each with its own `{name, body}` record |
| record only | **41**, all `Object` | the `{name, body}` record; no stub |
| neither | **15** | nothing: `Editor` (13 `BrushBuilder` + 1 `MaterialFactory`) and `UPreview` (1) |
| rows whose binder literal the image carries | **508 of 508** named rows | the name is in the pool for every declaration that has one |

The 41 are not a hole, and `Object` is why. All **230** of its live `GNatives` slots are **store-registered**
(`mov [slot], reg`), **0** of its 160 indexed natives has an indexed stub, and exactly **one** of its 42 index-less
declarations (`GetReferencers`) has a `-1` stub. `Object` is the class the build registers through the store table,
so its index-less half carrying no stub is the same fact rather than a second one; what binds those 41 is the
record, read by the `int%sexec%s` lookup at `0x0003a030` (`tools/native-binder.py`, U1).

The 15 have nothing to bind. `BrushBuilder` (13 declarations), `MaterialFactory` (1) and `UPreviewSkinnedButton` (1)
occur **0** times in the image -- not as a class name, not as a function name, not as an
`int<prefix><Class>exec<Function>` literal -- and `Editor` and `UPreview` are the **only two packages with 0 of
their declarations bound by name** (0 of 14 and 0 of 2; every other package is fully bound). The word `Editor` does
appear five times, and none of them is a class prefix: a property metatype label, two log messages
(`Initialising Karma In Editor.`, `LIPSincShutdownEditor`), never `intUEditorexec…`. Both packages are listed in
`System/Default.ini` under `EditPackages` and in neither runtime list, so their editor-side natives are declared in
packages the shipped build never loads: nothing to register, so nothing registers.

**One instrument fed the other's blind spot.** `tools/native-reconcile.py`'s `load_binder` filled an empty binder
with `"int%s%s" % (prefix_expected, function)` -- a literal the image does not carry -- and the residual table then
read its own fabrication as evidence. That is why `inventory/native_residual.csv` listed `UPreview`'s two records as
`text-missing` (`intUCreated`, `intUSetRenderInfo`); with the fill removed they read `record-unbound`, which is what
the tool's own self-test already said they must ("a record with no binder must not read as text-missing"),
and what `native_channels.csv` measures per declaration.

## Correction: the table committed before 2026-10-23 was the CRT's initialiser array

An earlier pass reported `GNatives` at `0x00598e00` with 994 entries and named 270 functions from it.
That array is the CRT's static-initialiser list. The evidence that killed it:

| Test | `0x00598e00` | What a dispatch table must look like |
|---|---|---|
| Order of entries | strictly increasing: 990 of 990 deltas positive, spacing 32 (635×), 96 (266×), 48 (74×), 16 (8×) | ordered by declaration, so arbitrary against code addresses |
| Span | 991 entries inside 0x2cb4a of `.text` — one contiguous block of compiler-generated stubs | natives are spread across the whole engine's code |
| The single xref (`0x00343762`) | `_initterm`: walks `[0x598e00,0x598e08)` and `[0x598e0c,0x599db0)`, skipping null and `0xffffffff`, calling each with no arguments — never indexes | one indexed access, `call [table+reg*4]` |
| The code it points at | an initialiser prologue: `mov ecx,[0x635560] … rep stos`, e.g. one stub fills 4,096 dwords at `0x6313e8` — GNatives itself — with a default pointer | distinct native implementations; operator natives are a few instructions each |
| Duplicate prologues | indices 119, 130 and 146 (`NotEqual_ObjectObject`, `AndAnd_BoolBool`, `Add_IntInt`) begin with byte-identical code | operators cannot share a body |

The old filter ("entry 0 null, starts a section, all entries in `.text`, exactly one code xref") is
satisfied by any initialiser array, and its cross-check — "270 of 274 declared indices resolve to a
distinct slot" — was vacuous: any table of a thousand entries resolves any index. `tools/gnatives.py`
now scores monotonicity and the xref's *kind* (indexed vs plain address load) and rejects the array
explicitly (`inventory/gnatives_candidates.csv`, verdict `rejected:monotonic`).

Removed with it: `inventory/gnatives_map.csv` (deleted), the 270 `exec*` names and 721 fabricated
`Native_*` functions in `symbols/xbox_functions.csv`, and the naming rules that assumed a static slot
list. What replaced them is the runtime map above: same idea (slot N is the function the script calls
by index N), but the address now comes from the table the engine actually dispatches through.

## Script readability (unaffected by the correction)

UELib resolves native calls through `UnrealPackage.NTLPackage`, which this dump does not ship, so
engine calls decompiled to `__NFUN_<n>__`. `tools/Sg1Tool` rebuilds that *compiler-side* table from
Core (operators) + Engine + the package being decompiled — a different thing from the XBE's dispatch
table — taking unresolved sites 51,289 → 4,230 → 1,470 → **0** across 2,319 classes.
`docs/DECOMPILER_GAPS.md` stays empty as the regression check.

## The body of a script function (4.3, second half)

Nothing in the file says where the interpreter is, because the interpreter is never called: a script
function is executed by taking a `UFunction`'s function pointer. That pointer is what the VM fills in,
so the way in is the code that fills it.

### The binder at `0x0003a030` (`Core\Src\UnClass.cpp`)

This is the routine the earlier notes called "the interpreter" because the GNatives asserts are inside
it. It does not execute bytecode; it decides what each `UFunction::Func` points at:

```
if ((FunctionFlags & 0x400) == 0) {          // not FUNC_Native: a script body
    assert "iNative==0"                      // UnClass.cpp:0x6a6
    Func = 0x00048bf0
} else if (iNative == 0) {                    // FUNC_Native without an index
    name = FString::Printf("int%sexec%s", ...)
    if (lookup(name) != NULL) Func = *lookup(name)
} else {
    assert "iNative<EX_Max"                  // 0x6ac
    assert "GNatives[iNative]!=0"            // 0x6ad
    Func = GNatives[iNative]
}
```

The three asserts are the ones the table was found from, so the earlier reading of this routine was
right about the index space and wrong about the routine's job. Fields it touches, and therefore the
measured layout of a `UFunction` here:

| offset | field | evidence in the body |
|---|---|---|
| `+0x80` | `FunctionFlags` (dword) | `& 0x400` selects the native branch |
| `+0x84` | `iNative` (word) | compared with `0` and with `0x1000` (`EX_Max`) |
| `+0x90` | `Func` (code pointer) | receives `GNatives[iNative]`, `0x00048bf0`, or the lookup result |

**This is also the answer to half of U1.** The 523 `native()` declarations that serialise no index are
not dispatched through `GNatives` at all: for a `FUNC_Native` function with `iNative == 0` the binder
resolves an implementation *by name* at bind time (`"int%sexec%s"`) and stores the result. They are
still unreachable from the index table, but they are no longer unexplained.

#### What the two `%s` hold, and the pool they are looked up in (measured 2026-11-08)

The binder's name is not built from a map or a PDB: the names are **in the image**, as wide literals packed into one
440.8 KB span of the wide-string data (median 30 characters apart, so one blob rather than scattered literals).
`tools/native-binder.py` (`make native-binder`, `inventory/native_binder_names.csv`) extracts them and joins them with
`inventory/natives.csv`:

```
intAActorexecKWake..intAActorexecKIsAwake..intUGUIControllerexecGetMapList
```

So the second `%s` is the function's own name, the first is a one-letter C++ type prefix **followed by the class name**,
and the literal `exec` sits between them whatever the function's `exec` flag says -- `Actor.KWake` is not `exec`. The
prefix is `A` exactly when the class descends from `Actor`, and that holds in **769 of 769** joined rows against
`inventory/classes.csv`, which is what makes the join a measurement and not a convention.

| package | declarations | with a binder name |
|---|---:|---:|
| Engine | 444 | 444 |
| Core | 203 | 191 |
| GUI | 81 | 81 |
| IpDrv | 32 | 32 |
| StargateGame | 17 | 17 |
| StargateVehicles | 4 | 4 |
| Editor | 14 | 0 |
| UPreview | 2 | 0 |
| **total** | **797** | **769** |

**So the lookup succeeds in a shipped build** -- U1's last open question -- for every declaration the game's own modules
make, including all 17 `StargateGame` and all 4 `StargateVehicles` natives, and for **508 of the 523** index-less ones.
The 15 index-less names it cannot resolve are `Editor.BrushBuilder` (13), `Editor.MaterialFactory` (1) and
`UPreview.UPreviewSkinnedButton` (1): packages that ship as script in the dump but whose C++ side is not in the Xbox
image, so no name was ever baked in. That is a fact about the image rather than about the packages, and it is why an
editor class binds to nothing at load.

The pool also carries 114 names no declaration in the 18 packages matches, all of them `UObject`: `Iterator`,
`HighNative0`..`HighNative15` (16, the VM's own boundary entries) and the operators under the engine's own spelling. The
twelve declarations the join misses that *do* carry an index are all `Core.Object` string operators spelled `_StrStr` in
script -- `Concat_StrStr`, `AtEqual_StrStr`, ... -- and every one of them has a `_StringString` twin in the pool
(`intUObjectexecConcat_StringString`), so those twelve are not missing at all: they dispatch through `GNatives` on their
own index and the binder never forms a name for them.

#### The registration table: 883 names, 1,104 records, and the body each one binds (measured 2026-11-09)

The binder looks a name up, so something has to hold the map. It is not a code xref to the literals: none of the
pool's addresses appears in `.text` as an immediate. The reference is a **table in `.data`** — one 8-byte record per
entry, `{pointer into the pool, pointer into .text}`.

`tools/native-table.py` (`make native-table`, `inventory/native_targets.csv`) finds it: **1,104 records over 203
contiguous runs** between `0x00599e08` and `0x005c6740` (87 of the runs hold a single record, the largest holds
306), covering **883 distinct names** — every name the pool carries. 23 names are registered more than once (up to
12 records, in separate runs 0x100 apart), and the duplicates always name the same body, so a name resolves to
exactly one address.

| measurement | value |
|---|---|
| records / contiguous runs | 1,104 / 203 |
| distinct names (and names in the pool) | 883 / 883 |
| declarations with a body address | **769 of 797** (508 of the 523 index-less, 261 of the 274 indexed) |
| agree with `inventory/gnatives_slots.csv` (runtime dump) | **261 of 261, 0 disagreements** |
| bodies that are entries in `inventory/function_modules.csv` | 829 (and **none** is an interior address) |
| bodies the function map has no function at | **275** |

The cross-check is what makes this a measurement rather than a pattern match: the 261 agreements are against the
table the *running build* installed (dumped over the xemu GDB stub before any of this), so the static records and
the runtime dispatch table are the same map, entry for entry. An older, independent address corroborates it end to
end: U16 found `UGUIController::execGetMapList` at `0x002fe5f0` from a log-string xref and a GDB breakpoint on the
call site, and `intUGUIControllerexecGetMapList`'s record names `0x002fe5f0`.

The 28 declarations without a body are exactly the three groups already accounted for: `Editor`'s 14 and
`UPreview`'s 2 (their C++ side is not in the Xbox image, U1) and `Core.Object`'s 12 operator declarations spelled
`_StrStr`, which dispatch on their own index and never form a name.

**The 275 bodies the seeder has no function at are new entry points.** None falls inside a function the map already
has, so the records name a start address the analysis is missing, with the callee's own name attached. That is the
cheapest next pass for the function coverage in PLAN 4.1: 275 `{name, address}` pairs that were not guessed from a
string or a prologue.

The two save-path natives U5 is waiting on are now locatable with no boot: `intAGameInfoexecSavePackage` is
`0x00298bb0` and `intAGameInfoexecLoadSaveGame` is `0x00149370`.

**A correction to the earlier `binder_va` column.** The first version of `tools/native-binder.py` decoded the whole
image as UTF-16LE and multiplied the character index by two. A surrogate pair collapses two code units into one
character, so every index after the first pair drifts: **all 769** non-empty addresses in
`inventory/native_binder_names.csv` were wrong — 752 pointed exactly **2,970 bytes (`0xb9a`) short** of their own
name and 17 landed outside every file-backed section entirely, so **0** of them sat on the string. The fix reads the
image as one character per 16-bit unit (lone surrogates are legal in a `str` and never match the binder pattern),
and all 769 now land exactly on their name. That is why no reference to a pool address had ever been found in the
image: the addresses being searched for were not the ones in the file.

### `UObject::ProcessInternal` at `0x00048bf0`

The address every script `UFunction::Func` is set to. Its body is the interpreter, and it says so
itself:

```
cVar1 = **(char **)(frame + 0xc);          // FFrame::Code
while (cVar1 != '\x04') {                   // 0x04 = EX_Return
    bVar2 = **(byte **)(frame + 0xc);        // opcode
    *(byte **)(frame + 0xc) += 1;            // Code++
    GNatives[bVar2](frame, result);          // dispatch through the same table
    cVar1 = **(char **)(frame + 0xc);
}
```

Its own strings: `"Infinite script recursion (%i calls) detected"` (guard 250, `0xfa`) and
`"%sNot executing, due to RPC, or nonprobing function"`. Both are referenced from inside the function,
which is the strongest evidence available short of a symbol: a routine that walks `GNatives` with
`EX_Return` as its terminator, and reports recursive script calls, is the script executor.

**Correction to this document's earlier claim:** `FFrame::Code` is at `+0x0c`, not `+0x90`. The
`+0x84`/`+0x90` pair belongs to `UFunction` (`iNative`, `Func`); the binder's `this` is a `UFunction`,
not an `FFrame`. The exec bodies' operand reads at `[frame+0xc]` were already consistent with `Code`
at `+0x0c`.

### The body behind one slot: `GNatives[0x1f]` is `execStringConst`

The map answers *which* address sits in a slot. What a bytecode reader needs next is what that body consumes
from the code stream, and `tools/gnatives-body.py --slot N` reads it: the address and channel from the committed
registration evidence (`inventory/gnatives_slots.csv`, `gnatives_records.csv`, `gnatives_stubs.csv`,
`gnatives_write_sites.csv`), the extent from `symbols/xbox_functions.csv` (or the terminator rule when the map has no
size for it), then the body with direct calls named, `call [reg*4+0x6313e8]` rendered as a table dispatch, and every
immediate that lands inside a section marked. Slot `0x1f` is the smallest complete answer it gives:

```
slot 31 (0x1f): slot-only
  store       0x0040d962 -> 0x00047bf0
  .data record intUObjectexecStringConst at 0x005c1b70
  declarers   none -- an engine-internal exec
  body 0x00047bf0: 69 byte(s), extent from symbols/xbox_functions.csv, map name Native_31
  00047bf0  56              push esi
  00047bf1  8b 74 24 08     mov esi,[esp+0x8]          ; FFrame&
  00047bf5  8b 46 0c        mov eax,[esi+0xc]          ; FFrame::Code
  00047bf8  68 00 04 00 00  push 0x400
  00047bfd  6a 00           push 0x0
  00047bff  50              push eax
  00047c00  e8 3b 0b 01 00  call 0x00058740            ; bounded copy, cap 0x400
  00047c05  8b 4c 24 18     mov ecx,[esp+0x18]         ; RESULT_DECL
  00047c0c  50              push eax
  00047c0d  e8 ae 40 09 00  call 0x000dbcc0            ; FString::operator=(ANSICHAR*)
  00047c12  8b 4e 0c        mov ecx,[esi+0xc]
  00047c15  80 39 00        cmp byte [ecx],0
  00047c18  74 14           je 0x47c2e
  00047c20  8b 4e 0c        mov ecx,[esi+0xc]
  00047c23  41              inc ecx
  00047c26  89 4e 0c        mov [esi+0xc],ecx
  00047c29  80 3a 00        cmp byte [edx],0
  00047c2c  75 f2           jne 0x47c20
  00047c2e  ff 46 0c        inc dword [esi+0xc]
  00047c31  5e              pop esi
  00047c32  c2 08 00        ret 8
```

Three readings follow. `FFrame::Code` is the frame's `+0x0c`, the same offset the exec bodies read; the literal is
passed through a helper with a **fixed `0x400` cap** and only then assigned to the result `FString`; and the token's
length is discovered by **walking the bytes one at a time to the NUL**, then advancing one more. So
`EX_StringConst` carries **no count, no header and no operand to widen**, `ret 8` matches the `(FFrame&,
RESULT_DECL)` signature the binder installs at `0x0003a030`, and the 1,023-character limit is the helper's, not the
token's. `docs/UNKNOWNS.md` U13's four bytes therefore cannot be this token's payload -- `docs/BYTECODE.md` carries
what they are instead.

### `FFrame::Step` at `0x000398e0` (1,583 bytes)

Reached from the exec bodies, this is the expression/token decoder: it takes the next byte from the
code stream, switches on it (tokens up to `0x70`; anything above `0x6f` is run to the end of the
expression by looping on a virtual call at `[this+0xa0]` until it returns `0x16`), and reports
`"Bad expr token %02x"` otherwise. The name is an inference from UE2's own `FFrame::Step`; the
behaviour and the string are measured.

### `UObject::ProcessEvent` at `0x0004bf20` (1,390 bytes)

The routine that calls both of the above. Signature, from the frame at entry: `ProcessEvent(this,
UFunction* Function, void* Parms, void* Result)`, with `Function` at `[ebp+0x7c]`, `Parms` at
`[ebp+0x74]` and `Result` at `[ebp+0x78]`.

```
0x4c01a  cmp  word [ebx+0x84], 0        ; Function->iNative
0x4c029  je   0x4c040
0x4c031  push [ebp+0x78]                ; Result
0x4c032  push [ebp+0x74]                ; Parms
0x4c033  mov  ecx, edi                  ; this
0x4c035  call dword [ebx+0x90]          ; Function->Func
0x4c03b  jmp  0x4c2a0
0x4c040  mov  eax, [ebx+0x80]           ; Function->FunctionFlags
0x4c046  test ah, 4                     ; & 0x400 (FUNC_Native)
```

So an indexed native is invoked through `UFunction::Func` (`+0x90`) with the frame the caller already
has, and a script body goes down the path that builds its own frame:

```
0x4c0d2  mov dword [ebp+0x40], 0x4d9618 ; FFrame vtable
0x4c0d9  mov dword [ebp+0x44], ebx      ; Object
0x4c0dc  mov dword [ebp+0x4c], ecx      ; Code   = Function->Script
0x4c0df  mov dword [ebp+0x50], edi      ; Locals = zeroed parm buffer
0x4c10f  call 0x48bf0                   ; ProcessInternal(frame, Result)
```

Measured `FFrame` layout, from those stores (frame base `ebp+0x40`): `+0x00` vtable,
`+0x04` `Object`, `+0x08` `Node` (the `UFunction`), `+0x0c` `Code`, `+0x10` `Locals`, `+0x18` a flag
`ProcessEvent` sets to 1 before the call (which routine reads it is not established). The locals buffer is sized from
`UFunction+0x40` (`ParmsSize`) and zeroed before the call — the decompiled body shows
`puVar3 = *(undefined1 **)(param_4 + 0x40)` feeding the allocation.

### Why these four were invisible

None of them has a call site, and each is invisible for a different reason:

| routine | evidence it exists | why the analyser missed it |
|---|---|---|
| binder `0x0003a030` | assert strings | reached only from class binding, itself reached from a table |
| `ProcessInternal` `0x00048bf0` | its own strings | called only through `UFunction::Func` |
| `FFrame::Step` `0x000398e0` | `"Bad expr token %02x"` | called only from exec bodies through a vtable |
| `ProcessEvent` `0x0004bf20` | none of its own | **492 aligned pointers to it in `.rdata`, zero direct calls** |

`ProcessEvent` is the clear case: scanning every `E8` call in the image finds no target of `0x4bf20`
at all, while a scan of the data sections finds 492 aligned dwords pointing at it, inside the runs of
code pointers that make up the class tables (neighbouring entries are the same shared thunks across
hundreds of classes). The address is therefore dispatched, not called, which is a fork property worth
carrying into 4.8. It was found by scanning the executable for calls to `ProcessInternal`, not by a
name: its only caller in the whole image is `ProcessEvent` itself (two sites, `0x4c10f` and `0x4c23e`,
both inside the same function).

That is what the seeder's second source is for. `tools/ghidra/SeedGaps.java` now also counts, for
every 4-byte value in a block that holds no functions, how many separate values point at each address
in executable memory with no function, and seeds the addresses with at least 8 referrers. A single
pointer can be a switch target; a table that names the same address hundreds of times is naming an
entry point.

The current map has **14,957** functions: one more than the end of that series, because the resolved XBE
entry point (`0x0033F317`, `docs/XBE_LAYOUT.md`) is now seeded into the analysis (`XbeEntry`, 114 bytes).

| measurement | before seeding | after assert seeds | after data-table seeds | after block promotion | after gap decodes |
|---|---|---|---|---|---|
| functions | 14,220 | 14,280 | 14,912 | 14,944 | 14,956 |
| assert sites in no function | 199 | 104 | 102 | 50 | **5** |
| assert-attributed functions | 1,201 | 1,253 | 1,256 | 1,288 | **1,300** |
| `FUN_*` (unnamed) | 12,339 | 12,347 | 12,971 | 12,971 | **12,971** |

The last column is a third kind of seed, added after the first two ran out: for a site whose entry
cannot be reached, the seeder disassembles the gap before it, walks back to the head of the block the
site sits in and creates a function there. Those bodies are **not function entries**, and
`inventory/xbe_block_seeds.csv` lists every one of them (25 at present) so a decompiled body that
starts at one is not mistaken for a function (`docs/UNKNOWNS.md` U8).

A fourth source decodes the gap a leftover site sits in and accepts an entry only when the body
contains the site, the instruction over the site is a `PUSH` of the file string the asserts table
names, and the body ends at a terminator (`inventory/xbe_gap_seeds.csv`, `docs/UNKNOWNS.md` U8).

The `FUN_*` column's larger count is the expected cost of the data-table technique: the 629 entries named
by tables are real code but carry no name evidence, so they stay `FUN_*` — an honest unknown with a
body, which is worth more than an unanalysed gap.

### Why these three were invisible

The analyse-time seeding is the other half: `tools/ghidra/SeedGaps.java` creates a function at the
nearest entry after `0xCC` fill bytes before each assert site that has none, keeps it only when the
assert that justified it lands inside the body it produced (52 of 64 seeds), and removes the rest
before counting. See `docs/NATIVE.md` for the regenerated coverage tables.

## How `ProcessEvent` is dispatched (U8, first half closed)

`0x0004bf20` has no call site anywhere in the image. What points at it is data — 492 dwords in
`.rdata` — and those dwords are not scattered: each sits at **slot 21 of a table of code pointers**,
with all 21 slots before it also holding code. `tools/class-tables.py` reads that structure out of
the image (`inventory/xbe_class_tables.csv`, `inventory/xbe_class_table_slots.csv`):

| measurement | value |
|---|---|
| tables | 492 |
| span | `0x004c78c0` – `0x0053b168` — 462 KB of code pointers, packed |
| slots per table | 30 – 159 |
| slot holding `0x0004bf20` | **21** (`+0x54`) in **492 of 492** tables |
| any other address at `+0x54` | none |
| slots 0 / 1 / 2 | `0x00062650` / `0x00062660` / `0x00062670` in all 492 |
| slot 22 (`+0x58`) | `0x00064770` in all 492 |
| slot 3 | a different function in every table (`0x000170b0`, `0x000171f0`, `0x00017270`, …), as are slots 9–11 and 30+ |
| table start referenced from `.text` | 492 of 492 (two immediates each for 443 of them, three or more for 63) |

The answer to U8's first question: this fork dispatches `ProcessEvent` as a **virtual at a fixed
slot, byte offset `+0x54`**, through the table at `[object+0x00]` — the same offset in every copy.
The boundaries the earlier note called "not pinned" are simply `pointer - 0x54`, and every start is
named by at least one immediate in the code, which is what a class registration looks like.

The slot beside it is the by-name entry point: `0x00064770` takes a name, looks it up in the class's
function hash (`[class+0x98 + (name & 0xff)*4]`, chain `+0x30`, name compare at `+0x20`), rejects
anything without `[function+0x80] & 0x200`, reads `[function+0x8a]` as the parameter size, copies
the parameters and runs it. So the two scripting entry points sit next to each other: by function
pointer at `+0x54`, by name at `+0x58`.

What varies between the tables is what makes them per-class: the first three slots and about a
dozen others are the same address in all 492 (inherited implementations), while slot 3 is unique per
table. Slots 19 and 20 hold `0x00020f00` — a single `ret` — in 327 tables and real bodies in the
other 165; `docs/NATIVE_CLASSES.md` names 466 of the 492 tables from the build's own class
registrations, and shows that split is "overrides these two virtuals" (the actor-side hierarchy)
against "keeps the empty default", not abstract against concrete.

## Next

1. The rest of the `UObject`/`UStruct`/`FFrame` layout, now that `ProcessInternal` and `ProcessEvent`
   give worked examples of how each field is reached. The property side is measured in
   `docs/NATIVE_PROPERTIES.md`; what remains there is the tagged tag record's field order (U10).
2. Name the 26 remaining class tables and identify the two virtuals at slots 19/20 — the naming
   chain and the size cross-check are in `docs/NATIVE_CLASSES.md`, the residuals in `UNKNOWNS.md` U8.
3. A second `GNatives` dump after a mission load, to catch the classes the front end does not touch
   (blocked on input automation, `docs/XEMU.md`).
4. Signature libraries for the CRT/XAPI/D3D8 majority (4.2) — blocked on signature data that this
   repository may not carry (`docs/UNKNOWNS.md` U9).

## What a class table does not carry: a script override

`tools/class-overrides.py` diffs every class table against its parent's and commits the result
(`inventory/class_virtual_overrides.csv`, `make class-overrides` with the dump set): of the 492 tables, 466 are named,
**302 pairs** resolve through `inventory/classes.csv`, and **200 classes override 658 slots** between them while 102
override nothing but slot 3; 39 classes *add* 185 slots their parent does not have. Slot 3 is reported once per pair as
`class-slot` because it holds a different function in every table and is never an override. The busiest slots are 30
(62 classes), 31 (41), 18 and 42 (33 each); `Serialize` (slot 11) is overridden by four classes.

The negative is the useful half: **a script override never reaches the table.** `Engine.Console` (`0x0050cb80`) and
`Engine.Interaction` (`0x0050c880`) are both 36 slots and agree on every index except slot 3 -- although `Console.uc`
re-declares five of Interaction's functions (`Initialized`, `KeyType`, `KeyEvent`, `Message`, `NotifyLevelChange`) and
carries 23 functions of its own. The slots a class adds are its *native* defaults: Interaction's
six (30-35) are one real body at `0x0014f790` and five two-instruction stubs whose `ret` immediates match the parameter
sizes of `KeyType` (`33 c0 c2 08 00`), `KeyEvent` (`33 c0 c2 0c 00`), `PreRender`, `PostRender` and `Tick` (`c2 04 00`),
and every subclass inherits those same five addresses. So a table answers "does this class replace a *native* virtual",
never "does it override a script function": read the script text for that
(`tools/pkg-script-text.py <dump>/System --tree --write $WORK/scripts`, 2,319 classes in under a second).

## `Serialize`: slot 11 of every class table, and the struct's own reader

PLAN 3.7 has been blocked on where a function's bytecode lives in its export, and the two previous attempts tried to
derive it from the payload bytes (UELib's reader order and a content anchor, both in `docs/UNKNOWNS.md` U13). The
third route works: **read the serializer out of the image's class tables.**

The 492 per-class tables in `.rdata` are vtables — a class's slots are mostly its parent's, and the ones that differ
are the virtuals it overrides. **Slot 11 is `Serialize`**, which the decompiled bodies confirm:

| class | slot 11 |
|---|---|
| `Const` | `0x00039270` |
| `Struct` | **`0x000392b0`** (two call sites only: `0x394c9` and `0x39f1a`) |
| `State` | `0x000394c0` |
| `Function` | `0x00039f10` |
| `Class` | `0x0003a800` |
| `LinkerLoad` / `LinkerSave` | `0x000515d0` (not a function start — see U13) / `0x00050050` |

`make serialize-slots` writes all 492 rows (`inventory/class_serialize_slots.csv`): **82 distinct `Serialize` entry
points**, each one a class that overrides the parent's. `Struct`'s is the one 3.7 needed.

**What `UStruct::Serialize` (`0x000392b0`) actually does**, from the decompiled body (under `$WORK`, never committed):

* calls its base first (`FUN_00038dc0`), then serialises `Super` and walks `Children`;
* serialises `FriendlyName` and **asserts it is not `NAME_None`** (`UnClass.cpp:0x2e2`);
* serialises two integers — the source line and text position;
* serialises the **script as a size plus that many bytes**, and on save appends a **`'G'`** marker when absent;
* then **walks the stored script token by token** (a virtual call at vtable `+0xa0` per token) and ends with
  `"Script serialization mismatch: Got %i, expected %i"` if the walk did not consume exactly the stored size.

That last line is the useful one for 3.7: the engine itself validates a script by walking tokens, so the token table
that the disassembler needs is the same dispatch the interpreter uses, and the size field is an *assertion* the walk
can be checked against rather than a guess. The `'G'` marker also explains why some payloads may not decode as
bytecode directly: a script can be stored compressed.

### The `+0xa0` slot is `0x000398e0`, and it is inherited more than overridden

`UStruct::Serialize` calls a virtual at `+0xa0` while it rebuilds the script buffer, so that slot is the token walker
the disassembler has to agree with. Reading it from every table (bounded by each table's own declared slot count —
reading past the end lands in whatever `.rdata` holds next, which is how the first attempt reported "walkers" at
`0x0d` and `0x31`):

* **327 of 492** classes have a slot `0xa0` at all; the rest have shorter tables;
* `Struct`, `Function` and `Class` all point at **`0x000398e0`** (1,373 bytes, attributed to `UnClass.cpp`) — one
  walker for the whole struct hierarchy;
* 134 distinct targets overall, and the distribution is lopsided: 117 classes share `0x00020f10`, 61 share
  `0x000171c0` (which is also the deleting destructor's target on some tables), 39 share `0x002f8880`. Those look
  like *inherited* engine implementations rather than per-class overrides.

`inventory/class_serialize_slots.csv` now carries `serialize` **and** `script_walk` (with the symbol each resolves to),
so "which function should the disassembler model?" has a per-class answer in a committed artifact.

### The walker's dispatch: 31 handler groups over 72 opcodes, and range checks above them

Decompiling the walker (`0x000398e0`) shows how a script is skipped:

1. read **one byte** — the opcode — and advance the cursor (`[esi]`), which indexes the object's script buffer at
   **`+0x48`** with its length at **`+0x4c`**, the same two fields the interpreter uses;
2. `if (0x6f < op)` and `if (0x5f < op)`: two range checks, each running a **nested walk** until the nested walk
   returns **`0x16`** (`EX_EndFunctionParms`);
3. `if (0x47 < op)`: the group that logs `"Bad expr token %02x"`;
4. otherwise the opcode indexes a **72-byte table at `0x39ec0`** whose values name one of **31 handler groups**, and
   the group indexes the jump table at **`0x39e44`**. The two tables are adjacent (`0x39e44 + 31*4 == 0x39ec0`).

**Corrected in the bytecode-table PR:** the jump table's 31 entries are handler *groups*, not one per opcode, and the
byte map is what turns the 72 opcodes `0x00..0x47` into them. Reading the jump table as if it were opcode-indexed
gives the right *count* and the wrong *index* — `0x47` is group 5, and group 30 alone covers eleven opcodes that the
walker rejects. Entry `0x1f` of the jump table is not code either: `.rdata` continues with `0xcccccccc` and ASCII,
which is why reading it as `0x100` entries produces plausible-looking garbage.

`make opcode-table` writes `inventory/opcode_table.csv` — 256 rows of opcode, group, handler address, operand
pattern and fixed byte count — and supersedes `inventory/opcode_dispatch.csv`, which listed group indices in an
`opcode` column. The 31 operand patterns are read from the handler bodies, and the terminators, the range rules and
the cross-check against the pinned UELib token map are in **`docs/BYTECODE.md`**.

Two primitives the same run identified, because the widths turned out to belong to the archive:

* **`0x00037110`** is `operator<<(int32)`: it bumps a clock, calls a callback if the low 16 bits of it are non-zero,
  then serialises **exactly 4 bytes** through archive vtable **`+4`**. So integers in a payload are raw little-endian
  32-bit — no compact encoding.
* **`0x00038dc0`** is `UField::Serialize`: it calls `UObject::Serialize`, then serialises `Next` (at `+0x28`) through
  archive vtable **`+0x18`** — the *object-reference* method, not a property block's compact index, which is why the
  payload-layout attempt below failed.

### Why the payload-layout attempts failed, now that the reader is known

The third attempt at finding the script inside a `UFunction` payload used the field order read out of
`UStruct::Serialize` — `Super`, `Children`, `FriendlyName`, line, text position, size, script — and tried compact
indices for the first three. **It validates for 0 of 3,931 `Engine.u` functions** (and 1 false positive in
`StargateGame.u`), which is consistent with the reader: `Super` and `Children` are serialised through the *archive's*
object-reference method (vtable `+0x18`) and `FriendlyName` through its name method (`+0x1c`), and those are not the
same encoding as a property block's compact index. The field *order* is right; the field *widths* belong to the
archive, and the archive's own primitives (`FUN_000370e0`, `FUN_00037110`) are what to read next.

Not measured yet: the walker's own dispatch (its jump table is the opcode table 3.7 needs), and whether any shipped
function's script carries the `G` marker.
