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

**Why the registration stops at 356.** Natives are registered with the class that declares them, and
at the main menu only the classes the front end has loaded exist. A complete table therefore needs a
dump taken after a mission loads — which is blocked on the input automation problem in
`docs/XEMU.md`, not on the table. The 273 declared indices are all covered because the engine's own
native classes are loaded before the menu.

**Validation.** The three operator natives behave like operator bodies: at `execNotEqual_ObjectObject`
(`0x000463f0`), `execAdd_IntInt` (`0x00041a50`) and `execAndAnd_BoolBool` (`0x00041120`) the code takes
the `FFrame` from `[esp+0xc]`, reads the operand byte at `[frame+0xc]`, advances that pointer and
pushes the argument — the standard UE2 exec prologue for reading an immediate. `execConcat_StrStr`
(`0x00049e10`, 318 bytes) is the largest of the operator group, as a string join should be. Nothing
here is inferred from ordering: every address came out of the runtime table at the index the script
declaration names.

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

| measurement | before seeding | after assert seeds | after data-table seeds |
|---|---|---|---|
| functions | 14,220 | 14,280 | **14,912** |
| assert sites in no function | 199 | 104 | **102** |
| assert-attributed functions | 1,201 | 1,253 | **1,256** |
| `FUN_*` (unnamed) | 12,339 | 12,347 | **12,971** |

The last column's larger `FUN_*` count is the expected cost of this technique: the 629 entries named
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
table. One observation is left open rather than explained: seven slots — 19 and 20 among them —
hold `0x00020f00` in 327 tables and something else in the remaining 165, which looks like two
families of table (abstract/base classes against concrete ones) but is not established here.

## Next

1. The rest of the `UObject`/`UStruct`/`FFrame` layout, now that `ProcessInternal` and `ProcessEvent`
   give worked examples of how each field is reached. The property side is measured in
   `docs/NATIVE_PROPERTIES.md`; what remains there is the tagged tag record's field order (U10).
2. Why 327 of the 492 class tables share one stub at seven slots (19 and 20 among them) while 165
   do not — abstract/base classes against concrete ones is the likely reading, and it is not
   established (`docs/UNKNOWNS.md` U8).
3. A second `GNatives` dump after a mission load, to catch the classes the front end does not touch
   (blocked on input automation, `docs/XEMU.md`).
4. Signature libraries for the CRT/XAPI/D3D8 majority (4.2) — blocked on signature data that this
   repository may not carry (`docs/UNKNOWNS.md` U9).
