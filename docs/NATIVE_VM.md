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

### The record's field widths are compact, and a 12-byte record is nine

`docs/NATIVE_VM.md` had the field *order* of a cooked class export from `UClass::Serialize` but not the *widths*,
and every walk built on it assumed a `TArray` count is a 32-bit dword and that the two array fields hold dwords.
Three writers say otherwise, read from the bodies under `$WORK/module-export/Core/` (never committed):

| writer | what it writes | consequence for the stream |
|---|---|---|
| `FUN_000637e0` | a count: a raw 4-byte integer (`0x00037110`) **only when both the loading and the saving flag are clear**, otherwise an `FCompactIndex` built byte by byte through `0x00037040` | in a cooked package -- a *saving* archive -- **every array count in the record is compact** |
| `FUN_0003d900` | the 12-byte-record array: the count, then per record one **object reference** (archive vtable `+0x18`) and two 4-byte dwords (`0x00037110`, `0x000370e0`) | a record is **9 bytes** when its reference is one byte long |
| `FUN_0003d160` | the two "dword" arrays: the count, then each element through archive vtable **`+0x1c`, the *name* method** -- the one `UStruct::Serialize` uses for `FriendlyName` | they are `TArray<FName>`, **one compact index per element** (`tools/archive-slots.py`), **not** `n x 4 bytes` |
| `FUN_000394c0` (`UState::Serialize`) | runs before `UClass`'s own fields: `0x000371d0` writes **8** bytes at `+0x80` and again at `+0x88`, `0x00037080` writes **2** at `+0x90`, `0x000370e0` writes **4** at `+0x94` | **22 bytes** the field table did not have |

**The stride-9 unit has an owner.** The residue's repeated 4-byte value at a stride of 9 (`docs/CLASS_DEFAULTS.md`,
69 of 540 candidates) is one streamed 12-byte record: a one-byte object reference followed by the record's two
dwords, the first of which repeats. The last run could only call that unit a candidate; it is the element of the
array `UClass::Serialize` writes at `+0x4d0`.

**With those widths the forward walk still does not fit, and the failure is measured.** `tools/class-record-fields.py`
(`make class-record-fields`, `inventory/class_record_fields.csv`, `--check` in `make ci`) walks the list from the
measured prefix's end -- 22 state bytes, five dwords, compact count plus 9-byte records, the config `FName`, two
compact counts of two-index names -- on all 2,209 classes whose block start is located:

| measure | value |
|---|---|
| the walk runs past the payload (a count or an index) | **1,038 of 2,209** (47.0%) |
| it ends exactly on the block start | **0** |
| bytes of the middle it explains | median **47**, p90 89, max 617 (the middle's median is 91) |
| bytes left before the block start | median **42**, p90 374; **52** rows overshoot it (to -281) |
| record-array count read | 0 on 712, 2 on 335, 22 on 48, 18 on 25, 54 on 19 |
| name arrays empty | 821 (`+0x4dc`) / 1,079 (`+0x4f4`) |

A wrong width still yields plausible small counts -- 0 and 2 cover 47% of the corpus -- so "ends exactly on the
block start" is the only test that counts, and it answers **zero**. The widths are now named rather than assumed,
which is what the last note asked for; the field *list* is still short of the record, so the 545 withheld blocks
are not relocated by this route either, and the per-class body between the end of these fields and the block
start is what remains.

### The stride-9 array is in the middle, and the leftover is not one grammar

Two readings walked the record from its own field table and one from the block backwards; all three refused. This one
stops asking for a field list and asks what the bytes between the prefix's end and the block start *are*.
`tools/class-record-middle.py` (`make class-record-middle`, `inventory/class_record_middle.csv`, `--check` in
`make ci`) measures three things on the 2,204 classes whose block start is located.

**The 12-byte record array is in the middle, found by its period rather than by its count.** The element
`FUN_0003d900` writes is one object reference plus two dwords -- 9 bytes in a cooked package -- so consecutive
identical records repeat one 4-byte field every 9 bytes. **536 classes (24.3%)** hold a run of at least three such
units: median 5 units, p90 14, and **269 units (2,421 bytes)** in `StargateGame.SGMenuInteraction`. In every one of
the 536 a second byte inside the unit varies while that dword repeats, which is the `[reference][dword][dword]`
split the writer names. The control is the same search over a window of the middle's length taken from the block
start: **13 classes (0.6%)**. So the run is the record array and not a permissive grammar -- but where it starts is a
distribution, not a constant: its first unit sits a median 128 bytes from the prefix's end, with modes at +78 (28
classes) and +84 (26). That is why the count field in front of it has not been found: the array can be located by
its period for a quarter of the corpus, and no single offset in the field table precedes it.

**The leftover after the documented walk is not one grammar.** Of the 1,166 classes whose walk stays inside the
payload, the window from the walk's end to the block start labels as **1,037 `mixed`** (median 42 bytes, commonest
size 20 on 190 classes), 47 `overshoot` (the walk ends past the block), 44 `dwords` (a whole number of dwords with a
repeat), 27 `records` (a whole number of 9-byte units), 7 `compact` (a chain of in-range compact indices) and 4
`zero`. The leftover's own mode, 20 bytes, is the width of the five-dword group, so "one more field in front of the
block" is the shape the corpus rules out: a field list that closed the walk would have to close it 20 bytes before
the block, not in front of it.

**The fit sweep refuses, and its own control says why.** If the middle were the record's own tail, some start offset
for the *same* field list would end exactly on the block start. Swept over offsets 0-40 with the five-dword group
where `UClass::Serialize` writes it, **80 classes (3.6%)** land -- against 46 (2.1%) for the same sweep aimed 8 bytes
past the block start. With the dword group moved after the two name arrays, 618 (28.0%) land against a control of 74
(3.4%) -- but **360 of those 618 are the 367 classes whose middle is exactly 88 bytes**, a size at which a short
window walks at many offsets (98% of that class fits somewhere); among middles of every other size the rate is 258
of 1,837 (14%). So the sweep measures how easily a short middle walks, not the field order, and it is recorded as a
negative: the ordering claim it appears to make does not survive a size-controlled reading.

### The run has no head: no count ends in front of it, in either encoding

The last note left one question open in the plainest terms -- "why the count field in front of it has not been
found" -- and the answer is that it is not in front of it. `tools/class-record-run.py` (`make class-record-run`,
`inventory/class_record_run.csv`, `--check` in `make ci`) takes the 536 classes with a stride-9 run and asks one
question about the run's head, with a null for every answer:

| measure | value |
|---|---|
| a compact count equal to the units, ending within 6 bytes in front of the run | **16 of 536** (3.0%); all one-byte indices, 11 of them the value 3, 1 of them adjacent |
| a raw 4-byte count in front of it (what a non-saving archive would write) | **0 of 536** |
| the same test at every other byte position of the same middles | **9,675 of 268,834** (3.6%) |
| a count equal to the *total* element count, at a lattice point 0..40 units back | 184 of 536 (34.3%), 168 of them further back than the run's own head |
| the unit carries 2 distinct bytes outside the repeating dword | 503 of 536 (3 on the other 33) |

**The head is at chance, not above it.** Sixteen hits against a background of 3.6% is 536 x 0.036 = 19 expected, so
the run's first byte is no more likely to be preceded by its own count than any other byte of the same window. The
41-point lattice sweep behind it does not rescue that either: with a 3.6% single-position rate, trying 41 positions
should succeed on about 78% of the classes even when no count exists, and it succeeds on 34.3% -- below chance. So
neither encoding of a `TArray` head is in front of the run, at its first unit or at any 9-byte lattice point up to
40 units back.

**Two readings survive, and they are the next measurement.** `FUN_0003d900` writes the record's reference through
the archive's object-reference method, whose compact index is **1 to 5 bytes**, so the array's earlier elements
need not be 9 bytes wide: the run can be the part of the array whose references happen to be one byte, and its head
then sits in front of a lattice of *variable* stride, which no 9-byte step count reaches. (The other reading is that
the period belongs to the values in the window rather than to an array head at all.) Nothing here says the run is
not the record array -- its 0.6% control still does -- only that its head is not where a fixed 9-byte step count
looks.

**What is either side of it, measured rather than called a field.** In front of the run: a median **131** bytes
(`mixed` on 499 of 536, `dwords` 19, `records` 2), against the field walk's own median 47 explained. Behind it: a
median **58**, with modes at 5 bytes (39 classes), 4 (38) and 49 (28) -- and on 5 classes the run ends exactly on
the block start. The dword that repeats is a single index-shaped byte in a zero dword on **431 of 536** (negative,
i.e. import-shaped, on 227; positive on 204; a multi-byte or non-index byte on 105), most often `01` (62 classes),
`83` (32) and `85` (30) -- so the period is mostly one small index value repeating at a 9-byte stride, not a hash
or a pointer.

### The walk back continues the record's own widths, and the count is still not at the end of it

The previous note refused a fixed 9-byte lattice behind the run and kept the reading the writer implies: each
record's reference is a compact index of 1 to 5 bytes, so the array's earlier elements need not be 9 wide and its
head sits in front of a *variable*-stride body. `tools/class-record-walkback.py` (`make class-record-walkback`,
`inventory/class_record_walkback.csv`, `--check` in `make ci`) walks exactly that: from the run's first element
boundary -- the byte the period search identifies as the record's reference -- one element at a time, an element
taken only where its reference decodes to exactly its own width and names an object the package has (a positive
index inside the export table, a negative one inside the import table); a step with two candidate widths is
recorded, not guessed. A head is accepted only where a compact count **ending exactly at the boundary the walk
reached** equals the elements walked plus the run's units -- an array's count is adjacent to its first element, so
the 1-5 bytes of slack the previous test allowed is a near miss here, not a hit. The null is the same walk, same
widths, aimed one byte past that boundary.

| measure | value |
|---|---|
| classes with a stride-9 run | 536 of 2,204 |
| the walk takes at least one step | **242 of 536** (45.1%) |
| three steps | 102 of 536 (19.0%) |
| eight or more steps (11 on 24 classes, 32 at most) | 24 of 536 (4.5%) |
| the null (one byte past the boundary) takes a step | 52 of 536 (9.7%), never more than 2 |
| a count equal to the elements walked + the run's units, at the head | **1 of 536** (0.2%) |
| the null finds a head | **3 of 536** (0.6%) |
| a count equal to the walked total ending 1-5 bytes early, refused as not adjacent | 15 of 536 |
| the chain stopped by two candidate widths | 28 of 536 (5.2%); by none 506; by the 32-step cap 2 |

**The widths continue, so what is in front of the run is the record's own shapes.** 242 of 536 walk at least one
element and 24 walk eight or more, against 52 and 0 for the same walk aimed one byte off, and a 9-13 byte element
is taken only when its reference lands on an object the package's tables actually hold -- a chain that long is not
what arbitrary bytes give. The run is therefore *inside* a variable-stride body of the same grammar rather than at
the front of one.

