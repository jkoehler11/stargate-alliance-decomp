# The UnrealScript VM in `default.xbe`

Status: `GNatives` located (4.3) and **read out of a running build** (4.4): the table has no bytes in
the file, so it was dumped from xemu over the GDB stub. 356 of 4,096 slots are registered at the main
menu, all 273 declared native indices among them. `UObject` layout, `ProcessEvent`/`ProcessInternal`
and the property tables are not yet documented; the interpreter site below is the way into them.

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

## Next

1. A second dump after a mission load, to catch the classes the front end does not touch (blocked on
   input automation, `docs/XEMU.md`).
2. `UObject` layout, `ProcessEvent`/`ProcessInternal` and the property pointer tables, starting from
   0x0003a083 — the interpreter is where the VM's structures become visible.
3. Signature libraries for the CRT/XAPI/D3D8 majority (4.2).
