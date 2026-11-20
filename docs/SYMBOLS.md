# The symbol map (`symbols/xbox.map`)

Status: emitted and checked. **15,447 symbols** — 14,973 functions plus 474 class tables — every one
inside the image's own sections, and `gdb` resolves them from the assembled form. This is PLAN 4.10.

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
  falls outside the section table: measured **15,447 of 15,447 inside, 0 outside**.
- **Names are file-level, not unique.** 131 names are used by more than one function — the assert
  attribution names a function by its source file, so `Core_UnTemplate` covers 309 of them,
  `Engine_UnActor` 97, `Core_UnClass` 73. The `.map` keeps the build's own text; the assembled `.s`
  gives every duplicate an `_<address>` suffix (1,299 symbols affected) so a debugger can address
  them separately, and the count is printed on every run.
- **7,788 functions are still `FUN_*`.** They are in the map — a debugger needs the address even
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
| `drop` | this address is not a body, it is interior code of the row named in `target` | must land strictly inside that row's repaired span |
| `add` | a body the map has no row for; `target` carries its name | may not land inside any row, and needs a matching `xbe_overrides.csv` row — that file is what creates the function |
| `rename` | the name this row's own instruction settles; `target` carries it | the map must have the row; moves no edge, so it cannot join an overlap |

Fifty-seven rows are in the file: eleven body repairs and the 46 thunk renames measured below. Each is one measured
thing: the `Core_UnTemplate` row at `0x0002f820` folded
into the audio body it belongs to (`0x0002f13c`, 2,282 bytes), the pump at `0x0002feb0` given its real end
(`0x3137f`), the body at `0x0002fa30` — slot 33 of `XBoxAudioSubsystem`'s table, which calls
`XboxAudio_FlushSounds` over `[this+0xd8]` — added, and eight other rows that stopped inside the slot load or the
`rdtsc` pair of a scope close resized to their own closing `ret`. The counts do not move: **14,973 functions and
3,970,681 bytes**, because the fold and the add cancel.

`--check` runs in `make ci` through the tool contract: it re-applies the file to the committed csv in memory and
fails if the bytes differ, so a Ghidra re-import that undoes a repair is red instead of silently reverting the map.
It also refuses a boundary row whose `function_size` in `symbols/xbe_overrides.csv` disagrees with the map — that
column was written by the registration pass and read by nothing — and refuses an `add` with no row in that file,
because `XbePost.java` builds a function from an override row and an `add` alone is a body the next import cannot
see (U37).

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