**And the count is still not at the end of that body.** One class of 536 has a count equal to the elements walked
plus the units immediately in front of the boundary the chain reached, against three for the null: at chance, below
the control rather than above it. The deepest case says the same thing in one window: `AmbientCreatures.HerdAnimal`
has 15 units at offset 952 of a 1,215-byte middle, the chain walks **11** elements back from it (references `0xA1`
and `0xB3` alternating, 9 bytes each), and stops at 853 where the next element would start on `51 0b ae f6 02 ...`,
a byte group that is not a reference at any width. The 14 bytes in front of that stop are
`05 00 00 00 00 51 0b ae f6 02 00 00 00 00` -- a dword 5, a zero, a four-byte value and a dword 2 -- and the count
this array would need there is **26** (11 walked plus the run's 15). It is not there, and no index whose value could
be a count is: five widths of search find *some* index ending at that position on 480 of the 506 unbranched
chains, which is what any byte position gives when five widths are tried, and the value it spells sits 3-7
**below** the unit count on the six commonest cases (a small integer, 2 on the commonest row).

**The repeated dword does not name an object either.** Decoding it as the single-field index it looks like and
resolving that through the package's own tables names *something* on 338 of 536 (63.1%), but every name it
produces is the front of the table: import 5 `Pawn` (43 classes), import 33 `Canvas` (28), `UWindowCheckbox` (24),
export 1 `Pawn` (19), import 12 `Actor` (12) -- 122 distinct (package, object) pairs, the commonest holding 24
classes. In `AmbientCreatures` the same frequencies land on import 51, `bDeleteMe`, a `BoolProperty` that no record
array holds. A one-byte index cannot name more than 63 objects and the ones it names are the first entries of each
package's table, so "a table of references to one import" is not what the numbers say: the dword is a **small
integer** -- `01`, `83` and `85`, i.e. 1, 3 and 5, in the commonest cases -- whose top bits read as a compact
index's sign.

**Two readings are left standing, and neither is a count in front of the elements.** Either the array's count is
written as a field of the record *before* the body, so the ordering rather than the stream is what is missing, or
the body is not a `TArray` at all but a run read until a terminator -- the shape the defaults themselves have, since
`UClass::Serialize` writes them with no length. The next instrument is `FUN_0003d900`'s **caller**, which allocates
the array it fills: its `push count; call operator new` pair, or the `TArray` head it passes in, is where a count of
this body is written, and it is a read of code rather than another walk of the bytes.

### `UClass::Serialize` (`0x0003a800`): the defaults are a buffer, and the record's tail is arrays

`UStruct::Serialize` is not the last reader on a class export's path. `UClass::Serialize` (`0x0003a800`, 1,310 bytes)
calls it through `UState::Serialize` (`0x000394c0`) first and then writes the class's own fields. Read from the
decompiled body (under `$WORK`, never committed), the order is:

| field | offsets | how it is written |
|---|---|---|
| `UStruct` (Super, Children, `FriendlyName`, line, text position, script) | — | `UStruct::Serialize` (`0x000392b0`) |
| object flags, version < `0x3e` only | `+0x1c` | one dword, then `ObjectFlags |= 0x80004` |
| five dwords | `+0x498`, `+0x4a0`, `+0x4a4`, `+0x4a8`, `+0x4ac` | `Ar << dword` (`0x00037110` / `0x000370e0`: archive vtable `+4`, length 4) |
| `TArray` of 12-byte records | `+0x4d0` (data), `+0x4d4`, `+0x4d8` | `0x0003d900`: count (`0x000637e0`), then `n` x 12 bytes |
| `ClassWithin` (`UClass*`) | `+0x4b0` | version >= `0x3e` only, through the archive's **reference** slot `+0x18`; asserted non-null at `UnClass.cpp:1000` (`ClassWithin`) |
| config name (`FName`, **one** dword) | `+0x4b4` | version >= `0x3e` through the **name** slot `+0x1c`, else `FName(L"System")`'s first dword |
| `TArray<dword>` | `+0x4dc`, `+0x4e0`, `+0x4e4` | `0x0003d160`: count, then `n` x 4 bytes |
| `TArray<dword>`, version > `0x62` | `+0x4f4`, `+0x4f8`, `+0x4fc` | the same helper |
| **defaults** | `+0x4e8` data, `+0x4ec` Num, `+0x4f0` Max | read by vtable **+0x98** = `0x0003bdc0`, written by **+0x94** = `0x0003a220` |

Three consequences:

* **the defaults are a `UClass` field, not a `UStruct` one, and their length is not in the payload.** The load path
  sizes the buffer with `GetPropertiesSize()` (vtable **+0x88** = `0x0003d520`) and asserts
  `Defaults.Num()==GetPropertiesSize()` before and after the stream is read (`UnClass.cpp:0x454` saving, `0x459`
  loading, `UnClass.h:0x235` after the read). So every "a size field in front of the block" search -- U13's and
  `docs/CLASS_DEFAULTS.md`'s -- was looking for something the build never writes: the stream is delimited by its own
  `None` terminator and bounded by the property record.
* **one reader for every class object.** These four slots are resolved through `Class`'s own table (`0x004d4870`), not
  through the per-class tables in `.rdata` -- those describe *instances* of a class, so their `+0x88`/`+0x94`/`+0x98`
  entries are a different function for nearly every class (109 / 140 / 105 distinct targets over the 492 tables) and
  none of them is this reader. `python3 tools/class-serialize-slots.py --xbe <dump>/default.xbe --class Class` prints
  the object's ABI by name.
* **all 2,319 class payloads end on the stream's terminator.** `inventory/class_records.csv`'s `last_byte` is `00` for
  every class export in the corpus, which is what a `None`-terminated tagged stream looks like when it is the last
  thing written -- and it is why a *forward* scan from a guessed start lands in the record's tail instead: the two
  `TArray` fields above are the "hash-like 4-byte values that repeat across classes" `docs/CLASS_DEFAULTS.md` had
  recorded without an owner.

What that leaves is a direction rather than a route: a class's defaults block is the last thing in the payload and
ends on its last byte, so it can be read **backwards** from that terminator instead of forwards from a candidate
start, with the record's tail now a named set of fields rather than unknown bytes.

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
  archive vtable **`+0x18`** — the *object-reference* method. **Its encoding is measured now, and it is the compact
  codec**: on all 2,319 class payloads that reference decodes into the export's own `super` in 1 or 2 bytes and
  never in 4 (*The class record's prefix* below), so the "not a compact index" reading this bullet used to carry
  was wrong about the stream — what the payload-layout attempt got wrong was the field *list*, not the encoding.
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
**2026-11-12: the field widths are measured, and they are not what the failure above blamed.** The references are
compact and the record's list is shorter than `UStruct::Serialize`'s body suggests, so the earlier attempt's field
*set* was the problem, not its encoding. The measurement, and the third stream-side reading it closes, are below.

### The class record's prefix: three references, 3-6 bytes

`tools/class-prefix.py` (`make class-prefix`, `--check` in `make ci`) decodes every `Class` export's first field with
`UStruct::Serialize`'s own order and asks the package's export table whether each answer is the right one, over all
2,319 class payloads (`inventory/class_prefixes.csv`):

| measure | answer |
|---|---|
| a compact index at offset 0 equals the export's `super` | **2,319 / 2,319** |
| bytes that reference occupies | 1 on 1,451, 2 on 868, **4 on 0** |
| the field after it is exactly one `00` byte | **2,319 / 2,319** |
| the third field is a compact index naming the class's own `TextBuffer` | **2,319 / 2,319** |
| where the prefix ends | offset 3 (19 rows), 4 (1,152), 5 (755), 6 (393) |

Three consequences:

* **The archive's object-reference method is the compact codec on this build.** A 32-bit index would make the first
  field four bytes on every payload; it is one or two, and it agrees with the export table on every class. The two
  references were never the obstacle.
* **The byte between `Super` and the script reference is a null reference, not `FriendlyName`.** The reader asserts
  `FriendlyName != NAME_None` (`UnClass.cpp:0x2e2`), so an `FName` in that slot could not encode as `00`; a null
  object reference does, and 2,319 of 2,319 say `00`. The cooked record writes `Super`, a null `Children` and
  `ScriptText` — **not** `Next`, `FriendlyName`, the source line, the text position or a script size — which is why
  the field list the earlier attempt used could validate on none of the 3,931 functions.
* **The record's middle is 91 bytes at the median and 394 at p90.** Where the locator found a block start, the
  distance from the prefix's end to it runs 8..13,660 bytes; 15 rows answer a start *inside* the prefix (offset 1),
  so those located starts are the locator's own false positives — named rather than averaged away.

The `UClass` field list does not fill that middle either, and that is measured with a control: `tail_fits` scans
**every** offset of each located payload for the list `UClass::Serialize` writes before the defaults — five dwords, a
count plus 12-byte records, the config `FName`, then two count-plus-4-byte arrays, with the counts read as 32-bit
**and** as compact and the `FName` as two dwords **and** as two compact indices — and requires the list to end
exactly on the block start. **191 of 2,209** land, and **184 of 2,209** land when the same walk is aimed eight bytes
short of the start: no discriminating power, and the 191 sit at 124 distinct offsets with the commonest holding 27.
So the record's tail is not that field list either, which closes a third stream-side reading the way the backward
array walk and the bytecode walk were closed.

Not measured yet: the walker's own dispatch (its jump table is the opcode table 3.7 needs), and whether any shipped
function's script carries the `G` marker.

### The array's count is its own first field, and its writer has one caller

The previous note left one question open by name: `FUN_0003d900`'s caller, the function that "allocates the array
whose records it fills", read from its `push count; call operator new` pair. That caller is `UClass::Serialize`
itself, and it does not allocate anything: a direct-call census of the image (`python3 tools/call-census.py
--target 0x3d900 --names`) finds **one** `E8` site for that function in 6.2 MB, `0x3a8a6`, inside `0x0003a800`,
and there the archive and the class object's own member are pushed -- `this + 0x4d0`, with `this + 0x4dc` handed
to the `FUN_0003d160` call that follows at `0x3a8af`. `FUN_0003d900` writes that member in place: `FUN_000637e0`
on the `TArray`'s `Num` word first, then one element per unit through archive vtable `+0x18` (an object reference)
and two raw dwords. The same census puts `0x3d160` at **7** call sites -- `0x3a8af` and `0x3a904` (the `+0x4dc`
array and, on version > `0x62`, `+0x4f4`), `0x500a1`, `0x172fe4`, `0x22d53f`/`0x22d54c` and `0x66a93` in
`UObject::Serialize` -- and `0x637e0` at **323**, which is what a count codec called by every array writer looks
like. So there is no build-side allocation to find: the count is the array's **own first field**, written by the
same function that writes the elements, and it counts the whole array rather than the elements a walk can reach.

**Where the array therefore sits, and what the bytes say about it.** Because `UClass::Serialize` writes
`UStruct`'s prefix and then, unconditionally, `UState`'s 22 bytes and five dwords before this field,
`tools/class-record-array.py` (`make class-record-array`, `inventory/class_record_array.csv`, `--check` in
`make ci`) reads the array at `prefix_end + 42` on every class and walks its tail -- the name arrays `+0x4dc` and,
version-gated, `+0x4f4`, whose number is therefore 0, 1 or 2 rather than the two the field list assumed:

| measure | value |
|---|---|
| located classes | 2,204 |
| the array parses at `prefix_end + 42` | **549** (24.9%) |
| ... with every element reference inside the package's tables | 365 of 549 (66.5%) |
| ... walking on to land **exactly** on the defaults start | **3** of 549 (0.5%), none of them table-clean |
| the same walk one byte later (`prefix_end + 43`) | **0** of 549 |
| the same array with the dword group moved after it (`prefix_end + 22`) | 36 of 549 (6.6%), 0 exact |
| where the walk stops short of the defaults: mode / median | **20** bytes (190 classes; 22 on 59, 21 on 31) |
| the same grammar from every byte position of the middle | 1,214 of 2,204 (55.1%), against 602 (27.3%) aimed one byte short |

**The position is the writer's, and the residue is one field wide.** Of the 549 classes where the array parses at
`+42`, only 36 still parse when the dword group is moved after it, and a walk one byte late never lands at all --
so the position in front of the array is decided: prefix, `UState`, five dwords, then the array. What follows the array is not another name array either: with 0, 1 or 2 of
them tried, the walk misses the defaults by exactly **20 bytes** on 190 of 549 classes -- the dword group's own
width -- where a missing name array would leave 2-3 bytes and a missing prefix would leave 42. The array is
therefore real, sized from the record, and the 20 bytes between its tail and the defaults are the next field, not
a rounding of this one.

**And the free-position scan is why three readings could not settle this from the bytes.** The same grammar
started at *every* position of the middle finds a walk that ends on the defaults on 1,214 of 2,204 classes, and on
602 when the target end is one byte short: eleven-byte elements fit almost anything, so a head found by scanning is
not a head. The earlier tools did not fail for want of a rule; they were asking a question the writer answers with
a position and a count, and both of those are in the code.

**What this does to the stride-9 run.** The run of identical 9-byte units sits inside this array's element span on
**98 of 536** classes, so on most of them the run is a stretch of *something else* after the array -- on
`AmbientCreatures.HerdAnimal` the array at `prefix_end + 42` is **22 records, every reference one byte**, ending
at offset 246 of a 1,365-byte payload, while the 15-unit run the earlier note built on starts at 952, 706 bytes
further into the record. The count that run never had in front of it is at the *array's* head, which is 905 bytes
in front of the run and not adjacent to it.

### The element is three fields, and only the first is a compact reference

`FUN_0003d900`'s element loop hands the archive **three** pointers, not one: `Data + index*0xc` is computed once
(`LEA EDX,[EBP + EBP*0x2]` then `LEA EAX,[EAX + EDX*0x4]`), then `element+8`, `element+4` and `element+0` are pushed
and a single `CALL dword ptr [EDX+0x18]` takes them (`0x3d99a`-`0x3d9b0`). The count is `FUN_000637e0` on the array's
`Num` word (`0x3d941`, and again at `0x3d9df` on the save path), the archive's `+0x14` slot is handed
`Max*12`/`Num*12` before the loop (`0x3d90e`-`0x3d927`), and the load path clears `Max` and re-allocates with a
`push 0xc` per element (`0x3d94d`). So the *memory* element is three dwords and the writer serializes all three.

That is the writer's shape; the bytes settle the width. `tools/class-record-element-fields.py`
(`make class-record-element-fields`, `inventory/class_record_element_fields.csv`, `--check` in `make ci`) walks the
array at the same position with three compact fields per element and compares the landing with the one-reference
walk:

| measure | value |
|---|---|
| classes the shipped walk read at `k=2` | 395 |
| three compact fields walk clean | 364 |
| ... landing exactly on the block start | **0** |
| ... stopping short of the one-reference walk | 363 (past it: 1) |
| span between the two walks | **17 bytes on 293**, 18 on 21, 16 on 9 |
| bytes per element under three compact fields | median **3.00** |

Three one-byte fields make a 3-byte element, so that grammar would end the array at a third of the width the shipped
walk measures; instead the walk stops a near-constant 17 bytes short, which is the two name arrays re-read from an
earlier offset rather than six bytes lost per element. The element therefore stays
`[compact reference][dword][dword]` -- 9 bytes at width 1, exactly `HerdAnimal`'s 199 bytes for 22 elements -- and
the `+0x18` slot writes a raw dword for its second and third calls even though the writer hands all three fields to
it.

### The prefix's third field is a ScriptText export, on all 2,209 records

`UStruct::Serialize` writes five fields after `Children` -- `FriendlyName` through the archive's `+0x1c` slot, a
dword behind the `0x77` version gate, two more dwords and `ScriptText` -- so the record's 3-6 byte prefix can only
carry the ones the cooker kept, and *which* ones is a question the bytes answer without a width argument. The
archive's `+0x18` slot takes an **object reference**: a positive value indexes the export table, a negative one the
import table, and nothing else. So if the prefix's third field is the class's script it must land on an export of
class `ScriptText`, and if it were `FriendlyName` it could not land on anything.

`tools/class-scripttext-ref.py` (`make class-scripttext-ref`, `inventory/class_scripttext_refs.csv`, `--check` in
`make ci`) decodes the three compact fields and resolves the third through that method:

| measure | value |
|---|---|
| class records with a decoded prefix | **2,209** |
| third field is a positive export reference | **2,209** |
| ... whose export is named `ScriptText` | **2,209** |
| third field is a null reference | 0 |
| `Children` is a null reference | **2,209** |

A perfect landing is the field order confirming itself: the record writes `Super`, a null `Children` and a
**reference to the class's own `ScriptText` export**, which is why no script bytes sit in the record and why the
reader resolves it as an object. `FriendlyName` is not in the record at all -- the body's assert
`FriendlyName!=NAME_None` (`UnClass.cpp:0x2e2`) is a *reader*'s precondition, satisfied by the name the export table
already carries, not by a field in this stream.

That closes the residue question from the other side. The 20/27 bytes in front of a defaults block cannot be a
`UStruct::Serialize` tail field, because every field that tail writes after `Children` is measurably absent from the
record: no `FriendlyName`, no line, no text position, no script bytes, and the one field that does exist there is a
single reference that the record's own prefix already carries. The gap stays the array's (see *The element is three
fields, and only the first is a compact reference* and *After the last name array...*).

### After the last name array the body writes nothing, so the gap belongs to the array

The last note left one measurement named: read `UClass::Serialize`'s body between the `+0x4f4` call and the defaults
branch for a field the version gate hides. Read at the instruction level, there is no such field -- the call is
followed by four bytes of stack cleanup and the defaults branch itself:

    0x3a8f6  CMP dword ptr [EBP + 4],0x63   ; the archive version, the last gate in this function
    0x3a8fa  JL  0x0003a90c                ; below it the +0x4f4 array is not written at all
    0x3a8fc  LEA ECX,[ESI + 0x4f4]
    0x3a902  PUSH ECX
    0x3a903  PUSH EBP
    0x3a904  CALL 0x0003d160               ; the same count-plus-dwords helper as +0x4dc
    0x3a909  ADD ESP,0x8                   ; stack cleanup only
    0x3a90c  MOV EAX,[EBP + 0x10]          ; save-or-load, then the defaults

So the record's field list is complete from the prefix to the defaults block: one name array at `+0x4dc`, the config
`FName` at `+0x4b0`/`+0x4b4` (`+0x4b0`, a class reference) and the config name (`+0x4b4`, one dword) -- both written on version >= `0x3e`, only the name defaulted below it, and, on version > `0x62`, the second
name array at `+0x4f4`. Anything between the array's tail and the defaults start has to be those three fields.

**They do not account for it, and the array's own end is what moves.** `tools/class-record-array.py` now measures the
gap from the array's *bare* tail (`array_end`, `gap`) as well as from the name-array walks, and asks whether the
writer's remaining fields consume it (`tail_ok`), over the same 549 classes that parse at `prefix_end + 42`:

| measure | value |
|---|---|
| gap from the array's tail to the defaults start: mode | **27** bytes (188 classes; 29 on 61, 28 on 30, 30 on 9) |
| gap: median | 27 bytes |
| the array's walk running **past** the defaults start | 13 classes (-105 on 7, -109 on 6) |
| name array + 8-byte config `FName` + name array closing the gap | **0** of 549 |
| the defaults property stream parsed from the block start, terminating on the payload's last byte | 543 of 551 |

The 20-byte mode the previous note reported is the same bytes read from a different boundary: on those rows its
`k=2` walk had already consumed 7 bytes (a three-element name array, `03 01 04 02 84 03 06` on the `GamePlay` zones),
so the gap from the array's own tail is 27, not 20. A field cannot be 27 bytes wide on 188 classes and 29 on 61 when
the writer's body says nothing is written there at all, and no field *after* the array can put the walk past the
defaults start on 13 classes -- so what is off is the array's element span (a compact reference plus two dwords),
which ends somewhere other than where the writer's next field begins on nearly every class. The record's remaining
gap is inside the array, not after it.

### The element is twelve bytes in memory and nine to thirteen on disk

The last note left the element span open, and the writer answers it without a boot. `FUN_0003d900` (306 bytes) scales
**both** `TArray` words by twelve before its `+0x14` call -- `LEA EAX,[EAX+EAX*0x2]; SHL EAX,2` on `Num` and the same
on `Max` -- and addresses element *i* as `base + i*12` (`LEA EDX,[EBP+EBP*0x2]; LEA EAX,[EAX+EDX*4]`). Inside one
element it makes three archive calls over the three dwords at `+0`, `+4` and `+8`:

    0x3d9a3  LEA ECX,[EAX + 0x8]
    0x3d9a6  PUSH ECX                        ; the third dword
    0x3d9a7  LEA EDX,[EAX + 0x4]
    0x3d9aa  PUSH EDX                        ; the second
    0x3d9ad  PUSH EAX                        ; the first
    0x3d9b0  CALL dword ptr [EDX + 0x18]     ; the archive's object-reference method
    0x3d9b3  PUSH EAX / CALL 0x00037110      ; Serialize(ptr, 4)
    0x3d9bc  PUSH EAX / CALL 0x000370e0      ; Serialize(ptr, 4)

The two trailing calls are one primitive, not a second codec: `0x00037110` and `0x000370e0` bump the archive's byte
clock and then call `Serialize(ptr,4)` through vtable `+4`, and their bodies differ in layout and in nothing else. So
the trailing pair are raw little-endian dwords while the first field is the archive's compact reference, and **a
record is twelve bytes in memory and `ref + 8` on disk** -- nine bytes when the reference is one byte wide, thirteen
at its five-byte maximum, never a fixed twelve. That also names the stride-9 unit `docs/CLASS_DEFAULTS.md` recorded:
a one-byte reference plus the two dwords, with the repeat in the first dword.

### The bytes at the array's own tail parse as the same array again, and the parse at `+42` is all-null

`tools/class-record-element.py` (`make class-record-element`, `inventory/class_record_element.csv`, `--check` in
`make ci`) takes every class whose record the array parses (`inventory/class_record_array.csv`) and restarts the
writer's own grammar at the array's bare tail -- `[compact count][count x (compact reference, 8 raw bytes)]` --
followed by the fields `UClass::Serialize` still writes: up to two name arrays and the config name at
`+0x4b0` (a class reference) and the config name at `+0x4b4`, in the source's order and with the name's width tried both ways (eight raw bytes, or two
compact indices) -- a reading this page has since corrected to **two fields**: a reference through the archive's
`+0x18` and a name through `+0x1c`, not one 8-byte `FName` (see *The pair at +0x4b0/+0x4b4 is a class reference
and a name, and the image names the first*):

| measure | value |
|---|---|
| classes the array parses at `prefix_end + 42` | 549 |
| a second array of the same grammar parses at its tail | **386** |
| ... and one of the writer's field orders then lands on the defaults start | **291 of 386 (75.4%)** |
| the same grammar started at each of the next 40 bytes (control) | 76 of 10,384 (0.7%) |
| the parse at `prefix_end + 42` is all-null records | **336 of 549** |
| the second parse is all-null records | 0 of 291 exact |
| the second parse's references are inside the package's own tables | 372 of 386 |

Every one of the 291 exact rows is a class whose parse at `prefix_end + 42` was all-null, so the two findings are one:
on those classes that position holds a field *shaped like the array* whose references are all null, the array proper
begins at that field's tail, and the writer's three remaining fields then close the record to the byte.
`GamePlay.WaterZone` is the small case -- a 102-byte payload whose prefix ends at 4 and whose defaults start at 92:
the parse at 46 is two all-null records ending at 65, the parse at 65 is two records with live references (an export
index and an import index) ending at 85, and a three-element name array fills 85..92.

**Two readings, and the writer's body rules one out.** A second array of the writer's shape needs a second writer, and
`UClass::Serialize` has exactly one call to `FUN_0003d900` (`0x3a8a6`); its other array writers are `FUN_0003d160` on
`+0x4dc` (`0x3a8af`) and on `+0x4f4` (`0x3a904` above the `0x63` gate), and those stride **four** bytes and write one
`FName` per element through the archive's `+0x1c` method. So the all-null field in front of the array is a member the
record's field list does not carry, and the array's head is one field later than the prefix walk assumed on those 336
classes -- the reading the `first_zero` / `second_zero` columns measure. Left to measure: that member's own identity
(19 bytes on the zone family: a compact count of 2 and eighteen zero bytes) and the exact disk width of one `FName`
through `+0x1c`, which sizes the two name arrays and so the last bytes of every gap.

### The tail's elements are a value and a reference, not two name indices

`tools/class-record-tail.py` (`make class-record-tail`, `--check` in `make ci`) measures the bytes between the located
array's tail and the defaults start on the **291** classes whose record closes
(`inventory/class_record_tail.csv`). They are **6 to 9 bytes** -- 7 on 201, 9 on 64, 8 on 21, 6 on 5 -- and **225** of
the 291 read as a compact count plus that many (compact, compact) pairs: the count is 3 on 219 (a 7-byte tail) and 2 on
6, and the walk consumes the gap exactly on 219 of the 225. The same grammar aimed one to three bytes later lands on
the block start on **0 of 225**, so the position is the record's and not a free scan's.

**The middle pair's second value is negative on 219 of 225, and it takes four distinct values in the whole corpus** --
-6 on 149 classes, -3 on 52, -4 on 12, -7 on 6, one per package. Every first value resolves in the package's own name
table on 219 of 225 pairs -- and those 219 are exactly the rows whose middle pair is negative -- so an element is a
small non-negative value and a *signed reference*; that is the shape of a
reference into the package's import table, and it is not an `FName` duplicate number, which is an unsigned counter.
The reading above -- these bytes as the two `TArray<FName>`s, a compact count and two compact *name indices* per
element -- has no term that can be negative, so it is corrected here: the tail is a value/reference pair list, and the
signed term is why it cannot be what `FUN_0003d160` writes through `+0x1c`.

**On most packages the same bytes come back for every class.** The tail's digest is identical across all 51 rows of
`Stargateeffects`, all 6 of `StargateGame`, 123 of 149 `Sharpeffects`, 5 of 6 `Engine` and 7 of 12 `GamePlay`, and it
varies per class on `StargateContent` (48 distinct tails over 61 classes) and `StargateVehicles` (6 over 6). A field
that does not move between 51 classes is not holding those classes' own content, so on five of the seven packages the
last bytes of the gap are package-level -- which is the fact a "the record's fields end here" reading has to explain.

**And the writer is not the one the page names.** `UClass::Serialize` pushes three pointers at `0x3a897`
(`this+0x4dc`, `this+0x4d0`, the archive); `FUN_0003d900`'s body reads only the archive and `this+0x4d0`, and the
third push is still on the stack when `0x3a8ab`'s `ADD ESP,0x8` leaves it there, so `+0x4dc` is `FUN_0003d160`'s second
argument at `0x3a8af` and never reaches the first callee at all. One expression, two callees, one of the two arguments
passed to both -- which is why the per-call mapping in the field table is right and the pair list still has no writer
inside `UClass::Serialize`.


### The field map is the destructor's, not the constructor's

The class-record notes above read `UClass`'s record region from `UClass::Serialize`'s callees: the record array at
`+0x4d0` (writer `0x3d900`), the two `TArray<FName>` at `+0x4dc` and `+0x4f4`, and the defaults block behind them.
The bytes after the record array still parse as a three-entry value/reference list that no callee in that function
writes, so the region needed a field map -- and the constructor, which `PLAN.md` named as the route to one, cannot
supply it.

**A constructor of this build zero-fills, so it has no boundaries.** `Class`'s default constructor (`0x3ad20`) writes
`+0x4b8`, `+0x4bc`, `+0x4c0`, ... one `MOV dword ptr [ESI + disp],EDI` per dword, and it *skips* `+0x4b4`, `+0x50c`
and `+0x510`; the one-argument form (`0x3ae30`) skips `+0x4ec`/`+0x4f0` too, because it copies its parent's array
(`MOV EAX,[ESI+0x28]; LEA ECX,[ESI+0x4e8]`) instead of writing it; the full constructor (`0x3af60`) writes the run
from `+0x4b4` with no hole at all. Three constructors, three different sets of written dwords, and not one of them
marks a member boundary: the dword a body skips is the neighbour of the one it writes, and which dwords are skipped
differs per constructor.

**A destructor destroys by address, so every member it owns is a `lea`.** `0x3dbd0` is
`lea ecx,[esi+disp]; mov BYTE PTR [esp+0x10],n; call target` eight times, one per member, and the offsets it names
are the field map of the whole record region:

    +0x4b8 -> 0x3d7a0      +0x4d0 -> 0x3da40      +0x4e8 -> 0x1aa30      +0x500 -> 0x1c750
    +0x4c4 -> 0x3d850      +0x4dc -> 0x1c750      +0x4f4 -> 0x1c750      +0x514 -> 0x1b310

**That moves the array in front of the record array from `+0x4c0` to `+0x4c4`.** `+0x4c0` is the last dword of the
member at `+0x4b8` and the first of nothing, so the "`+0x4c0`..`+0x4cc` member" of the earlier note is the
constructor's own zero grid spanning one `TArray`'s tail and the next one's head. `+0x4b8`, `+0x4c4` and `+0x4d0`
are twelve bytes each -- three `TArray`s of three *distinct* element types, which is what their three destructors
say: `0x3d7a0`, `0x3d850` and `0x3da40` open with the same thirty bytes (`MOV EDI,[ESI+0x4]; TEST EDI,EDI;
PUSH 0x287; PUSH 0x4c87e8; PUSH 0x4c87cc`) -- one template instantiated three times -- and each of the three serves
exactly one member in the whole image, where the plain name arrays at `+0x4dc`/`+0x4f4`/`+0x500` share `0x1c750`
(117 members) and `+0x514`'s `0x1b310` (328) is a different body with no element loop.

**The instrument is `tools/class-member-map.py`, and the corpus says the rule is general.** It decodes every
constructor and destructor in `inventory/class_constructors.csv` with `objdump` from the function's own first byte
and writes one row per `this`-relative reference (`inventory/class_member_map.csv`, 3,418 rows). The slice has to be
its own file: `--start-address` keeps decoding the section from its beginning and only prints from that address, so
every body that sits after a jump table arrives desynced and reads as `(bad)` -- the first run of this tool reported
59 of 60 bodies unread for exactly that reason. Of the 493 destructors, **765 members** are placed and every one of
them carries a destructor; there are **199** distinct destructors and **146** of them are used by exactly one member.
Only **210 of 762** member starts are dwords a constructor of the same class also writes, so for two thirds of them
the constructor route has nothing to say: the destructor sequence is a class's field map in this build, and a
constructor's store map is not.

    python3 tools/class-member-map.py --xbe DUMP/default.xbe -o inventory
    python3 tools/class-member-map.py --class Class --report


### The one byte a `TArray` destructor varies is its element width

A `TArray<T>::~TArray` in this build is one template, and its per-instantiation byte is a single `PUSH imm32`: the
first argument of the range destroy at `0x55f00`. `Class`'s three adjacent arrays differ in that instruction alone --
`0x3d7a0` pushes 8, `0x3d850` pushes 4, `0x3da40` pushes 12 -- and the rest of the three bodies is byte-identical
(`MOV EDI,[ESI+0x4]; TEST EDI,EDI; ... PUSH 0x287; PUSH 0x288; ... PUSH edi; PUSH 0x0; MOV ECX,ESI; CALL 0x55f00`),
followed by the same free through the allocator object at `0x5bf510`'s vtable slot `+0x8`.

So the `PUSH` is a measured element width, not an inference. Corpus-wide, of the **199** distinct destructors members
are passed to, **93 are the template** (covering 302 of the 765 placed members) and **106 are bespoke**; all 93 yield
a width -- from 1 byte (`0x1aa30`) to 1,068 -- with 4 bytes on 25 targets, 8 on 11 and 12 on 7, so the reading is not
one constant that happens to fit.

That is a second, independent reading of the record array's element: `+0x4d0`'s destructor pushes **12**, the same
12 `FUN_0003d900` scales by, and the two members around it are 8 and 4. It also settles the two name arrays that the
arithmetic only implied -- `+0x4dc`, `+0x4f4` and `+0x500` share `0x1c750`'s **4** (`FName`-width elements) -- and it
says the array inherited into `+0x4e8` is **1** byte per element.

**And it removes a candidate for the tail.** The pair list after the record array is a value and a *reference* per
element, and a 4-byte element cannot hold both: `+0x4c4` is out, and of the three `TArray`s in the group only
`+0x4b8` (8-byte elements) is left. That is a narrower question than the offsets alone gave, and it is now the named
next measurement: what indexes the `+0x4b8` array at a stride of eight.

Toward the free path the corpus is nearly uniform and the exception is worth recording: **154** of the 199 free
through the allocator object at `0x5bf510`'s vtable `+0x8`, **one** frees its buffer with a direct `CALL`, and **44**
have no free call at all -- they destroy their elements and leave the buffer to a base class. So the free a destructor
uses does not identify its element type, and the width does.

    python3 tools/class-member-map.py --xbe DUMP/default.xbe --targets -o inventory
    python3 tools/class-member-map.py --xbe DUMP/default.xbe --targets -o inventory --class Class

### Every `+0x4b8` access in the image, and the two that index it at eight

The measurement the element widths left open was "what indexes the `+0x4b8` array at a stride of
eight", with the warning that the writer "will not be reached from `UClass::Serialize`".
`tools/member-access-census.py` (`make member-access-census`, artifact
`inventory/member_access_census.csv`) censuses one displacement over the whole image: it sweeps every
body `symbols/xbox_functions.csv` places -- 14,973 bodies over 2.5 MB -- from that body's own first
byte, so a candidate is an instruction the decoder produced at an exact boundary and not a byte
pattern that happens to contain `b8 04 00 00`, and each row records how many consecutive instruction
ends confirm that boundary.

**Seventeen sites in fifteen functions.** Nine load the member, two `lea` its address (the two
destructors the member map already had) and six store to it; a further thirty-six hits are
`[esp+0x4b8]`/`[ebp+0x4b8]` stack slots, counted apart rather than dropped, and none is an absolute
address. Of the seventeen, **three loads index the value at stride 4, two at stride 8**, and four
pass the value on without an index -- two dereference it as an object (`0x001be0a8`, `0x001be0bb`),
one copies it into another field (`0x000e685f`), one keeps it in `edi` past the window
(`0x000cfda5`).

**The two stride-8 consumers read an element as two words, and one reads exactly the pair the tail
parses as.** At `0x0010b719` in `Engine_UnChan` (Engine module, 2,757 bytes):

    mov  ecx,[eax+0x4b8]        the array's data pointer
    mov  esi,[ecx+edx*8]        element + 0  -> an object pointer, read at +0x38/+0x48/+0x20
    mov  ebx,[ecx+edx*8+0x4]    element + 4  -> a count, `imul edi,ebx` four instructions later
    lea  eax,[ecx+edx*8]        the element's own address

so the element is a **reference and an integer**, which is the shape the record's tail parser reports
as a value and a reference. The second, `0x00358c4a` in `FUN_00358bb6` (260 bytes), reads the same
eight bytes as two 16-bit fields -- `mov dx,[eax+0x6]`, `mov ax,[eax+0x4]` -- and copies them into
its own `+0x3fa4`/`+0x3fa6`, a different class's struct and not a pair list.

**But neither of them is a `UClass`, and on a `Class` the array is never read.** The destructor map
places a member at `+0x4b8` on exactly two classes, `Class` (8-byte elements) and
`GameReplicationInfo` (4-byte), and the two stride-8 sites belong to neither: theirs are an unnamed
260-byte body whose object carries `+0x1474`/`+0x2d38`/`+0x3fa4`, and an Engine body whose object is
reached through `+0x6c`. On a `Class` the census finds only its destructor's `lea` + `0x3d7a0` call
and its three constructors' zero-fill; `UClass::Serialize` contains no access at all, and no function
in the image indexes a `Class`'s `+0x4b8` at any stride. The three zero-fills are the same three rows
`inventory/class_member_map.csv` records as `store-zero` at that displacement -- an independent
artifact agreeing -- and the census adds two stores that map does not classify, `0x00114e6b` and
`0x0035508e`, each copying another field's value into its own object's `+0x4b8`.

**So the tail is not this array's content on the evidence available, and the width argument that made
it a candidate still holds.** The 8-byte element is measured twice, from functions that do not call
each other: the destructor's `PUSH 0x8` and `Engine_UnChan`'s element+0/element+4 read. What is
missing is an owner: an array nothing reads cannot be the producer of a stream the record carries,
and there is no reader of a `Class`'s `+0x4b8` to be its consumer either. The next measurement is the
rest of the group -- the same census at `+0x4c4` and `+0x4d0`, which says which of the three arrays a
`Class` is actually read through, `+0x4d0` being the record array whose writer `FUN_0003d900` is
already measured -- and then the same census with each base register's origin followed, so the two
stride-8 objects are typed instead of assumed.

    python3 tools/member-access-census.py --xbe DUMP/default.xbe --disp 0x4b8 -o inventory
    python3 tools/member-access-census.py --xbe DUMP/default.xbe --disp 0x4b8 --report --window-show

### The group of three, and the one array no `Class` code names

The `+0x4b8` census left the record's tail unowned and named its successor: the same census at
`+0x4c4` and `+0x4d0`, to say which of the three adjacent `TArray`s a `Class` is actually reached
through. `tools/member-access-census.py` now takes `--disp` more than once and writes one artifact for
the group -- **61 sites over the three displacements** (17 at `0x4b8`, 29 at `0x4c4`, 15 at `0x4d0`).

**A `Class` names two of the three, and the third only through its constructors and destructor.**
`0x3a670` (`Class` table slot 10) is the region's initialiser: it takes each array's address, zeroes
the `count`/`max` pair at `+4`/`+8` and calls the same helper with that array's *element width*

    lea  ecx,[esi+0x4c4] ; push 0x4  ; call 0x1b846
    lea  ecx,[esi+0x4d0] ; push 0xc  ; call 0x1b833
    lea  ecx,[esi+0x4dc] ; push 0x4  ; call ...

and `UClass::Serialize` (`0x3a800`, slot 11) takes `+0x4d0`'s own address at `0x3a89e` -- the record
array, at the width the destructor independently measures. The three constructors zero-fill all three
and the destructor destroys all three. **No function whose class is `Class` takes `+0x4b8`'s
address**: the 8-byte array is the one member of the region initialised inline, a dword at a time in
each constructor, and never named again.

**The displacement is shared, which is why the attribution column is the finding.** Of the 29 sites at
`+0x4c4`, seven distinct classes are named by their own tables -- `Pawn` at slots 57, 85, 87, 88, 89
and 124, `Controller@30` and `SparkEmitter@47` -- and only the `lea` at `0x3a675` plus the four
constructor/destructor sites belong to `Class`. At `+0x4d0` the tables name `xEmitter@35`,
`Controller@52` and `PlayerController@52` beside `Class@10` and `Class@11`. **None of the 44 sites at
these two displacements indexes the value it loads at any stride**: they take the array's address and
hand it to a callee, which is the shape a `TArray`'s initialiser and destructor have, not a reader's.

**So the tail's owner is not any of the three on the read side, and the group's initialiser says why
that is not a contradiction.** The record array's writer `FUN_0003d900` is reached with `+0x4d0`'s
address from `UClass::Serialize`, so the bytes that produce the record's middle come from a function
handed the array's address -- not from an index loop over any of the three, and a `TArray` nothing
indexes can still be filled by the callee it is passed to. What is left is who fills the 8-byte array,
and within this image nobody does: its six stores are the three constructors' zero-fill, one direct
`Class` sibling, and two copies into objects of classes that carry neither `+0x4d0` nor
`UClass::Serialize`. The named next measurement is the body of the writer itself, past its element
loop.

    python3 tools/member-access-census.py --xbe DUMP/default.xbe -o inventory
    python3 tools/member-access-census.py --xbe DUMP/default.xbe \
        --disp 0x4b8 --disp 0x4c4 --disp 0x4d0 --report

### The array writer is 306 bytes, and the four members behind it are arrays

`FUN_0003d900` was the named next measurement -- "the body of the writer itself, past its element loop". Read to its
end it is **306 bytes** (`0x0003d900` to `0x0003da31`) with **two loops and no code after either of them**: both exit
into the same epilogue (`POP EDI; MOV EAX,ESI; POP ESI; POP EBP; POP EBX; RET`, at `0x3d9d6` and `0x3da2b`), and the
next body in the symbol map, `0x0003da40`, is the record array's own destructor. The two loops are the archive's two
directions of one array: the branch it selects on is the flag at `[archive+0x10]` (`0x3d92a`), and the path taken when
that flag is zero (`0x3d9dd`) reads the compact count into a temporary and streams each element where it already
sits, while the other (`0x3d937`) grows the array as it reads. Neither path writes a second structure after the
element loop, so the `3 x 9`-byte pair list the record's tail carries is **not** the array writer's, and the "one
body, already partly read" reading of the previous note is closed.

**The four members behind the record array are censused, and none of them is a pair list.** `make
member-access-census` now sweeps the record region's seven displacements together (`--disp` is repeatable), and
`inventory/member_access_census.csv` is **217 sites**:

| displacement | sites | forms | stack forms | destructor | element |
| --- | --- | --- | --- | --- | --- |
| `+0x4b8` | 17 | 9 load, 2 `lea`, 6 store | 36 | `0x3d7a0` | 8 |
| `+0x4c4` | 29 | 8 load, 16 `lea`, 5 store | 8 | `0x3d850` | 4 |
| `+0x4d0` | 15 | 3 load, 7 `lea`, 5 store | 2 | `0x3da40` | 12 |
| `+0x4e8` | 105 | 95 load, 6 `lea`, 4 store | 19 | `0x1aa30` | 1 |
| `+0x4f4` | 22 | 8 load, 7 `lea`, 7 store | 4 | `0x1c750` | 4 |
| `+0x500` | 12 | 7 load, 1 `lea`, 4 store | 6 | `0x1c750` | 4 |
| `+0x514` | 17 | 5 load, 5 `lea`, 7 store | 2 | `0x1b310` | none measured |

`+0x4e8` is the defaults buffer, and its 95 loads say so: it is read by the T3D exporter (`0x000348d5`), by
`UExporter` and `UFactory` (`0x00034d0b`, `0x000361fd`), by the class serialiser itself at `0x3a96d`/`0x3ac96`/
`0x3acf6`, and by the three native property walkers at `0x0004708b`/`0x00047126` -- the shape of a buffer every
property reader consults, not of a per-class list. Its destructor `0x1aa30` agrees from the other side: it is the
template's *Empty* path with **no element loop** (`PUSH 0x1; PUSH count; PUSH 0x0; CALL 0x55f00`, then one free
through `0x5bf510`'s slot `+0x8`), so the member holds bytes and has no element to destroy.

**And the one member whose map entry is not a destructor.** `+0x514` is labelled `dtor` in
`inventory/class_member_map.csv` through `0x1b310`, which 328 members share -- but that body zeroes `Num` and `Max`,
calls `0x55ed0` with **2** and ends by calling `0x1a920`: a reset/constructor shape, so the `2` is an element width
taken at construction and not a per-element destroy. The displacement is also shared across classes (the census
attributes `+0x4f4` to `GameReplicationInfo`, `Weapon`, `BeamEmitter` and `Pawn@73` as well as to seven `Class`
slots), so neither the label nor the displacement alone types it; what survives is that the record region ends in a
byte buffer, two 4-byte name arrays and a 2-byte string member, and that none of the four can hold a
(reference, value) pair.

    python3 tools/member-access-census.py --xbe DUMP/default.xbe -o inventory
    python3 tools/member-access-census.py --xbe DUMP/default.xbe \
        --disp 0x4b8 --disp 0x4c4 --disp 0x4d0 --disp 0x4e8 --disp 0x4f4 --disp 0x500 --disp 0x514 --report

### The tail is a count of name/reference pairs, and not an `FName` array

`tools/class-record-tail.py` now reads the 6-to-9-byte tail both ways on the 291 records that close. As a bare
`(index, number)` `FName` sequence -- the leading value taken as the first name's index -- it closes **0 of the 219**
three-pair tails (the six two-pair tails close only because 6 and 8 bytes are an even number of two-byte names, and
they leave a negative number behind, which an `FName` cannot hold). As a compact count plus that many pairs it
closes all 219, and every pair's second value then fits the archive's reference encoding (0 null, negative import
`-v-1`, positive export `v-1`) on **225 of 225** parsed tails. The negative term names one import, `Class.Object`
(the import of `Core.Object`), on **219 of 225** rows, against five distinct targets when the same test is aimed one
import earlier -- so the four spellings that term takes across the corpus are one object at four import-table
positions, and the field is a reference rather than a number. The pairs' first values classify as the package's own
name, `Core` and `System` on 189 rows (`pkg;Core;other` 18, `other;Core;System` 12, `pkg;?` 6), which is why the same
tail closes every class in five of the seven packages. The owner is still unnamed: no member the destructor places
in the record region can hold such a pair, and a tail written once per package is not written per class.

    make class-record-tail
    python3 tools/class-record-tail.py --dump $SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
    python3 tools/class-record-tail.py --class WaterZone --package GamePlay --dump $SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15

### The reader writes nothing between the last field and the defaults, so the list is a field

`0x0003a800`'s listing (`$WORK/module-export/Core/`, 120 instructions from `0x3a894`) gives the record's **field
order in the reader**, and it is the order of the file:

| address | what |
| --- | --- |
| `0x3a8a6` | `CALL 0x0003d900` -- the record array at `+0x4d0` |
| `0x3a8af` | `CALL 0x0003d160` -- the name array at `+0x4dc` |
| `0x3a8b4` | `MOV EAX,[EBP+0x4]; CMP EAX,0x3e; JL 0x3a8de` -- version gate |
| `0x3a8c2`-`0x3a8d9` | `LEA EAX,[ESI+0x4b0]` then `CALL [EDX+0x18]`, then `MOV EDX,[EAX]` / `LEA ECX,[ESI+0x4b4]` / `CALL [EDX+0x1c]` -- the config name |
| `0x3a8f6`-`0x3a904` | `CMP [EBP+0x4],0x63; JL 0x3a90c` then `CALL 0x0003d160` -- the second name array at `+0x4f4` |
| `0x3a90c`-`0x3a913` | `MOV EAX,[EBP+0x10]; TEST EAX,EAX; JZ 0x3ac63` -- the archive's direction flag |
| `0x3a96d`-`0x3a981` | `LEA EDI,[ESI+0x4e8]`, `[EDI+0x4]=0`, `[EDI+0x8]=GetPropertiesSize()`, `CALL 0x00055ed0` -- the defaults buffer, and then its stream |

Three consequences, all from the listing:

- **There is no unowned tail.** Between the `+0x4f4` call and the defaults buffer the body writes nothing, so the
  6-to-9-byte list `tools/class-record-tail.py` measures sits at the position of the **last field it writes**, the
  version-gated `+0x4f4` name array -- count 3 plus three two-byte elements on the 219 three-pair rows, which is the
  shape `FUN_0003d160` writes. It is a field, not free bytes before the defaults block.
- **The 27-byte gap before a defaults start is those three fields.** On the rows where the list is 7 bytes the middle
  is 20 bytes on 198 of 219 classes, and `first_count` is 2 on every one of them, so the 20 bytes are the `+0x4dc`
  array and the 8-byte config name. Their split is not measured -- that is the next read, not a claim.
- **The config name is two virtual calls on two adjacent members,** `+0x18` on `+0x4b0` and then `+0x1c` on `+0x4b4`
  taken through the object the first call returned (`MOV EDX,[EAX]`; `MOV ECX,EAX`). So a vtable slot is not
  "the reference method" or "the name method" on its own: the pair writes one `FName`, and every element count in
  this region that was read as "through `+0x1c`, the name method" is one call of that pair.

What this corrects in the tools above: `inventory/class_record_element.csv`'s `second_end` is *not* a field
boundary -- the array that ends there is inside the 20-byte middle -- so "the bytes after the array" is the record's
last field. A class whose `+0x4f4` array were empty would carry no list at all, which is the check that would falsify
the placement and has not been run.

### The 20-byte middle is a second record array, and the config name is not in it

`tools/class-record-gap.py` (`make class-record-gap`, `inventory/class_record_gap.csv`, `--check` in `make ci`)
takes every class whose array the reader locates (`inventory/class_record_array.csv`, 549 rows) and decomposes the
gap from that array's *own* tail to the defaults start instead of naming its fields. The gap is 27 bytes on 188
classes, and its first part is the writer's array grammar again -- a compact count and, per record, one compact
reference plus eight raw bytes:

| measure | value |
|---|---|
| a second array of `FUN_0003d900`'s grammar parses at the located array's tail | **361 of 549 (65.8%)** |
| its byte length: median, most common | **20** bytes, 20 on **260** classes (21 on 33, 22 on 19, 19 on 9) |
| records per array | 2 on 321, 11 on 4, 57 on 3, 1 on 8 |
| reference widths, one per record | `2+1` on **258**, `2+2` on 31, `3+2` on 19, `1+1` on 9 |
| its references inside the package's object tables | 355 of 361 |
| its count equal to the first array's | 319 of 361 |
| the same grammar started 1..8 bytes later: starts that parse / land on the defaults start | 811 / **1** |
| after it: 7 bytes / 9 / 8 / 6 | 201 / 68 / 23 / 8 |
| the count-plus-pairs array closes the record exactly | **219** (all 3-pair, 7-byte) |
| no list field at all / a single byte | 7 / 7 (0x03 on 5, 0x02 on 2) |
| 9 bytes left that the pairs grammar cannot close / the array running past the block | 67 / 7 |
| the 8 bytes in front of the list read as an `FName` (a name index, a counter <= 64) | **0 of 355** |

**So the 20 bytes are one array, not an array plus a config name.** A 2-record array whose references are two and
one bytes wide is `1 + 10 + 9 = 20` bytes, which is the mode, and `first_count` is 2 on those rows because the
second array's count is 2 -- not because a name array's count is. The alternative reading needs the 8 bytes in
front of the list to be the config `FName`; they are `01 00 00 00` followed by a per-class 32-bit value whose
median magnitude is 1.34e9, so the "number" would be a duplicate counter of a billion on every class. The first
int32 being 1 is not a name index either: index 1 is the same name in every package, and the value tracks nothing
in the name table.

**And the falsifier the placement note asked for does not fire the way it was written.** If the list were the
version-gated `+0x4f4` array, a class with that array empty would write its count (0x00) and nothing else -- one
byte. The corpus instead shows 7 rows with **no** list bytes, 7 with **one** byte holding `0x02`/`0x03` (a count
whose elements are missing), and the pairs array closing exactly on 219 rows. So the "empty array" case is not
present as a zero count, and the placement it was meant to test is confirmed only where the list parses (219), not
by an absence.

**Two residue populations are named rather than explained.** 67 rows end in 9 bytes whose leading `4` needs eight
more bytes and the pairs walk cannot close; on those the last field is not the count-plus-pairs shape (or the
second array's end is two bytes early). 7 rows have the second array ending past the defaults start, which is the
`-105` residue `inventory/class_record_array.csv` already reports from the other direction. Both are the next
question's, not this one's.

### The second array is a different member, not `+0x4d0` written twice

The test the section above named is a per-class comparison of the two arrays' reference lists, and
`inventory/class_record_gap.csv` now carries both sides (`first_refs`, `second_refs`, `ref_same`, `ref_same_set`,
`ref_shared`, `array_same`, and the class of every referenced object in `first_kinds` / `second_kinds`). On the 361
classes where the second array parses:

| measure | value |
|---|---|
| identical reference list, same order | **0** |
| the same values in another order | **0** |
| identical bytes over the whole array | **0** |
| no reference in common | **333** |
| sharing 1-10 references | 28 (1 on 10, 2 on 7, 3 on 6, 5 on 2, 4/9/10 on 1 each) |
| the same record count as the first array | 319 |
| its references resolving in the package's object tables | 355 of 361 |

**So the middle is a second member.** A copy would be byte-identical, and the closest row in the corpus shares ten
of its fifty-seven references. The count matching on 319 rows is not a copy either: those rows have the same element
grammar and the same length with not one value in common, which is what two *parallel* members look like.

**Both arrays reference classes, and the second one almost only classes.** Resolved through each package's own
tables:

| reference class | first array | second array | the package population |
|---|---|---|---|
| export of class `Class` | 413 of its 514 export refs (80.3%) | **738 of 789 (93.5%)** | 2,319 of 57,749 exports (4.0%) |
| import of class `Class` | 80 of 84 | **402 of 426 (94.4%)** | 1,157 of 7,500 imports (15.4%) |
| null | **735** | 233 | -- |
| property / function / other export | 101 | 51 | 95.4% of exports are not classes |
| outside the object tables | 40 | 8 | -- |

So the array the previous section measured is a list of *class references* at fifteen times the base rate of class
exports, and the array before it is one padded with nulls. The object tables are larger than the name tables in this
build (Engine: 17,980 exports against 10,406 names), so "resolves inside the tables" proves little on its own -- the
class enrichment is the part that does.

**And the reader does not pack it.** `0x0003a800`'s listing calls `FUN_0003d900` -- the 12-byte-record writer --
**once**, at `0x3a8a6`, and calls `FUN_0003d160` (count plus one- and two-byte pairs) twice, at `0x3a8af` and
`0x3a904`. A 20-byte middle is two 12-byte records, which `0x3d160`'s element grammar cannot produce, so the second
array is not written by this body: either the located array is not the member `+0x4d0` names, or a second member of
the same element type sits beside it and is packed elsewhere (a version-gated or load-side path). Naming it is the
next read, and it is a call census of `0x3d900` rather than a boot.

### Only one call site writes the record array, and its reader stages the second member under that call

`tools/record-writer-calls.py` (`make record-writer-calls`, `inventory/record_writer_calls.csv`, `--check` in
`make ci`) scans the image's executable sections for `E8 rel32` into the two record writers and decodes backwards to
the instruction that last wrote the register each call pushes, so a call site yields the member displacement the
callee is handed. A site is kept only when a forward decode from a start offset behind it lands exactly on the call
(`tools/vcall-receiver.py`'s boundary rule); every one of the eight candidates in the image passes that test.

| writer | sites | argument frames | member displacements |
|---|---|---|---|
| `FUN_0003d900` (12-byte records) | **1** -- `0x3a8a6` in `Core_UnClass` | 2 (`archive`, `&array`) | `+0x4d0`, leaving `+0x4dc` staged |
| `FUN_0003d160` (name array) | 7 | 2 on six, 1 on `0x172fe4` | `+0x4dc` (`0x3a8af`, staged), `+0x4f4` (`0x3a904`), `+0x70` (`0x500a1`), `+0x2ec`/`+0x2f8` (`0x22d53f`/`0x22d54c`); `0x66a93` takes its first argument from the stack |

**There is no second call site into the record writer.** The corpus's second array is 361 classes of the same
12-byte element grammar, and the image contains exactly one call that can produce one -- so "a second member of the
same element type, packed elsewhere" is refuted for this build: nowhere else is `FUN_0003d900` called at all.

**Which writer the middle belongs to is named; its encoding is not.** The two bodies agree on their `TArray`
skeleton -- reserve, count through `FUN_000637e0`, then one archive call per element -- and differ only in the
element: twelve bytes through archive slot `+0x18` for `0x3d900`, four bytes through the name slot `+0x1c` for
`0x3d160`. So the middle is handed to the `+0x4dc` writer and is not packed by a second record writer, which settles
the owner and leaves the shape open -- the subsection below measures the reader's own field order over the same gap
and it closes on no class at all.

**And the reader's second call is handed its member from under the first one.** At `0x3a8af` the instruction
immediately before the call is a single `PUSH EAX`, and `EAX` holds the value just returned by the call at
`0x3a8a6` (`FUN_0003d900` ends `return param_1`, i.e. it returns its archive argument). But the frame of a call is
not the run of pushes nearest it: at `0x3a897` the reader emits `LEA EDX,[ESI+0x4dc]` and pushes it into that run
**first**, `0x3d900` is handed two arguments (`archive`, `&+0x4d0`) and pops neither, and the caller's `ADD ESP,0x8`
at `0x3a8ab` skips exactly those two -- so `+0x4dc` is still on the stack when `0x3d160` runs, and that call is
handed `(archive, &+0x4dc)`. The tool reads frames that way for every site: the arity is the caller's own
`add esp,imm`, the arguments are the topmost dwords of the window's push accounting, and pushes a site leaves on the
stack are reported as *staged*. Two of the seven name-array sites take the archive from the previous call
(`0x3a8af` after `0x3d900`, `0x172fe4` after `0x172fe0`); `0x172fe4` is the only one that comes out one-argument,
and the window is what says so.

**Both bodies are the same shape, which is what makes the offsets readable.** `FUN_0003d900` reserves
`count*0xc` bytes through the archive's slot `+0x14`, writes the count with `FUN_000637e0`, then per 12-byte element
calls the archive's `+0x18` with the reference and its two dwords; `FUN_0003d160` reserves `count*4`, writes the
count the same way, then per element calls the archive's `+0x1c` -- the name method -- which is why its file elements
are one and two bytes. Both `return param_1`.

**The element width is one compact field, and it is measured from the archive, not from the array's bytes.**
`tools/archive-slots.py` (`make archive-slots`, `inventory/archive_slots.csv`, `--check` in `make ci`) finds every
table whose constructor stores this build's version pair (130 at `+4`, licensee 4 at `+0xc`) beside a vtable store
-- **six** in the image, so the pin alone does not name the object -- and decodes the slots the class-record writers
are handed. Only `0x004d40d0` has a *distinct* function at each: `+0x14` is `0x000ea7f0`, a `ret 8` no-op
(`Preload`); `+0x18` is `0x00038bc0`, which takes the element's reference, converts it through its proxied table
(`call [eax+0x24]`) and writes **one** compact field with `FUN_000637e0`; `+0x1c` is `0x00038bf0`, the same shape
through the *name* half of that table (`call [eax+0x20]`) and **one** compact field. So an `FName` on disk is one
index of 1-5 bytes, an array of `n` names spans **at most `1 + 5n` bytes**, and the earlier reading of the tail as
`(index, number)` pairs was a field list, not a width.

What this leaves open is the middle's **encoding**, not its owner: both grammars in the corpus fit its bytes -- a
compact count and twelve-byte records on 361 classes, a compact count and one-field names on 414 -- and neither parse
closes the gap at its position, so the field is written by `FUN_0003d160` in a shape the corpus's parse of
`FUN_0003d160` does not model. What the width *does* decide is which of the two the middle cannot be.

### The gap does not decompose in the reader's own field order either

`tools/class-record-gap.py` (`make class-record-gap`, `inventory/class_record_gap.csv`, `--check` in `make ci`)
re-reads each class's gap the way the reader writes it: the `+0x4dc` names (`FUN_0003d160`: a compact count and that
many **single-field** names, the width `tools/archive-slots.py` measures), the config name (two compact fields), then
the `+0x4f4` names, requiring the last to end exactly on the defaults start. **3 of 549 classes close**, against a
control of **14** for the same three-part parse started one to eight bytes later -- so chance, and the reading is
refused. The middle read as a count-plus-names list alone parses on 414 of 549, but consumes a median of 4 bytes and
leaves 23: what it matches is the leading count of the twelve-byte array, not a field of its own.

**And the width makes that arithmetic rather than an argument.** `nm_fits` records the bound: a name array of the
middle's own leading count can span at most `1 + 5*count` bytes, and the middle is wider than that on **400 of the
414** rows that parse -- on the disputed count of 2 (325 middles, median 27 bytes, bound 11) on **323 of 325**. So the
middle's leading byte is not a name count: two twelve-byte records of `3+3+3` and `2+8` span 19 bytes, which is the
only reading of the corpus's commonest middle that the measured element widths allow.

So two grammars fit the middle's bytes and neither accounts for its position, which is why naming the writer did not
name the field. The next instrument is the element width `FUN_0003d160` reserves -- `count*4` through archive slot
`+0x14`, then one name call per element -- read against the middle's leading byte, rather than another grammar over
the same bytes.

(A previous version of this section concluded that no site in the image is handed `+0x4dc`, from the pushes nearest
each call. The negative was the census's window, not the build.)

### The array at `+0x4c4` is initialised and destroyed, and no body fills it

The last note left `+0x4c4` as the candidate for the bytes the record's name parse does not consume: the
twelve-byte array does not end there, so the reader's 20-byte middle was re-read as the *other* member of
the group -- the 4-byte-element `TArray` between the 8-byte one (`+0x4b8`) and the record array (`+0x4d0`).
The way to test that is not another grammar over the same bytes: it is the array's head, which is three
dwords -- data at `+0x4c4`, count at `+0x4c8`, max at `+0x4cc` -- and a writer that fills it must write the
count or the max.

`tools/member-access-census.py` now takes `--artifact`, so the head is a census of its own
(`inventory/class_array_head_census.csv`, `make class-array-head`): **70 sites over the three dwords** --
29 at `+0x4c4` (8 load, 16 `lea`, 5 store), 30 at `+0x4c8` (20 load, 5 `lea`, 5 store) and 11 at `+0x4cc`
(6 load, 1 `lea`, 4 store) -- and **no site in the image indexes any of the three at any stride**.

**On a `Class` all nine stores into the head are the three constructors' own dword zero-fills.** Each constructor writes
all three dwords, and the register it writes is zeroed above them:

    ctor_Class    0x3ad98  mov [esi+0x4c4],edi   0x3ad9e  mov [esi+0x4c8],edi   0x3ada4  mov [esi+0x4cc],edi
    FUN_0003ae30  0x3aea1  mov [esi+0x4c4],edi   0x3aea7  mov [esi+0x4c8],edi   0x3aead  mov [esi+0x4cc],edi
    FUN_0003af60  0x3afda  mov [esi+0x4c4],edi   0x3afe0  mov [esi+0x4c8],edi   0x3afe6  mov [esi+0x4cc],edi

So the head is zeroed whole, and **no site in the sweep writes a non-zero count or a max**. The group's other
dword stores belong to other classes' own members at the same displacement: `0x001fd005  mov [ecx+0x4c4],imm`
in `FUN_001fc630` is a scalar (the body never touches `+0x4c8`/`+0x4cc`), and `0x0020be21` in
`ctor_SceneManager` and `0x00082f6a` in an unnamed body write their own `+0x4c4`/`+0x4cc`. The same is true of every `lea` of count or max
(`PlayerReplicationInfo`, `WarpZoneInfo`, `LevelInfo`, `Gui_UIXInteraction`): none of those bodies belongs to
a `Class`.

**And no `Class` body ever reads the array either.** The only two sites on `Class` are the region initialiser
`0x0003a670` (slot 10) at `0x3a675` -- `lea ecx,[esi+0x4c4]; push 0x4; call 0x1b846` -- and the destructor
`0x3dc60`, which hands the same address to `0x3d850`. The other fourteen `lea`s at `+0x4c4` are `Pawn` at slots
57/85/87/88/89/124, `Controller@30`, `SparkEmitter@47` or an unnamed body, and the eight loads are the same
classes' members at the same displacement -- a displacement is not a field.

**Two 4-byte arrays, two different initialisers.** `+0x4c4` is emptied through `0x1b846` and `+0x4dc` through
`0x1b820`, both with `push 0x4`; the element width is the same and the function is not, so the initialiser
identifies the element *type* rather than its size, and a width alone cannot name a `TArray` here. The
record array's own `0x1b833` (width `0xc`) is a third instantiation.

So the group's middle member is initialised empty, destroyed empty, and never appended in this image: it
cannot be the producer of the stream the record carries. The arithmetic agrees before the writer is even
asked -- a `TArray` of 4-byte elements streams `1 + 4n` bytes (5, 9, 13, 17, 21) and the middle is 27 bytes at
the mode, 20 as twelve-byte records, and 23 as the residue the name parse leaves; none of the three is of
that form. What the member is for stays unnamed; what it is not is the record's middle.

    make class-array-head
    python3 tools/member-access-census.py --xbe DUMP/default.xbe \
        --disp 0x4c4 --disp 0x4c8 --disp 0x4cc -o inventory --artifact class_array_head_census.csv
    python3 tools/member-access-census.py --xbe DUMP/default.xbe \
        --disp 0x4c4 --disp 0x4c8 --disp 0x4cc --report

**Correction, same day: the census said `byte` for a dword store.** `vcall-receiver`'s formatter fixed the
width of its `0x88`/`0x89` branch at `byte`, so `89 be c4 04 00 00` -- `mov dword ptr [esi+0x4c4],edi` --
printed as `mov byte [esi+0x4c4],edi`, and the first version of this section read the nine zero-fills as
one-byte writes. The width now comes from the opcode (`0x88` byte, `0x89` dword, `0xC6`/`0xC7` the same for
their immediate forms) and the census's `--check` drives all three, so it cannot regress quietly. Both
artifacts are regenerated and **only the `insn` column moved** -- 34 of 217 rows in the region sweep and 13 of
70 in the head, with no site, form, stride, displacement or row count changed -- so every count in the notes
stands. What the correction costs is a sentence: the head is zeroed whole by dword stores of a zeroed
register, which is what a `TArray` initialiser does, and the negative that matters is unchanged, because an
append would still show as a non-zero count or a read of `+0x4c8`/`+0x4cc` on a `Class`, and the sweep has
neither.

### The image names twenty member displacements in its own assert expressions

`tools/member-assert-names.py` (`make member-assert-names`, `inventory/member_assert_names.csv`, its `--check` in
`make ci`) sweeps every function body for one shape: `mov r32,[base+disp32]` -> `test r32,r32` -> `jcc` ->
`push line; push file; push expression; call 0x58a30` (the assert printer). When the pushed expression is a bare
identifier, the site names the member the function just checked -- the same route that named `ClassWithin` at
`+0x4b0`, generalised.

| displacement | name the image prints | sites |
|---|---|---|
| `+0x54` | `LinearFileStillOpen` | 2 |
| `+0x6c` | `ActorClass` | 1 |
| `+0x70` | `Input` | 1 |
| `+0x74` | `RenDev` | 2 |
| `+0x88` | `FileManager` | 1 |
| `+0x8c` | `Tracker` | 1 |
| `+0x90` | `DemoRecDriver` | 1 |
| `+0xa4` | `level` | 1 |
| `+0xac` | `RenderedSound` | 7 |
| `+0xbc` | `PTextureDetail` | 1 |
| `+0xc0` | `Mesh` | 1 |
| `+0x188` | `PendingFrame` | 1 |
| `+0x264` | `Brush` | 2 |
| `+0x348` | `kparams` | 1 |
| `+0x3e4` | `TerrainMap` | 4 |
| `+0x444` | `PlayerID` | 1 |
| `+0x4b0` | `ClassWithin` | 1 |
| `+0xea0` | `Viewport` | 3 |
| `+0x11e0` | `CurrentRemoteInfo` | 1 |
| `+0x20660` | `MemInit` | 1 |

34 sites, 20 displacement/name pairs, 20 displacements. **Fifteen of them are displacements the destructor map does
not place at all**, and for four of the five it does place, the map's owner is a different class (`+0x264` `Brush`
against the map's `ParticleEmitter`/`GUIEditBox`, `+0x3e4` `TerrainMap` against `FileLog`/`SkeletalMeshInstance`) --
which is the same lesson the `+0x4b0` census taught from the other side: an assert names the member *that function*
checked, and one displacement has as many meanings as there are classes with a member there.

Two limits, both structural. The shape test drops an expression that is not an identifier (`Index<=ArrayNum`,
`Defaults.Num()==GetPropertiesSize()`), so a check on a local or on a length is not reported rather than filtered by
a word list; and the base register is whatever the body loaded, which is not always `this` -- `+0x54`
`LinearFileStillOpen` sits in a file wrapper, not in a game class. Read the row's site before reading the name.

    make member-assert-names
    python3 tools/member-assert-names.py --xbe DUMP/default.xbe --report

### The middle's field order is the writer's, and the array is nineteen bytes further than the field table said

`UClass::Serialize` (`0x0003a800`) is straight-line code, so the order of its calls *is* the order of the record's
fields, and its listing and decompiled form agree on it:

| call | what it writes |
|---|---|
| `FUN_000394c0(archive)` | `UState::Serialize` -- `UStruct::Serialize` first, then 8 + 8 + 2 + 4 bytes |
| `FUN_000370e0(archive, +0x498)` | one dword (`Serialize(ptr,4)` on the proxy) |
| `FUN_000370e0(archive, +0x4a0 .. +0x4ac)` | four dwords -- 20 bytes |
| `FUN_0003d900(archive, +0x4d0)` | the 12-byte-record array: a compact count, then count x (compact reference, dword, dword) |
| `FUN_0003d160(archive, +0x4dc)` | `TArray<FName>`: a compact count, one compact field per element |
| `slot[+0x18](&+0x4b0)` | one object reference, `ClassWithin` |
| `slot[+0x1c](&+0x4b4)` | one `FName`, the config name |
| `FUN_0003d160(archive, +0x4f4)` | the second name array, version > `0x62` only |
| `slot[+0x98](...)` | the defaults buffer |

So the pair at `+0x4b0`/`+0x4b4` is written **after** both arrays, not in front of them -- which is why every reading
that put it first missed -- and the two slots have the same on-disk width. They are nine bytes each and differ only
in which proxy map they consult before the write:

    0x00038bc0   mov edx,[arg]; mov edx,[edx]; mov ecx,[ecx+0x4c]; mov eax,[ecx]; push edx
                 call [eax+0x24]                          ; proxy->MapObject(object)
                 push &value; push archive; call 0x637e0   ; one FCompactIndex
    0x00038bf0   the same, `call [eax+0x20]`              ; proxy->MapName(name)

Both end in `FUN_000637e0`, so the reference field and the name field are the *same* encoding and the same 1-5 byte
width: the open question "which encoding is the middle's first compact value" cannot be answered from a width at
all. The order of the writer's calls is what answers it.

`tools/class-record-order.py` (`make class-record-order`, `inventory/class_record_order.csv`, its `--check` in
`make ci`) therefore walks that order and asks, per class, where it lands against the defaults block start -- from
`prefix_end + 42` (22 bytes of `UState::Serialize` plus the five dwords) and then from each of the next 32 byte
positions, because the field table's own sum is the thing on trial:

| measure | result |
|---|---|
| closes at the field table's own offset (`prefix_end + 42`) | **0 of 2,204** |
| closes somewhere in `+0..+31` | **1,626 (73.8%)** |
| closes at `+19` (`prefix_end + 61`) | **732**, and **700 of them at exactly one offset** |
| the next two offsets | `+20` 518, `+21` 232 -- a 1-3 byte spread |
| controls: any offset closing for the pair-in-front reading | 167 |
| controls: the two name arrays adjacent | 33 |
| controls: the version-gated last array dropped | 37 |

On the 732 classes that close at `+19` the fields are coherent: the array's count is **2 on 708** (the "middle's
leading 2" the name-width work measured), the first name array's count is 3 on 484, the second is 0 on 619 and 1 on
95, the reference is **one byte on all 732** and inside the package's tables on all of them (null on 2), and the
config name is a valid name index on all 732 (one byte on 641, two on 91). The middle's median is 88 bytes.

The negative is the offset itself. The writers the body names between the five dwords and `FUN_0003d900` account
for exactly 42 bytes -- `0x371d0` pushes `8` and is called twice, `0x37080` pushes `2`, `0x370e0` pushes `4`, and
the five dwords are 20 -- so the 19 to 21 bytes in front of the array belong to `UStruct::Serialize` (`0x000392b0`),
whose output the record's prefix page cuts short at its three documented fields. **Measured 2026-11-13 and half right**
(`--gap-sweep`, at the end of this page): the gap's last sixteen bytes are zero on 1,481 of the 1,482 modal classes, so
the four dwords' *width* is right and their *position* is not, and the three to five compact bytes in front of them are
written by no body in the chain. The residue of 20 bytes the
name-width work measured from the array's tail is that same gap seen from the other side.

    make class-record-order
    python3 tools/class-record-order.py --dump DUMP --class AmbientCreatures.HerdAnimal
    python3 tools/class-record-order.py --report

### The gap's last sixteen bytes are zero, and three to five compact values sit in front of them

`tools/class-record-order.py --gap-sweep` (`make class-record-gap-front`, `inventory/class_record_gap_front.csv`, its
`--check` in `make ci`) reads the bytes the walk steps over instead of assuming them. Every class whose writer order
closes has a gap, and on **1,482 of the 1,626 closing classes it is 19, 20 or 21 bytes** -- 732 / 518 / 232, the same
three offsets the walk measures. What those bytes hold is the same shape everywhere:

| measure | result |
|---|---|
| the gap's **last 16 bytes are zero** | **1,481 of 1,482** at the modal lengths (1,494 of all 1,626) |
| the gap's last non-zero byte | index **0** (528), **2** (323), **1** (299), **3** (161) -- never past **3** |
| the bytes in front of that run | 3 to 5 bytes, **95 distinct** over the 1,482 |
| those bytes read as `FCompactIndex` | **every one parses**, 0 overruns on all 1,626 |
| the most common fronts | `2 0 0` (250), `0 0 0` (121), `16 0 0` (112), `54 16 32 0` (104), `16 32 0` (81) |

So the gap is one shape and not a parse: **three to five compact values, then sixteen zero bytes, then the array's
count.** The sixteen are exactly the four dwords `UStruct::Serialize` writes between `FriendlyName` and its script --
`0x370e0` at `+0x5c`, two `0x37110` at `+0x58` and `+0x54`, and the script size at `+0x4c` -- and their value is zero
on every cooked class, which is why nothing in the payload distinguishes them and why every reading that looked for a
non-zero field there found none. The compact run in front of them is the only content in the gap, and no body in the
chain (`0x38dc0`, `0x392b0`, `0x394c0`, `0x3a800`) writes one at that place.

**What that settles and what it does not.** The arithmetic now closes: `prefix_end + 42` (22 state bytes and the five
dwords) `+ 3..5` (the compact run) `+ 16` (the zero dwords) is `prefix_end + 61..63`, which is where the walk lands,
and it explains the 1-3 byte spread as the front's own width rather than as a mis-measured array. It leaves two
questions, and they are the same question: whether the zero run *is* the struct's tail (its width and its position
both fit, but the body writes those dwords before `UState`'s 22 bytes, not after the five dwords), or whether the tail
is absent from the cooked stream the way `FriendlyName`, the source line and the text position already are -- and in
either case which field the compact run is.

**What would settle it.** A live `UClass` from a link, read at `+0x5c`/`+0x58`/`+0x54`/`+0x4c`: if the tail is in the
stream those four members are non-zero on a class whose source line is not (the assert at `0x397cc` carries
`UnClass.cpp:0x2e2`, so at least one class has a line). Failing that, the compact run can be tested against the class's
own tables the way the pair at `+0x4b0`/`+0x4b4` was -- a front of three to five bytes cannot be a count plus
elements, so the candidates are one `FName` (two fields) plus one or two references, or one reference plus one `FName`;
the artifact carries each front's values and the class's own table sizes, so the test is a query, not a boot.

    make class-record-gap-front
    python3 tools/class-record-order.py --dump DUMP --gap-sweep -o inventory
    python3 tools/class-record-order.py --gap-report

### The shape at `prefix + 42` survives the walk, so the front is a record field

`tools/class-record-front.py` measured what the 3-5 bytes in front of the sixteen zero dwords *can* be, and every
reading it passed was a small integer's base rate; that left whether those bytes are a field of the record or an
artefact of the offset the closing walk happened to choose. The population that answers it is the **578 located rows
the walk does not close on** (`inventory/class_record_array_head.csv` with an empty `arr_count`): they carry no
gap-census row by construction, so anything found at `prefix + 42` there cannot have come from the walk.

`tools/class-record-open-front.py` (`make class-record-open-front`, `inventory/class_record_open_front.csv`, its
`--check` in `make ci`) reads that shape -- **3-5 compact values, sixteen zero bytes, then the count** -- on every
located row, and anchors it on the zero run's **end**. Anchoring on the start is the trap: a front whose last byte is
`00` merges into the run, and a start-anchored search then reports a four-byte front as one byte
(`AmbientCreatureManager`, front `02 00 00`, reads as a one-byte `02`). Candidates are the modal 19/20/21 only; when
two pass, the **widest** is the front's real width, because the shorter pass is the front's own trailing zero.

| population | rows | shape at `prefix + 42` | local base rate |
|---|---|---|---|
| closing, with the gap census as the control | 1,626 | **1,607 (98.8%)** | 0.000 |
| ... of those, gap equal to the census length | 1,607 | **1,473 (91.7%)** | -- |
| **open, the walk does not close** | 578 | **515 (89.1%)** | 0.000 |
| all located rows | 2,204 | 2,122 (96.3%) | -- |

The open population's gaps are 20 (223), 19 (151) and 21 (136), plus five at the off-modal 18 -- the same width range
as the closing rows and the same zero run; its fronts are 78 distinct values against the closing rows 93, 52 of them
shared (the commonest open fronts are `12 00 00 00`, `00 00 00`, `32 30 00 00`, `02 00 00`). **The base rate is the
control that carries the claim**: the same test at 24 offsets just past the head, in the same payload, never passes
on either population, so a sixteen-zero run ending at exactly one of three modal positions behind a parseable front is
a property of that position and not of the payload being zero-rich.

**What that settles and what it does not.** The front is a field the record writes, and the open rows fail somewhere
after the array head. From the head this shape gives, the element walk lands past the block start on 361 of the 515
(residue median **4** bytes, 119 within 3, 245 within 9), inside an element on 114, and on no block at all on 40 --
block starts at or inside the head, the locator's own false positives (the closing rows carry 4 too, and those are
excluded from the walk rather than scored). Against the shipped head, which reads the front as the count, the same
rows miss the block start by a median of hundreds of bytes. What the shape does **not** settle is the front's
identity, which stays open in `docs/UNKNOWNS.md` U17 with the writer-side read (`UStruct::Serialize`, `0x000392b0`) as
the route, nor the 63 open rows it does not answer at all.

    make class-record-open-front
    python3 tools/class-record-open-front.py --dump DUMP --sweep -o inventory
    python3 tools/class-record-open-front.py --report

### The 1-9 bytes the element grid stops short on are the closing group, and the block start is inside it

The open-front census left one population named: on the rows the walk does not close, the element grid ends **1 to 9
bytes before** the located defaults-block start on **245** of the 515 rows that carry the gap shape. On the closing
rows those bytes are the record's closing group -- `UClass::Serialize` (`0x0003a800`) writes a names array, one
reference, the config name and a second names array after the record array and before the defaults branch
(`tools/class-tail-run.py`, `inventory/class_tail_run.csv`). Anchored to the block start that grammar closes on **0**
of the 245, which is what a start placed *inside* the group would produce.

`tools/class-record-open-tail.py` (`make class-record-open-tail`, `inventory/class_record_open_tail.csv`, its
`--check` in `make ci`) therefore stops anchoring: it parses the same four fields from the element end with **no
required end** -- bounded, every index inside the name table, one compact 1-5 bytes -- and reports where the group
ends as a difference from the located start (`overlap`), not as a condition on it.

| population | rows walked | group parses from the element end | group ends exactly on the block start |
|---|---|---|---|
| closing (the control) | 1,603 | **1,603 (100%)** | **1,596 (99.6%)** |
| open, the walk does not close | 475 | **475 (100%)** | 0 |
| ... the 1-9-byte-short population | 245 | **245 (100%)** | 0 |

On the closing rows the free parse reproduces the anchored one: its end is the located block start on 1,596 of 1,603,
and the group lengths are the residue distribution the anchored parse already measured (7 on 618, 8 on 302, 9 on 302,
6 on 241). So the instrument measures the object `class-tail-run.py` found, with the anchor removed. On the 245 rows
it always ends **past** the located start -- overlap 4 (94), 3 (80), 6 (43), 5 (15), 7 (5), 10 (5), 2 (2), 9 (1) --
never at it and never short of it, so the located start falls inside the group's names array.

**Which of the two starts is the defaults block is decided by the property stream, not by the group.** The block
start was chosen by `tools/class-defaults.py` on exactly this test -- a start whose tag stream runs to the export end
and names properties the class declares -- so the same test scores both candidates here. On the 245 rows the located
start's first tag is a package or class name read as a tag (`Engine`, `Core`, `Lighting`), and it is fully declared on
**2 of 245 (0.8%)** with a mean declared fraction of 0.237; the group's end opens a declared property (`bActive`,
`bHideHUD`) and is fully declared on **126 of the 207** rows where its stream is non-empty (60.9%), mean 0.609. The
block begins at the group's end; the earlier start is inside the group.

**The control that is not a control.** The unanchored parse is permissive on its own: the same parse at +-1..6 bytes
around the element end succeeds at 3.07 of 12 offsets on the closing rows and 3.34 on the open ones, so *group exists*
is not evidence at one position. What is evidence is the pair -- the end landing on a declared tag stream, and the
anchored control closing on 99.6% of the closing population.

**What is still open.** The oracle is silent on 119 of the 245 (no candidate fully declared, mostly because
`inventory/class_properties.csv` does not carry every declared name), though their geometry is the same; and the block
start itself is wrong on them, so any count that reads `class_defaults_per_class.csv`'s `block_start` as the record's
boundary inherits a start 2-10 bytes inside the group on these rows. The next measurement is upstream of this tool:
teach the block-start chooser to reject a candidate that falls inside a record's closing group -- the group's start and
end are now committed per row -- and re-run it on the 245.

### The 63 rows that answer nothing at `prefix + 42` carry the shape 10 to 48 bytes later

The open-front census answered 515 of the 578 open rows and left 63 with no shape at `prefix + 42` at all. Two
explanations survive it -- the record's prefix is a different width on those rows, or there is no record array there
to find -- and a window scan separates them without a boot. `tools/class-record-no-shape.py`
(`make class-record-no-shape`, `inventory/class_record_no_shape.csv`, `--check` in `make ci`) runs the same test at
every offset in `prefix + 42 - 16 .. + 48` and reports each surviving head with the element walk from it.

| population | rows | no shape anywhere in the window | shape at a distant offset | walk from it lands 0-9 bytes before the block |
|---|---|---|---|---|
| open, no shape at `prefix + 42` | 63 | **21 (33.3%)** | **42** | **35 of the 42** |
| control: open, shape at `prefix + 42` | 515 | 0 | 188 | -- |

The 42 answer at `k` = 11 (11 rows), 20 (7), 10 (4), 40 (4), 19 (2), 30 (2), 29 (2), 21 (2), 12 (2), 48 (1) -- tens of
bytes, not the one-to-three the controls show -- and from that head the element walk lands past the located block start
on 38 (residue median **7**, within 9 bytes on 35, within 3 on only 3). So those rows are not shapeless: their array
head sits later in the record than the field table's position, and the shape is the same shape.

**The control that refuses the easy reading.** The nearest hits on the 515 control rows are at `k` = -1 (515 rows),
+1 (510), -2 (492), -3 (457): a front whose leading bytes are zero merges backwards into the sixteen-zero run, so the
same shape reports two or three bytes early and the rows have to be compared by **count position**, not by offset. And
188 of the 515 control rows carry some hit at `|k| > 4`, so a distant shape on its own is not evidence for the 63 --
what is reported is the walk's landing, not the offset.

**What is still open.** The 21 rows with no shape anywhere in the 65-offset window (a different record shape, or a
payload that is not a class record), and the 42 with a late head, where the offset itself is the answer but the writer
side that would say why the prefix is wider -- `UStruct::Serialize`, `0x000392b0` -- is still unread.

### The array's head sits after the sixteen zero bytes, and there its end is the block start

The first version of this test read the `+0x4d0` array at `prefix_end + 42` -- `UStruct`'s prefix, then
`UState::Serialize`'s 22 bytes and five dwords -- and reported 297 heads that passed both table tests with the block
start exactly twenty bytes past their end. That twenty is the reading refuting itself. This page's gap census had
already measured where the array begins: **19 to 21 bytes** past the field table's position, after sixteen zero bytes
and a three to five byte compact front (*The gap's last sixteen bytes are zero*). So the "count" at `+42` was the
front's first value, and the twenty-byte "margin" was that front and the zero run read back as an array.

The offset is now a join rather than an assumption. `tools/class-record-bound.py --position` reads the compact value at
`prefix_end + 42 + gap_len`, with the gap length the gap census committed per class, and compares it with that census's
`arr_count`:

| measure | value |
|---|---|
| classes in both artifacts | **1,626** |
| the value at `prefix + 42 + gap_len` equals the census `arr_count` | **1,626** (100%) |
| classes whose value at `prefix + 42` is a front value | 2,189 |
| ... of the 297 the `+42` reading credited, where that value equals the census's first front value | **234** of the 234 with a census row |

The last row is why the wrong reading looked right: the front's first value equals the array's own count on 232 of
those rows -- both are small, and `2` is the commonest of each -- so the number was right and the position was 19 to 21
bytes early. The same test now finds the head where the writer's rule puts it, the count immediately after sixteen
zero bytes, and credits it only when every element is an object reference inside the package's tables and every index
of both name arrays is inside the name table. **4 of the 1,626** pass, because a large array rarely has every
reference inside the tables:

| class | middle | head at | count | record ends | block start |
|---|---:|---:|---:|---:|---:|
| `Engine.PlayerInput` | 212 | **63** | 15 | 217 | 217 |
| `Engine.AIMsgManager` | 1,907 | **62** | 204 | 1,912 | 1,912 |
| `Engine.AdminBase` | 719 | **63** | 69 | 724 | 724 |
| `Engine.XBoxPlayerInput` | 117 | **62** | 5 | 123 | 123 |

**On all four the record ends exactly on the defaults block -- margin 0, no residue.** The field order closes to the
byte once the head is read where the stream carries it: `prefix + 42 + front + 16 + count + count x 9 + two name
arrays = block start`, on counts of 5, 15, 69 and 204 alike.

**The 32 locator rows the first version found stand, and they are independent of the array's position.** 32 of the
2,204 located rows leave fewer than 42 bytes between the prefix and the block -- less than `UClass::Serialize` writes
unconditionally -- 15 of them at offset 1 or before (middle -4) on payloads under 120 bytes, and `Engine.KBSJoint` and
`StargateContent.CTFGateTeleporter` are the two whose located middle is 41. A record-field precondition is a cheaper
locator test than the tag walk for that tail, and nothing about it depends on where the array begins.

**What it settles, for U18.** Where a head can be credited the record's own end *is* the defaults block start, so no
located start sits inside the record array and there is no residue between them to explain. What the earlier version
reported as a 20-byte gap is a field *in front of* the array -- the front the gap census measures -- not a residue
behind it. The two menu pages' 12-byte values stay open, and the route U18 names is unchanged:
`UDelegateProperty`'s own value serialiser, or a second package that ships the property with a value short enough to
read directly.

    make class-record-bound
    python3 tools/class-record-bound.py --dump DUMP --position
### The 32 starts the record forbids are all at their payload's end

`tools/class-record-bound.py --report` ends on a row it could not explain: **32** of the 2,204 located classes leave
fewer than 42 bytes between the prefix and the defaults block, and `UClass::Serialize` writes 22 `UState` bytes and five
dwords unconditionally. The starts are room **2** on 7 (all `Engine`), room **-4** on 15 (the start sits *inside* the
prefix) and room **41** on 10, and the payloads are 95-115 bytes -- small enough that the record's own fields cannot fit
in front of such a start. On all 32 the tag walk's parse is a **two-property** block.

`tools/class-record-room.py` (`make class-record-room`, `inventory/class_record_room.csv`, its `--check` in `make ci`)
re-locates them by the cheap question instead of the walk's tail: a block is a run of tagged properties, so a
*candidate* start is any offset where one parses and ends at the payload's end, and the record then bounds the
candidate from below. `tools/class-defaults.py`'s `candidates()` already refuses a parse that leaves more than 64 bytes
behind it; the room filter is the second half. Candidates are plentiful -- **8 to 14 parses per payload**, median 9 --
so a start counts as evidence only when its tags name something the class declares (`inventory/class_properties.csv`;
the 32 declare 5 to 586 names):

| verdict | rows | what the payload says |
|---|---:|---|
| re-located: a declared tag parses at a clearing start | **8** | room 63, 70 or 91 |
| an empty block at the payload's last byte | **24** | no parse anywhere names a declared property |

**Four of the eight close to the byte, and they are the ones the walk was furthest from.** The `Engine.SubAction*`
classes (`CameraShake`, `FOV`, `SceneSpeed`, `Trigger`) get best start **97** at room **91**: one tag naming `Desc`,
trailer **0**, whose value is the literal string the payload ends on (`FOV`, `Shake`, `Scene Speed`, `Trigger`). The
walk's own start there is **8**, two bytes past the prefix, on a two-property parse whose first tag is `Desc` as well.
The room filter is what separates them -- 8 - 6 = 2 bytes cannot hold the record's 42 -- and it separates them without
reading a single value.

The other four re-locate to a declared tag but leave a tail: `Engine.KBSJoint` room 63, trailer 21 (`bHidden`);
`StargateContent.HaakenMK2Weapon_Bot` room 63, trailer 24 (`FidgetAnims`); `StargateContent.P90Torch` room 63, trailer
25 (`RunMovementAnims`); `StargateVehicles.AttachedDeathGliderTurretController` room 70, trailer 14 (`KParams`). A tag
that *begins* there and a block that *tiles* the payload are two claims, and only the first is measured here.

**The 24 that are not re-located are still bounded, and an independent artifact agrees with them.** Each declares 5 to
586 properties and its payload names **none** of them in any of its 7-13 parses, so the discriminator that works on the
8 finds nothing here. What remains is the payload's last byte -- the `None` terminator, an **empty** block, which
clears the record with room 90-102 on all 24 -- and seven of the 24 are `*VoicePack` classes that
`inventory/voice_sets.csv` already marks `silent`, the family `docs/systems/AUDIO.md` describes as 16 of 25 payloads
that are the class record alone. The empty verdict is recorded as the weaker one: it says the class sets no default of
its own, not that a tag was found.

**What it settles.** No start these 32 rows report can be a block start, and where the record's fields are respected
the payload's own shape puts the block at its end -- four of them tiling it exactly. **What is still open** is the same
question on the other side of the tail: whether the four leftover tails are a tag followed by a second table, and
whether the 24 ship a bare terminator or a one-tag block whose name the cooked stream does not declare. The route is
`tools/class-defaults.py`'s `single_declared_block` over a class whose one declared property is known from its script
text, which is the declaration the tool already reads.

    make class-record-room
    python3 tools/class-record-room.py --dump DUMP --sweep -o inventory
    python3 tools/class-record-room.py --report

### The record's own tail: the block starts 6-9 bytes past its last unit, and four re-located starts are inside it

`tools/class-record-room.py` left two questions on its 32 rows: whether the four it re-located to a declared tag (with
a 14-25 byte tail) are a tag followed by a second table, and whether the 24 it called empty carry a bare terminator.
Both are questions about the bytes between the record and the payload's end, and four classes in the corpus settle
them from the **block** side, because their block is known rather than chosen: `Engine.SubActionCameraShake`,
`SubActionFOV`, `SubActionSceneSpeed` and `SubActionTrigger` each declare one property and set it, so the single
declared tag that tiles the payload exactly *is* the block (`class-defaults.py`'s `single_declared_block`, all four at
start **97**, type `Str`, value the literal string the payload ends on).

Read forward from the record's last streamed field, every one of the 32 payloads ends in a run of units and a short
closing run:

    <name index> 01 00 00 00 <4 bytes>          ... one or more units
    02 01 02 83 03 00 <Desc tag> <None>         the anchors: closing run, then the block

`tools/class-tail-units.py` (`make class-tail-units`, `inventory/class_tail_units.csv`) reports three things per row,
none of them assuming a tag grammar: the unit offsets, the bytes after the last unit, and whether a declared tag tiles.
Two of those are measurements over the whole corpus:

| distance, last unit -> block start | rows | share |
|---|---:|---:|
| 6 bytes | 169 | 7.7% |
| 7 bytes | 449 | 20.4% |
| 8 bytes | 261 | 11.8% |
| 9 bytes | 185 | 8.4% |
| within 12 bytes | 1,318 | 59.8% |
| no unit in front of the block | 65 | 2.9% |
| further back | 821 | 37.3% |

The mode is **7**, the anchors are all **6**, and the shape of that peak is the point: a block start lands a handful of
bytes past the last unit on most classes because the closing run is the record's, and its length follows the class's
name arrays rather than being one constant. A unit is therefore a landmark, not a guess: on the four anchors the block
begins six bytes past it, and on the 32 rows the last unit is at 77-83 of a 95-115 byte payload.

**All four re-located starts are inside a unit.** `Engine.KBSJoint` claims 69 and its units are at 70 and 80 -- one
byte before the first unit's flag dword; `StargateContent.HaakenMK2Weapon_Bot` 68 and `P90Torch` 68 have units at 69
and 79, the same relation; `StargateVehicles.AttachedDeathGliderTurretController` claims 75 with units at 68 and 78,
and 75 is inside the earlier unit's four trailing bytes. In each case the parse that accepted the start read a unit's
`01 00 00 00` flag dword as a property value, and the property name it landed on (`bHidden`, `FidgetAnims`,
`RunMovementAnims`, `KParams`) is a coincidence of the name table at that index. A defaults block cannot begin inside
the record, and what follows each claimed start is the closing run plus a terminator -- the same bytes the empty
payloads end on -- not a second tag table.

**The 24 stand as a negative, with the anchors as the control.** On every one of them no single declared tag tiles the
payload at any offset, and that is exactly the test that finds all four anchors; their record ends 1 to 2 bytes short
of the payload's end where the anchors' leaves a ten-byte tag. The verdict is therefore `no-declared-tag` and not
`empty`: the payload does not carry a tag the class declares, which is the strongest statement it supports. Seven of
the 24 are `*VoicePack` classes `inventory/voice_sets.csv` already marks `silent`.

| verdict | rows | what it says |
|---|---:|---|
| `block` | 4 | a declared tag tiles the payload: the `SubAction*` anchors |
| `record` | 4 | the claimed start is inside a record unit |
| `no-declared-tag` | 24 | no declared tag tiles anywhere in the payload |

    make class-tail-units
    python3 tools/class-tail-units.py --dump DUMP --sweep -o inventory
    python3 tools/class-tail-units.py --report
    python3 tools/class-tail-units.py --dump DUMP --join

### The closing run is the writers group: a names array, an import reference, a config name and an empty list

`tools/class-tail-units.py` measured the bytes between the record's last unit and the block (6-9 bytes on
**1,180 of 2,204** located rows) and left their grammar open, naming a join against
`inventory/class_record_gap.csv`'s array counts. `UClass::Serialize` writes four fields there, and its own listing
gives the order and the versions (`~/sg1/work/module-export/Core/0003a800_Core_UnClass.c`):

    0x3a8a6  CALL 0x0003d900   the record array: &+0x4d0 into &+0x4dc, returns the archive
    0x3a8af  CALL 0x0003d160   &+0x4dc written as names: a compact count, then ONE compact index per element
    0x3a8ba  CMP [Ar+4],0x3e   version >= 62:  &+0x4b0 through slot +0x18, then &+0x4b4 through slot +0x1c
    0x3a8f6  CMP [Ar+4],0x63   version >= 99:  &+0x4f4 through 0x0003d160 (count + one compact per element)

`inventory/archive_slots.csv` measures both name slots as **one compact field** on the cooking archive
(`0x4d40d0`, proxy slots `+0x24` and `+0x20`), so the run is the whole group, in this order:

    <count> <name index> ...        +0x4dc: a names array
    <reference>                     an object reference, negative for an import slot
    <name>                          the config name index
    <count> <name index> ...        +0x4f4: written only at version >= 99, and empty on most classes here

`tools/class-tail-run.py` (`make class-tail-run`, `inventory/class_tail_run.csv`) parses the run against exactly
that order and accepts a row only when the parse **ends on the block start** and every name index is inside the
package's name table -- so the anchor that made the six bytes look ungrammatical is not a fudge factor:

| population | rows | parse as names-ref-name-list |
|---|---:|---:|
| every payload with a unit before a located block | 2,139 | 984 (46.0%) |
| the same, run no longer than 12 bytes (the unit table's population) | 1,318 | 953 (72.3%) |

Three measurements follow, and the first is the one the earlier reading missed:

* **the reference field is negative on 981 of 984 parses (99.7%)**, and every one resolves inside the import
  table (`|ref| <=` the package's import count); the values are `-6` on 437, `-3` on 223, `-4` on 135, `-9` on 84,
  `-7` on 72, `-2` on 18, `-16` on 7 and positive on 3. That single byte is why six bytes refused to parse as
  names: `83` is a compact **-3**, not a name index, and no reading of a name array can hold it.
* **the config name index is inside the name table on all 984**, so the two fields after the array are a reference
  and a name, in the order the listing has them.
* **the `+0x4f4` array is empty on 801 of 984 rows** (count 0; 1 on 126, 6 on 33, 4 on 11, 2 on 11, 3 on 2). The
  version-gated array is written, and it is a one-byte zero on four fifths of the corpus -- which is also why the
  24 `no-declared-tag` payloads of the room table end 1 to 2 bytes from the payload end: those bytes are this
  count and the empty list behind it, not a tag.

Parses by run length: 6 bytes 149 of 169, 7 bytes 413 of 449, 8 bytes 202 of 261, 9 bytes 163 of 185, 5 bytes 3 of
33, 10-12 bytes 23 of 55. What does not parse is bounded and named rather than hidden: 52 runs are **empty**
(the last unit ends exactly on the block, so the group is not in the payload), and the commonest refusals are
`02` (21), `4502010284` (17) and `450201028403010e` (12) -- payloads whose located block swallows part or all of
the group.

**The join is a negative, and it is the useful one.** 294 of the 412 rows that also carry a
`class_record_gap.csv` row parse here, and that census reports `nm_mid_count = 2` on **all 294** while the count
this tool reads for the same array is 3 on 217 and 4 on 69. A count that is constant across every overlapping row
is not a count of these arrays: the census starts its mid parse at `array_end`, which sits inside the record's own
unit stream, so its `nm_*` columns describe a position the writer never writes an array at. The tail measurement
above does not depend on it -- it lands on the block start from the other side -- but a column that reads the same
value on 294 rows should be re-derived before it is quoted again.

The run is therefore **not** the tail of the version-gated `+0x4f4` array and not a field of its own: it is the
whole four-field group, and the part that looked like an unexplained byte is the archive's reference slot writing
an import index negative.

### The gap census's array tail is not the writers, and its mid count is a unit body

`inventory/class_record_gap.csv` locates an array from the payload's other side and starts its `nm_*` parse at that
array's tail, but the record's closing group starts at `last_unit + 8`, and those are not the same byte.
`tools/class-tail-run.py` carries the join as three columns (`gap_array_end`, `gap_off`, `gap_pos`) and prints it,
and on the **545** rows both artifacts carry the census's tail is never the group's start:

| where `array_end` sits | rows | parse the group here |
|---|---:|---:|
| before the record's last unit | 402 | 294 |
| past the block start, inside the defaults buffer | 93 | 36 |
| inside the closing run | 49 | 0 |
| exactly at the block start | 1 | 0 |
| at the group start (`last_unit + 8`) | 0 | 0 |

The offsets are a narrow mode, not a spread: **-12 on 262** of the 402, -13 on 34, -14 on 19, -11 on 10. Twelve
bytes before the last unit's flag dword is four bytes inside the body of the unit ahead of it, when the units are
eight bytes apart -- where an array tail lands if a run of unit bytes is read as the array. The 93 rows past the
block start carry no `nm_*` at all: their located array lies inside the defaults buffer, which is the locator's own
question rather than the run's.

On the 294 rows whose census tail is before the last unit *and* whose group parses, that census reads
`nm_mid_count = 2` on all 294 and `nm_shape = open` on all 294 -- the middle is not merely a wrong count, its split
never closes on the block, and `nm_exact` closes on **0 of the 330** overlapping parses. Read at the position the
writer uses, the same 294 rows give

* the `+0x4dc` count **3 on 217 and 4 on 69** (2 on 6, 5 on 2);
* the config name inside the name table on **294 of 294**, and the reference an import slot on all 294;
* the `+0x4f4` list **empty on 280** (count 6 on 10, 4 on 4).

So the census's `nm_*` block measures the record's **unit stream** wherever its located tail falls before the last
unit -- 402 of 545 rows, 294 of the 330 that parse -- and must not be quoted as the record's arrays; the counts to
quote are the group's columns. `make class-tail-run` regenerates the artifact and prints the join, and
`tools/class-record-gap.py --report` says which of its own starts fall there.

### The 95 rows whose located array ends past the block start, and the head census that does not separate them

`inventory/class_record_array.csv` locates the record array from the prefix: `array_end` is the bare array started
at `prefix_end + 42`, and on **95 of the 549** rows that carry both ends the array ends **past** the block start --
the walk swallowed the buffer, and `class_record_gap.csv` starts its own parse from there: **135 of its 549 rows
carry no `nm_*` at all**, and all 95 of these are among them. `tools/class-record-overshoot.py` (`make class-record-overshoot`,
`inventory/class_record_overshoot.csv`) joins those rows to the locator's head census and to the record side.

| measure | overshoot rows | the 454 that land at or before the block |
|---|---:|---:|
| rows | 95 (17.3% of 549) | 454 |
| a free head closes exactly on the block start | **55** (57.9%) | **316** (69.6%) |
| no head closes there | 40 | 138 |

The overshoot is not shallow: mode **105-109** bytes, deepest 281, median **30.4%** of the payload. But the head
census -- every offset in `[prefix_end, block_start)` whose array-plus-tail walk lands on the block start -- does
**not** single these rows out. They carry a head *less* often than the rows that land correctly, so "another head
in the payload fixes them" is refused: a head is a property of the payload (371 of 549 located rows have one), not
a discriminator of the overshoot. Where the overshoot rows do have one it sits at `prefix + 61` on 32 and `+62` on
14, its array count is 2 on 33, its tail 2 names on 35, and its references resolve inside the tables on 50 of 55 --
the same shape as the head the correctly-landing rows use.

**The record side has a say on 36 of them.** 93 of the 95 carry a `class_tail_run.csv` row and 36 parse the
closing group there; on those the run is **6 bytes on 31** (the length of the four `Engine.SubAction*` anchors)
and it ends on the block start, so the block start is confirmed from the record on exactly the rows the located
array overshoots it. What the 95 share is the located **tail**, not the payload: neither the block start nor the
head census moves with them.


### The defaults stream closes on the block start, so on 92 of the 95 the located tail is the only thing in doubt

The section above left **40** rows where all three witnesses of the block start are silent: the documented array end
overshoots, no free head in the payload closes on the start, and the record-side closing group does not parse.
The witness left is the block's own grammar. `tools/class-overshoot-stream.py` (`make class-overshoot-stream`,
`inventory/class_overshoot_stream.csv`) re-reads all 95 overshoot rows from the dump and runs `prop-stream.read_block`
at `block_start`: a property stream either terminates on the payload's last byte or it does not. The corpus
census below puts a number on how strong that is: a close is what **95.9%** of the located starts do, so on these rows
the close is a non-refutation and the name column is the discriminator.

| measure | the 40 with no head | the 55 with a head |
|---|---:|---:|
| the stream from `block_start` terminates on the payload's last byte | **37** (92.5%) | **55** (100%) |
| it terminates short of it | 3 | 0 |

**A close on its own is a 33% test, so it is scored against the position.** The same parse at `block_start + 1 .. + 4`
closes on **125 of the 380** later starts (32.9%), and on **70 of the 95** rows at least one neighbour closes -- a tag
stream can re-synchronise onto the buffer's end. What separates the block start from those neighbours is what the tags
*name*: of the **44** rows whose class declares properties, the parse at `block_start` names strictly more declared
names than any closing neighbour on **41**, the same number on **3** (`StargateContent.HaakenWeaponShield` 11 against
11, `StargateContent.Reetou` 105 against 105, `UWindow.UWindowHSplitter` 1 against 1) and fewer on **none**. The other
38 rows parse tags whose names the property inventory does not carry (29 of them `GUI.STY_*` styles), so on those the
close is the only evidence and it is read as such.

**The three rows that stop short are the only ones where the block start itself is unsupported**, and they stop by
less than the locator's own trailer bound: `Engine.KBSJoint` leaves **41** bytes, `StargateContent.CTFGateTeleporter`
**49** and `StargateVehicles.HoverTankPilotBot` **59** -- all inside the 64-byte trailer `class-defaults.py` already
tolerates, so they are short of the last byte rather than refused by it. `HoverTankPilotBot` is also the one row no
witness reaches: the record's closing group does not parse there and the stream does not reach the end.

**The two witnesses never disagree.** The 36 rows whose record-side closing group parses are exactly the 36 on which
this stream closes too; the stream speaks on 56 rows the record never reached, 37 of them the headless ones. Nothing
here moves the located array: what the 95 share is the array's own tail, not the block start.

**The same parse over all 2,204 located block starts: a close is the norm, not a confirmation.** `--corpus` re-reads
every row of `inventory/class_record_bound.csv` (`inventory/class_blockstart_stream.csv`, 2,204 rows): the stream
closes on the payload's last byte on **2,113 (95.9%)**, and the **91** that stop short leave a trailer of 1-64 bytes
(mode **1** on 22, **43** on 6, **41** on 5; 44 of the 91 sit in 33-64). So 92 of the 95 overshoot rows closing is the
corpus rate rather than a confirmation of its own -- the split is 451 of 454 (99.3%) for the rows whose array lands at
or before the block against 92 of 95 (96.8%) past it. What the corpus does not do is single the three short rows out:
trailers of 41, 49 and 59 bytes fall inside that 44-row bucket, so they are ordinary too. The block start and the
payload are consistent on all 95; the located array's tail remains the only thing they share.

**The position control is the same order corpus-wide.** 3,394 of the 8,816 later starts close (38.5%), and among the
919 rows whose class declares properties and whose neighbour closes, the block start names more declared tags on
**606**, the same number on **299** and fewer on **14** (1.5%). The name test therefore separates the block start
from its neighbours on two rows in three where it can speak at all; the other 1,237 closing parses name no property
the inventory carries, and there the close is the only evidence.

### No start inside a short trailer lands on the payload end with declared names

The three rows above that stop 41-59 bytes short -- `Engine.KBSJoint`, `StargateContent.CTFGateTeleporter` and
`StargateVehicles.HoverTankPilotBot` -- have one reading left: the block start is misplaced and the stream begins a
few bytes further on, leaving those bytes in front of it. `tools/class-overshoot-stream.py --trailer 59` answers it by
reading the same parse at *every* offset in `[block_start, block_start + 59]` (`inventory/class_trailer_scan.csv`,
164 offsets over the three rows) and scoring each by whether it ends on the payload's last byte and what its tags name.

| row | payload | block start | offsets read | later starts that reach the payload end | declared tags there |
|---|---:|---:|---:|---:|---:|
| `Engine.KBSJoint` | 95 | 47 | 48 | **3** (+39, +41, +44) | 0 / 0 / 0 |
| `StargateContent.CTFGateTeleporter` | 101 | 45 | 56 | **0** | -- |
| `StargateVehicles.HoverTankPilotBot` | 276 | 208 | 60 | **5** (+20, +38, +56, +57, +59) | 0 / 0 / 0 / 0 / 0 |

**A close inside the trailer is rarer than at an ordinary neighbour, so nothing there behaves like a stream that
starts a few bytes on.** Eight of the 164 later starts close (4.9%) against the **38.5%** the corpus census measured
at `block_start + 1 .. + 4`; most offsets inside the trailer answer `empty` (25 of 48, 24 of 56, 20 of 60) or stop
short, because 41-59 bytes of a small payload are mostly tag-shaped noise.

**And the eight that do close name no declared property.** `KBSJoint` and `CTFGateTeleporter` carry no row in
`inventory/class_properties.csv`, so the name test is silent on them; on `HoverTankPilotBot`, whose closure holds 591
declared names and matches 1 at the block start, the closing parses resolve to `Timer`, `Engine`,
`Tweak_Pawns_HoverTankPilot` and `InVal` -- identifiers of other objects, none of them a property the class declares
-- while the block start names `Mesh` and `ControllerClass`, one of which is declared. On the only row whose names can
tell two starts apart, the block start is the only start that names a declared property.

So the trailer is not a misplaced boundary: those 41-59 bytes are a region the class serialises after its defaults
block, and the block start stands on all three rows. What the region *is* is the writer's question, and
`Core/0003a800_Core_UnClass.c` in `$WORK/module-export` is on disk -- the listing between the defaults call and the
function's return is a grep, not a Ghidra round.


### The array's tail was its head: at the writer's head the record closes on the block start on 1,612 of 1,612

`inventory/class_record_array.csv` starts its walk at `prefix_end + 42`, and the two numbers that reading left on the
books were a median **27**-byte residue to the block start and **95** rows ending *past* it. Both are one thing:
`prefix + 42` holds the front's first value, not the array's count. `tools/class-record-array-head.py`
(`make class-record-array-head`, `inventory/class_record_array_head.csv`) reads the array where the gap-front census
put the count -- `prefix + 42 + gap_len` -- and closes the record with the four fields `UClass::Serialize` writes
after it, using `class-tail-run.parse_group` rather than a second guess at the grammar:

| measure | rows |
|---|---:|
| located rows | 2,204 |
| ... carrying a gap-front row, i.e. a head | 1,612 |
| the count at that head equals the census's `arr_count` | **1,612** |
| the block start past the array's end (never inside or on an element) | **1,612** |
| the array's end plus names-ref-name-list lands exactly on the block start | **1,612** |
| ... and every name index in the group is inside the package's name table | **1,612** |

The residue the head leaves is the closing group's own length: median **7** bytes, `7` on 619, `8` on 302, `9` on 301,
`6` on 244 -- 1,466 of 1,612 inside 6-9, the run the unit table measured. Its fields read like the writer's: the
reference is negative on **1,604** of 1,612, the names array holds 2-5 indices, and the version-gated list is empty on
1,286. So the record's field order closes to the byte from the head onward, and no residue is left behind the array.

**The 95 overshoot rows are not a class of row.** 89 of them carry a head, and at it all 89 close on the block start
with a residue of 6-11 (mode 9 on 36, 6 on 34) -- the corpus distribution, not a tail. What the shipped walk reports
past the block start is its own misalignment: on the overshoot rows the block start falls inside an element on 74
(dword2 on 66, at offsets 0-3 within the dword; dword1 on 8) and on an element boundary on 19. No element field
repeats at that boundary, which is what a whole-grid shift looks like and what a field over-consumption never does.

**The count the shipped walk reads is the front's first value on 413 of 413 rows** where both exist, and it happens to
*equal* the array's real count on 256 of them -- the coincidence that made the reading look right: both are small
integers and the front's first value is often the same one. A count that is right on 62% of the rows and a position
that is 19-21 bytes early explains the residue (front plus group) and the overshoots (the shifted grid) at once.

**The element is unchanged, and it is measured here rather than assumed.** The reference is wider than one byte on
1,502 rows (4,445 references), the first element is 10 bytes on 233 rows and 9 on 1,379, and the walk over the
elements never crosses the block start on any of the 1,612 -- the two raw dwords are there on every element of every
row. 592 located rows carry no gap-front row (the writer order that census needs does not close on them), so this
says nothing about them; the join it does make is exact, 1,612 of 1,612 on both the count and the close.


### The block side finds the head where the census can speak, and nowhere it cannot

`tools/class-record-array-head.py` closes the record from the head the gap-front census supplies, and the census is
silent on 578 of the 2,204 located rows. `tools/class-record-array-search.py` (`make class-record-array-search`,
`inventory/class_record_array_search.csv`) finds the head without it: every `p` in `[prefix + 42, prefix + 42 + 40)`
whose element walk is followed by a closing group that lands exactly on the block start with every name index inside
the package's table. The census is the control.

| population | rows | a candidate head | a unique one | the census head among the candidates |
|---|---:|---:|---:|---:|
| rows the census has a head for (control) | 1,626 | 1,616 (99.4%) | 1,399 (86.0%) | **1,612 of 1,616 (99.8%)** |
| rows without one | 578 | **13 (2.2%)** | 13 | -- |

Where the search is unambiguous it finds the census's position: the offset past `prefix + 42` is `19` on 730, `20` on
519 and `21` on 233 -- the front the gap-front census measured, recovered here from the block side alone. It is not
always unambiguous (2 candidates on 202 rows, 3 on 14, 4 on 1, none on 10) and those rows are reported as `ambiguous`
rather than resolved by preference; the earliest candidate is the one recorded.

**The 578 rows are not a locator failure, they are rows the group is absent on.** 521 of them carry a
`class_tail_run.csv` row and **none of the 521 parses as names-ref-name-list**, against 984 of 1,618 on the rows that
do have a head. The group is the search's only anchor, so a block start that no group precedes cannot be located this
way -- which is also why the gap census is silent on exactly this population, stated from the other side.

**The 13 hits are genuine and they are the exception that proves the shape.** They sit at offset 32-34 rather than
19-21, i.e. a longer front than the census's mode, and their counts are large where the classes are (`SquadAIBasic`
471 elements, `CTFFlag` 80, `GameObject` 59). A head further out is still a head: the search's window is 40 bytes and
the front it tolerates is up to 18 bytes longer than the one the census measured.

    make class-record-array-search
    python3 tools/class-record-array-search.py --dump DUMP --sweep -o inventory
    python3 tools/class-record-array-search.py --report

### The block start anchors the head without the group: 97% of the rows that have one, 64% of the blind ones

`tools/class-record-array-search.py` locates the head from the block side only through the record's closing group, and
that is exact where the group is there -- but **575** of the 2,204 located rows have no head from it: the 565 rows the
gap-front census is silent on and the group does not reach, plus the 10 rows the census has a head for and the group
does not parse. `tools/class-record-array-blind.py` (`make class-record-array-blind`,
`inventory/class_record_array_blind.csv`) needs no group, because the block start is itself an anchor: the tags of the
class's defaults stream begin there, so the record's last element must end there -- or 6-11 bytes before it, where a
group is present but does not parse as names-ref-name-list. The array is therefore read backwards:

    block_start  <-  the last element's end
                 <-  one element back at a time: a compact object reference (1-5 bytes, inside the package's tables)
                     plus the two raw dwords, i.e. a stride of 9 to 13 bytes
                 <-  a compact count in [prefix + 42, prefix + 42 + 40) whose decode ends where the walk is and whose
                     value equals the number of elements walked

It is the same element grid `tools/class-record-walkback.py` already walks over this record; what is new is the
anchor. Every start in `[block_start - 13, block_start]` is tried, every `(start offset, head, count)` is reported
rather than picked, and the sixteen zero bytes the gap-front census measured in front of the count are carried as a
column of their own.

| population | rows | a head | exactly one candidate | the sixteen-byte front there | exactly one such |
|---|---:|---:|---:|---:|---:|
| rows the group parse resolves (the control) | 1,629 | 1,608 (98.7%) | 582 | 1,591 (97.7%) | 1,591 |
| rows it does not reach (the measurement) | 575 | 429 (74.6%) | 205 | **368 (64.0%)** | 368 |

**The control reproduces both readings it is scored against.** The gap-front census's head is among the walk's heads
on **1,569 of 1,616 (97.1%)** and the group parse's head on **1,585 of 1,629 (97.3%)**, without either being used to
find it. Where a head is found the start offset is the group's own width -- `7` on 924 rows, `6` on 562, `5` on 442,
`8` on 378, `9` on 371 -- and where the census head is reproduced it sits `19` (730), `20` (509) or `21` (217) bytes
past `prefix + 42`: **1,456 of 1,569** at the front position the census measured, now recovered from the other end.

**On the blind rows the same position comes out.** The **368** rows whose head carries the sixteen-byte front sit at
`20` (167), `19` (106) and `21` (90) past `prefix + 42` -- **363 of 368** at 19-21 -- and their counts are short:
median **3**, mode **2** on 173 rows. That is why the group is absent on this population: a two- or three-element tail
does not parse as names-ref-name-list, and the gap-front census, which needs the record's field order to close on the
payload, goes silent on exactly the rows where it does not.

**The null is 17 times lower.** The same walk started one byte past the block start finds a head on **98 of 2,204
(4.4%)** against 74.6% on the blind population, so the anchor is doing the work and not the stride's freedom to fit.

**Negatives, stated as they are.** **146** blind rows yield no head at all, and on **41** of the 167 headless rows the
walk dies at depth 0: the byte in front of the block start does not decode as an in-table reference, so no element
ends there and the tags are not adjacent to the record either. **61** blind rows yield a head without the sixteen
zero bytes in front of it; those stay candidates, because the front is what separates them, and the earliest-candidate
rule the group search needed is not used anywhere here.

**What it settles.** The blind population is not a hole in the locator: **368 of 575 (64.0%)** have their head, at the
census's own position, from the block side alone and with no group, and 120 more have a candidate that the front does
not confirm. The 146 that stay unanchored are the next measurement, not an unexplained remainder.

    make class-record-array-blind
    python3 tools/class-record-array-blind.py --dump DUMP --sweep -o inventory
    python3 tools/class-record-array-blind.py --report

Reads the packages and two committed tables; no boot, no Ghidra round, no property name or value decoded.

### Widening the trailer to 40 bytes buys 33 control rows and nothing on the blind ones

The walk above starts at most 13 bytes behind the block start, because a closing group costs 6-11. `class-defaults.py`
tolerates a trailer of up to 64 bytes, so the same walk is run a second time from every start in
`[block_start - 40, block_start]` on all 2,204 rows, and both answers are carried in the artifact.

| measure | 13-byte window | 40-byte window |
|---|---:|---:|
| the census head reproduced (control, 1,616 rows) | 1,569 (97.1%) | **1,601 (99.1%)** |
| rows gaining the sixteen-byte front (control) | -- | 33, **all 33 carrying the census head** |
| blind rows with the front (575) | 368 | **368, none gained** |
| blind rows with any head | 429 | 450 |
| the null (a start past the window's own far end) | 98 (4.4%, one byte past the block) | **14 (0.6%)** |

**The 33 rows are a longer trailer, not a different record.** Their winning start sits 14 bytes back on 18 of them, 20
on 10, 15 on 2, 19 on 2 and 21 on 1, i.e. just past the group's own 6-11, and every one of the 33 lands on the head the
census measured. The widened null falls to 0.6% -- the extra room is not fitting noise -- so the wider window is a
strict improvement on the control.

**On the blind population it changes nothing: the count of rows with the front stays at 368.** 450 rows yield some
head, but the 82 that are new have no sixteen-byte front, and 0 of the 207 blind rows that lack the front gain one.
So the rows that remain unlocated are not waiting on a longer gap: the bytes between their array and their tags are not
the explanation, and the next measurement has to read the array's own content -- a count that is not in
`[prefix + 42, prefix + 42 + 40)`, or an element whose reference is outside the package's tables.

    make class-record-array-blind
    python3 tools/class-record-array-blind.py --report

Same tool, same artifact: `wide_heads`, `wide_z16`, `wide_head`, `wide_off`, `wide_count`, `wide_head_off`,
`wide_null` and `wide_census_matched` are the widened run's columns, so the comparison above is re-checkable from the
CSV alone.

### The count read forward fits one byte past the tags as often as at them

The previous section named the two remaining explanations for the 207 blind rows with no sixteen-byte front: a count
outside the 40-byte window, or an element reference outside the package's tables. Both are bounds of the *backward*
read, so the same grid is read the other way: every position in `[prefix + 42, block_start)` is read as a count, its
elements are walked **forward** (compact reference of 1-5 bytes inside the package's tables, then two raw dwords
each), and the walk is accepted when it ends on the tags, allowing the recording group's 0-11 bytes of slack. The
count is then read a second time as a raw 32-bit dword, and a third pass admits references outside the package's
tables. The same artifact carries all four answers plus the shipped backward one, on all 2,204 rows.

| measure | control (1,629) | blind (575) |
|---|---:|---:|
| a forward head at the block start | 1,623 (99.6%) | 482 (83.8%) |
| the group parse's head among the fits | 1,529 (93.9%) | -- |
| mean fits per row, at the block start | 2.05 | 1.76 |
| mean fits per row, **one byte past it (the null)** | **2.06** | **1.86** |
| the count as a raw 4-byte dword | 44 (2.7%) | 33 (5.7%) |
| references outside the tables admitted (loose) | 1,626 (99.8%) | 487 (84.7%) |

**The null is the whole result: aimed one byte past the tags, the same walk fits as often as at them.** It answers on
2,100 of 2,204 rows (95.3%) and its mean fits per row *exceeds* the block start's in both populations, so the forward
read has no discriminating power at all -- the 482 blind "heads" are chance fits, not a located count. That kills both
candidate explanations rather than confirming one: a count outside the window would still have been found by a scan
that reads the whole gap (median 52 positions on the blind rows, 49 on the control), and an out-of-table reference is
not the block either, because admitting those gains only 5 rows on the blind population and 1,626 of 1,629 control
rows fit *without* admitting anything, i.e. the tables were never the constraint. On the 146 blind rows the backward
walk finds nothing at all, the forward read answers 54 (37.0%) -- while its null answers 65 of the same 146, mean
0.71 fits against mean 0.90 -- so even that subset is noise.

**What the forward read is good for is candidates, not positions.** Where the truth is known it does contain it: the
group parse's own head is among the fits on 1,529 of 1,629 control rows. But only 70 of those 1,629 rows have a fit
that ends exactly on the tags at all, the picked gap is 4-7 bytes on 1,259 of the 1,623 rows that fit, and the null is
nearly as productive where the head is known -- so the set cannot carry a location on its own.

The defaults block beside it is not the doubt: read forward from `block_start` it reaches its `None` terminator on all
2,204 rows (median 34 bytes, 4 properties) and 2,113 of them (95.9%) end exactly on the payload's last byte -- 485 of
the 575 blind ones included, so those rows' blocks close normally and it is the record's side that is unread.

    make class-record-array-blind
    python3 tools/class-record-array-blind.py --report

The forward columns are `fw_*` (compact count), `fr_*` (raw dword count) and `fl_*` (loose references), each with its
own null (`fw_null`, `fr_null`), plus the block's own extent (`block_len`, `block_props`, `block_reason`) and the
number of count positions scanned (`gap_scanned`) -- so every number above is one `--report` away from the CSV.

### The front names nothing: each reading it passes is a small integer's base rate

The forward walk of the writer's order closes on 1,626 of the 2,204 located class payloads, and on **1,482** of them
the step it takes goes over a gap of 19 to 21 bytes: a front of 3-5 bytes, then a run of sixteen zero bytes, then the
array's count. The zeros are the four dwords `UStruct::Serialize` writes (`0x370e0` at `+0x5c`, two `0x37110` at
`+0x58` and `+0x54`, the script count at `+0x4c`); on the cooked classes all four are zero, which is why no payload
distinguishes them from padding. The front is the remaining content, and no body in the chain (`0x38dc0`, `0x392b0`,
`0x394c0`, `0x3a800`) writes one there -- the only field `UStruct::Serialize` names between its second reference and
those dwords is `FriendlyName` at `+0x44`, an `FName` of two compacts written through the archive's name slot
(`+0x1c`, `0x00038bf0`).

`tools/class-record-front.py` (`make class-record-front`, `inventory/class_record_front.csv`, its `--check` in
`make ci`) identifies the front by what it can be. Four readings are taken on every gap row and each one is also run
on three same-length windows a field would not be found in -- one byte later inside the same gap, one window earlier,
and the bytes immediately before the defaults block:

| reading | front | one byte later | one window earlier | before the block |
|---|---:|---:|---:|---:|
| every value a valid name index | 1,484 of 1,490 (99.6%) | 1,484 (99.6%) | 1,254 (84.2%) | 309 (20.7%) |
| the first value names a property the class declares | **1 (0.1%)** | 1 (0.1%) | 0 | 0 |
| every value a valid object reference (`0` as null) | 5,457 of 5,468 (99.8%) | 5,468 (100.0%) | 4,731 (86.5%) | 5,148 (94.1%) |
| of those, the non-zero ones inside the tables | 2,221 of 2,232 (99.5%) | -- | -- | -- |

**The name and reference readings are base rates, and the one reading with discriminating power refuses the front.**
A compact value below the name count, the export count and the import count passes all three of the first tests at
once, and this image's tables are large enough that a value under 64 always does; the same window shifted by one byte
scores identically on the name reading and *better* on the reference one. The property-tag reading, which asks the package whether the first value names a `Property`
export of the class itself, answers on **1 of 1,490** against 0, 1 and 0 for the controls, so the front is not a tag.

**It also cannot be the field the writer names there.** 1,479 of the 1,490 fronts (99.3%) contain no value that needed
more than one byte, so every value is 0-63 -- while an `FName` written in a package with thousands of names, or an
object reference into a table of hundreds, mostly is not. The largest value anywhere in this census is 1,863, on one
row where `0xc0 0x06` decodes to -384. And the front's own width is wrong for an `FName`: an `FName` is two compacts
and **8** of 1,490 fronts are two values (the rest are 3 (730), 4 (516), 5 (227), 1 (2), and 6 rows carry a 15-byte
run), so the front is one field too wide for the only one the body puts there.

So the front is not a name, not a property tag, not an object reference, and not `FriendlyName`; and because a
multi-byte compact is absent from 99.3% of fronts it cannot be an index above 63 into either table. What the 3-5
bytes are remains open, and the census is the row-by-row record of what they are not.

    make class-record-front
    python3 tools/class-record-front.py --dump DUMP --class AmbientCreatures.Bird
    python3 tools/class-record-front.py --report

### The chooser refuses a start inside the committed group, and the group's end recovers 281 blocks

`tools/class-record-open-tail.py` left the chooser itself named: on the 245 open rows the located start sits inside
the record's closing group, 2-10 bytes before its end. `tools/class-blockstart-guard.py` makes that geometry a rule
and re-runs the chooser over the shipped population (`class_defaults_per_class.csv`, 2,209 rows with a start, 2,190
readable). Two changes, one rule read from both ends: a candidate start that a **committed** closing group covers is
refused, and a committed group's **end** joins the candidate set -- the group is the record field in front of the
block, so its end is where the record says the block's first byte is. The ranking is `class-defaults.py`'s `choose`,
untouched, so the only thing that changed is the population of candidates.

- **The guard fires on 297 of 2,190 rows (13.6%) and 252 of them move.** The refused starts are badly wrong: the
  declared fraction of the chosen block's tags has a mean of **0.364** and is fully declared on **13 of 297**,
  against **0.919** and **231 of 252** at the start the guard leaves. 224 of the moved rows are answered by the
  group's own end -- often a **one-property** block, which `choose`'s two-property floor withholds and
  `class-defaults.py`'s `single_declared_block` reads only from a free scan; anchoring that reading to the group's
  end is what keeps it from firing at every offset. 28 are answered by another parsed block. **45 rows are refused
  with no replacement** and keep the shipped start (`Engine` 17, `UWindow` 10, `StargateGame` 7, `GamePlay` 6,
  `Fire` 4, `UnrealEd` 1): every parse the chooser had there is covered by a group.
- **The population the last note named is answered whole.** On the **245** open rows whose element walk stops 1-9
  bytes short, the guard refuses **245 of 245**; 216 move and **206 land exactly on the group's end**, which takes
  fully-declared from **2 of 245 to 206 of 245**, and the new start's tag stream reaches the export end on 207 of
  the 216. 206 of the 216 are answered by the group's end and 10 by another parsed block. The movement is the
  group's own length: **+4 on 93, +3 on 80, +5 on 15, +6 on 13, +7 on 3, +2 on 2**, then eight negative rows
  (-12 to -75, where the covering group starts before the residue does) -- and the remaining 29 refusals are the
  no-survivor rows.
- **The control is the geometry this tool does not read.** Joined to `class_record_open_tail.csv`'s `overlap`
  (element walk, group parsed from the element end): the guard refuses **282 of the 481** rows whose group ends past
  the start (58.6%) and **10 of the 1,596** rows whose group ends on it (0.6%) -- `Engine.Stream`,
  `Engine.AvoidMarker`, `Engine.PSBumpBSP` and three `StargateContent` formation classes, whose covering parse is a
  nearer group than the element walk's. 199 overlapping rows are not refused, because the covering group starts
  further back than the 16-byte lookback or no parse inside it carries a committed reference.
- **The commitment test is what makes a cover mean anything.** `free_group` only demands that every index be inside
  the name table, and index 0 is inside it, so a zero run parses as an empty group at every offset and a start four
  bytes into one parses as a group whose names are all 0. The archive's reference slot writes import references
  **negative**, so the guard takes a group only when its reference is negative: **297 of 297** covering groups are,
  and the padding windows that a bare parse accepts are refused.
- **The population total moves from 1,655 to 1,936 fully declared of 2,190 (75.6% to 88.4%).** 62 of the gain is the
  group-end candidate on its own, on rows the guard cleared: those starts stay put, but a one-property group-end
  block outranks them.
- **The chooser reproduces the shipped start on 2,188 of 2,190**, so a movement is the guard's and not a version
  skew. The two exceptions are `Engine.Speech` (shipped 139, chooser 5) and `StargateGame.CTFFlagMesh` (96 against
  21,801): `class_defaults_per_class.csv` is one chooser version behind on those rows.

`make class-blockstart-guard` writes `inventory/class_blockstart_guard.csv` (2,209 rows: `shipped`, `chosen`,
`reproduced`, `cands`, `guard`, `group_from`/`group_len`/`group_end`/`group_ref`/`group_hex`, `overlap`,
`old_count`/`old_known`/`old_decl`, `survivors`, `new_start`/`delta`/`new_count`/`new_known`/`new_decl`/`new_to_end`/
`new_origin`/`new_on_group`, `verdict`). It reads the packages and three committed tables; no boot, no Ghidra round,
no property value decoded.

**What is still open.** The guard's start is a separate artifact: `class_defaults_per_class.csv` still carries the
old start, and five artifacts downstream of it (`class_defaults_emit`, `class_defaults_coverage`,
`class-defaults.py --coverage`, `docs/CLASS_DEFAULTS.md`, `docs/STATUS.md`) would move with it, so switching the
chooser is its own change. And the 45 refusals with no replacement are a measurement, not a tie: on them every
parsed candidate lies inside a group, so the group parse is probably over-reading (`Engine.SavedMove`'s group starts
at 347 and ends at 359 of a 360-byte payload, leaving no block), which the anchored parse would show.

### The 45 refusals with no replacement are not an over-read: their payload ends with an empty block

The guard's own open item was that on 45 rows every parsed start lies inside a committed closing group, which looked
like the free group parse over-reading past the record. `tools/class-group-stop.py` reads the class's property tag
stream from the group's end and answers it the other way round: on those rows the payload ends **inside a byte** of
the group on 36 of 45, and the byte that is left opens an **empty** block (`empty-block-after-group`, 36 of 45).
Nothing follows the record there, so there is no start to find and the refusal is right.

- **The refused population splits by what the group's end opens.** On the **297** rows
  `class_blockstart_guard.csv` refuses: **234** open a block that runs to the payload's last byte (`ActionString`
  first on 51, `bObsolete` on 10, `VoiceSets` on 9, `Mesh` on 9; fully declared on **224 of 241** non-empty streams,
  mean declared fraction **0.929**), **55** open an **empty** block (a single terminator), **6** parse a tag or two
  and overrun, **1** block stops short of the end, and **1** group ends on the payload's last byte.
- **The split is the guard's own verdict, not a separate reading.** Of the 252 rows the guard moved, a block opens
  on **232**; of the **45** it refused with no replacement, a block opens on **9** and **36 are empty**, with the
  group ending within a byte of the payload on **36 of 45**. So the 45 are the class's own shape -- a class that sets
  no defaults -- and not a locator failure, which is the answer the last note's next measurement asked for.
- **The control is the instrument, and it is exact.** On the **1,586** rows whose group end is the located block
  start by two independent witnesses (`class_record_open_tail.csv`'s `overlap == 0`: the element walk and the group
  parse both put it there), the same reader opens a block that runs to the payload end on **1,586 of 1,586**, fully
  declared and mean **1.000**, with declared names first (`Emitters` 185, `EnvironmentSize` 100, `KeyName` 70,
  `RosterString` 67). So the 19% that open nothing on the refused rows is the payload's shape and not the reader's
  failure -- the one control that could have made this a tie.
- **What follows is small and not the discriminator.** After the group end the payload carries a handful of bytes on
  the refused rows (mode **5** on 69, **1** on 48, **8** on 39) and the same order on the control (**22** on 51,
  **44** on 40, **9** on 39), so the group's end is not distinguished by the size of what follows it -- only by
  whether a tag stream opens at all.

`make class-group-stop` writes `inventory/class_group_stop.csv` (1,883 rows: `population` -- `refused` or `control`
-- `guard_verdict`, `payload`, `shipped`, `group_from`/`group_len`/`group_end`, `after`, `props`, `to_end`, `first`,
`decl`, `last`, `verdict`). It reads the packages and the two committed geometries; no boot, no Ghidra round, no
property value decoded.

**What is still open.** Thirteen refused rows are not accounted for by an empty block at the payload's end: 6 parse
nothing at the group end with 4-10 bytes after it, 7 open an empty block with 2-459 bytes still after it (274, 296 and
459 among them), and `StargateVehicles.HoverCraftFixedGun` opens one tag with 108 bytes left; and
`class_defaults_per_class.csv` still carries the pre-guard start, so the 234 rows this page says have a block at the group end are still read 2-10 bytes early by every count
downstream of that artifact. Switching the chooser, and re-running the five artifacts that read it, is the change
those two leave.

### The boundary past the closing group is 3, 4 or 11 bytes on 7 of the 13, and there is none on 6

`tools/class-group-after-scan.py` takes the 13 rows `class_group_stop.csv` refuses where the group's end opens nothing
usable and more than one byte of payload still follows it, and reads the tag stream from every offset in
`[group_end, group_end + 12)`. The pick is the answering offset that reads the **most** properties: a block read from
inside another one is a suffix of it and strictly shorter, so the widest reading is the boundary.

- **Offset 0 answers on none of the 13, and some offset in the window on 7.** `Engine.Weapon` opens at **k=3**
  (`FireSoundPitchMax` first, 34 tags, every one declared, 271 bytes to the payload's last byte), the two
  `StargateContent.ZatGun_*` rows at **k=4** (one tag, `AmmoName`), and all four `StargateContent` FORMATION rows at
  **k=11** (`offset` first: 47 tags on `Wedge`, 32 on `2Column`, 21 on `SingleFileSpreadOut`, 7 on `HaakenPod`).
- **Six answer nowhere in the window.** `Engine.Admin`, `Engine.AvoidMarker`, `Engine.ProceduralSound`,
  `Engine.SoundGroup`, `StargateContent.DecipheringTrigger` and `StargateContent.HackingPanel` leave only **2-6 bytes**
  after the group and read as empty, short, undeclared or unreadable at all twelve offsets, so they are the empty
  defaults block of the previous section with a few bytes of the record's tail still in front of the payload's end.
- **The missing width is not one constant.** 3 bytes on `Engine.Weapon`, 4 on the two ZatGuns, 11 on every FORMATION
  row. On `Weapon` the three bytes plus the group's own last byte are `00 00 80 3f`, a float **1.0**; the FORMATION
  gap is the same eleven bytes on all four (`00`, two two-byte compacts, four zero bytes). That reads as a field of the
  class family plus the group's optional tail rather than as noise, but the field is not identified here.
- **The control fixes what a hit means.** On the **1,586** rows whose group end is the located block start by two
  witnesses, offset 0 answers on **1,586 of 1,586** and the widest reading picks it on **1,586 of 1,586**. The scan is
  permissive: **1,180 of those (74.4%)** also answer at a second offset (622 rows have two answering offsets in all,
  468 three, 57 four), 1,888 extra answers over those, so "an offset answers" alone is a 100% base rate and only the
  widest reading separates a boundary from a suffix.

`make class-group-after-scan` writes `inventory/class_group_after_scan.csv` (1,700 rows: the **114**
`(class, offset)` tests on the 13, one row per control class). Columns: `population`, `payload`, `shipped`,
`group_from`/`group_len`/`group_end`, `after`, `k`, `offset`, `props`, `length`, `to_end`, `decl`, `first`, `last`,
`verdict` (`declared-to-end`, `undeclared`, `short-of-end`, `empty`, `no-stream`, `unreadable`, `nothing-left`,
`past-payload`), `n_full` and `full_offsets`. It reads the packages and the two committed geometries; no boot, no Ghidra
round, no property value decoded.

**What is still open.** The 7 rows now have a boundary and `class_defaults_per_class.csv` still does not: the chooser
takes the group's end as a candidate but not `group_end + 3/4/11`, and the six rows with no block should be marked as
having no defaults rather than left refused. `StargateVehicles.HoverCraftFixedGun` -- one tag, 108 bytes left -- is the
one refused row that opens a block and stops short, and it is not in this population.

### The chooser takes the group's end: 373 starts move, and one of them costs a block

`tools/class-blockstart-guard.py` measured the rule that `tools/class-record-open-tail.py` found the geometry for;
this change puts it in the chooser itself, in `tools/class-defaults.py`, which is what writes
`inventory/class_defaults_per_class.csv` -- the artifact every count downstream of the record's end reads. Three
things had to be measured before it could move: what a committed group is, what happens to a block that the rule
takes away, and what the recovered starts are worth.

- **A committed reference is one the package's own import table can hold.** The guard took any negative reference as
  the writer's, and an `FCompactIndex` is five bytes wide, so a parse can carry -13,555,990,527 -- an index no table
  holds. Measured over the 2,209 located rows: of the **1,654** groups whose end **is** the shipped start (the
  control population, two witnesses), **1,653** carry a magnitude inside the import count and one does not, with a
  median of **6** and a largest of **42**. Of the groups that *cover* a shipped start, on **18 rows** every cover is
  outside the table: `Engine` holds **120** imports, so the cover at **-3,007** over `Engine.AvoidMarker` and
  **-567,581** over `Engine.FileLog` cannot be the record's field, and those starts are no longer refused.
- **The rule in the chooser.** A candidate start a committed group covers is refused; the group's **end** is offered
  as a start in its own right; and when that end opens an **empty** block, every offset in `[end, end + 12)` that
  opens a declared block reaching the payload's last byte is offered too -- the window `class-group-after-scan.py`
  measured, which is where the block sits behind the record's tail.
- **373 of the 2,319 starts move and 1,946 stay.** The movement is the group's own length: **+4 on 100, +3 on 83,
  +7 on 16, +5 on 15, +8 on 15, +6 on 14, +9 on 9**, then a long tail (+19 on 6, +20 on 4, **+89 on 4**) for rows
  where the covered start was deep inside a longer record. The declared fraction of the chosen block rises on
  **314** of them and falls on **1**: `Engine.PSBumpBSP`, whose two-tag fully declared block at 100 a cover still
  refuses -- the rule's whole cost, and the same row `class_record_open_tail.csv` lists as an `overlap == 0` false
  positive. 58 more move at an unchanged fraction, 22 of them start to start.
- **The recovered blocks are one property each, and the floor still withholds the verdict.** **316** rows now carry
  a start at a group's end whose whole block is a single tag (0 before), so `defaults = 1` is published and
  `fully_declared` stays 0: the floor is a verdict about the *block*, and `docs/CLASS_DEFAULTS.md` measured that
  promoting one-tag parses turns 320 misses into "fully declared" on a single tag. `single-declared` in the misses
  table therefore rises from 263 to **288 of 656**, and the head-line count barely moves: **1,663 fully declared of
  2,319 (71.7%) against 1,664**, with **122 classes carrying no block at all** (110).
- **What it costs and what it buys downstream.** `inventory/compile_readiness.csv` goes from **1,824 to 1,823**
  classes with no blocker (`no-defaults-block` 82 -> 91, `defaults-ambiguous` 413 -> 405), and the four artifacts
  that read the start were regenerated in the same change: `class_defaults_coverage.csv` (354 ambiguous, was 430),
  `class_defaults_misses.csv`, `compile_readiness.csv`/`_by_package.csv` with `docs/COMPILE.md`, and
  `docs/STATUS.md` with the README badges. `StargateVehicles.HoverCraftFixedGun` -- the row the last note left as
  "one tag, 108 bytes left" -- now lands **16 bytes later on an 11-tag block, every name declared**.

Reproduce with `python3 tools/class-defaults.py --packages DUMP/System -o $WORK/cd` (9 s for 2,319 classes) and
`python3 tools/class-defaults.py --coverage DUMP/System -o $WORK/cd`; the reference test is
`committed_group()` and the rule is `guarded()`. No boot, no Ghidra round, no property value decoded.

**What is still open.** The 288 `single-declared` rows are now a population rather than a residue -- each has one
declared tag tiling the payload's tail from a group's end, and the floor says that is the shape of a table tail as
often as of a block, so deciding it needs a reader that does not exist for either (a walk of the bytes after the
group that classifies where a tag grammar stops, or one stock `ucc` build of a `*VoicePack` class). And the 58 rows
that move without their declared fraction changing are the next section's measurement:
`tools/class-defaults-moved.py` places both starts against the group and answers where they sit.

### The 58 unchanged-fraction moves are the group's own bytes, and 14 of their replacements sit in front of the group

`tools/class-defaults-moved.py` recomputes the pre-rule choice (the same chooser with `guarded()` taken out), places
both starts against the payload's committed groups, and reads the 58 rows the last note left open.

- **The population is exact, and the rule is the only cause.** The recomputation reproduces the merged artifact's own
  diff: **58 of 2,319** rows change at an unchanged declared fraction -- **22** to a different start, **36** to no
  block -- against 289 whose fraction rises and one that falls (`Engine.PSBumpBSP`). All **58** pre-rule starts sat
  **inside a committed group**, so the refusal accounts for every one of them and no row moved for another reason. The
  36 block losses are 36 of the 37 the last note counted, and every one was a fraction-0 parse, so the rule's only
  real cost is still the row whose fraction fell.
- **Where the replacements sit.** 36 leave no start at all. **14 land before the payload's first committed group** and
  name nothing the class declares; **5 land in the 12-byte window past a group's end** and **3 exactly on a group's
  end**, and those eight name every property they hold (fraction 1.0) while losing exactly one tag to the move:
  the four FORMATION rows 48/22/33/8 -> 47/21/32/7 at a gap of **11**, `StargateVehicles.HoverCraftFixedGun`
  12 -> 11 at **6**, `StargateGame.TeamTeleporter` 6 -> 5 and both `StargateMenus.Select*Panel` 2 -> 1 **on** the end.
- **The third option the last note named is empty by construction; the one that exists is in front of the group.**
  `candidates()` already drops a start whose parse ends past the payload's last byte, so no start can sit there. What
  the scan found instead is a start **ahead of the record's closing group**: `StargateContent.HackingPanel` at offset
  **1** (94 bytes of payload, two tags) and `StargateContent.DecipheringTrigger` at **46** (from 87, 49 bytes), with
  `GamePlay.ACTION_*` at **66**, `StargateVehicles.VehicleRemoteTurret` at **46** and
  `StargateVehicleGunAttachment` at **47**. On **11 of the 14** silent moves the first tag is one of the build's own
  **package** names (`Core`, `Engine`, `Gameplay`, `StargateVehicles`), a name-table entry that cannot be a property
  tag of any class. A parse that tiles a short payload from a name index is an end witness, not a block.
- **What the coverage table can and cannot claim.** **212** of the 1,663 published blocks name no declared property at
  all (`defaults = 1`, `declared_fraction = 0`), and the rule removed **36 of the 248** such starts that existed before
  it: it prunes this shape and leaves most of it in place. A zero-fraction start is the same end-witness shape whether
  it sits on a group's end or in front of the group, so `defaults = 1` on those 212 rows is not evidence that the class
  has a defaults block.
- **The chooser's own witness floor is computed and dropped.** `guarded()`'s comment says a candidate must run to the
  payload's last byte once the guard refused something; the code builds that list as `kept` -- which only labels the
  `origins` map -- and returns a filter over the *unfiltered* `cands`. Measured: **1,972** classes had a candidate
  refused, **16** of them publish a start with a nonzero trailer, **93 of the 2,197** chosen starts leave bytes behind
  overall, and **13 of the 22** moved starts do (trailers 12 to 61). So the rule as shipped is "refuse a covered start
  and offer the group's end", without the floor its comment describes.

`make class-defaults-moved` writes `inventory/class_defaults_moved.csv` (58 rows: `kind` -- `start-moved`,
`block-lost`, `block-gained` -- both block counts, `known_before`/`known_after`, both fractions, both starts,
`before_where`/`after_where` (`inside-group`, `on-group-end`, `in-group-tail`, `past-group-tail`,
`before-first-group`, `no-group`, `no-block`), `before_covered`, `group_end`, `gap`, `to_end`, `trailer_before`/
`trailer_after` and the first tag of each parse). It reads the packages; no boot, no Ghidra round, no property value
decoded.

**What is still open.** Whether the 212 zero-fraction rows keep `defaults = 1`: the geometry here says the start is a
name-table entry in three cases in four, and the floor that would settle it is the trailer-0 witness the chooser
computes and drops -- applying it touches **16 of the 1,972** refused rows and is the change this leaves.
