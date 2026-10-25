# Architecture

How the shipped build is put together, and which artifact proves each statement. This is a map of the
*measured* structure — where the layers meet and what sits at which address — not a design document for
code that does not exist yet. Anything not measured is marked as unknown instead of filled in.

Everything here comes from the committed inventory, the symbol map and the documents in `docs/`. The
"proof" column in each section names the file or command, so a reader never has to take a sentence on
trust.

## 1. What ships

Three layers, and they are separable: a native image, a script tree, and content.

| layer | artifact | measured |
|---|---|---|
| native | `default.xbe` | 6,209,536 bytes, 15 sections, 13 statically linked libraries (`docs/IMPORTS.md`, `docs/XBE_LAYOUT.md`) |
| script | 18 `.u` packages | 2,319 classes, each also carrying its **original source text** as a `ScriptText` export (`docs/PACKAGE_SCRIPT_TEXT.md`); the decompiled tree at L0 is a cross-check on that text, not the only readable copy (`inventory/decompile_coverage.csv`) |
| content | 73 maps + audio | 61 mission maps, 10 multiplayer maps, 1,364 speech WAVs, 1,923 music WAVs |

The script packages, largest first: `Engine` 3,090,635 bytes, `StargateGame` 3,031,678, `StargateContent`
1,743,988, `UPreview` 734,635, `GUI` 658,290, `UWindow` 652,275, `Editor` 457,514, `UDebugMenu` 402,839,
`Sharpeffects` 365,841, `GamePlay` 334,722, `StargateMenus` 285,884, `StargateVehicles` 498,874,
`Stargateeffects` 131,482, `IpDrv` 83,120, `AmbientCreatures` 73,342, `Core` 79,165, `UnrealEd` 17,588,
`Fire` 15,474. Proof: `inventory/packages.csv`.

Two of those are worth noticing on their own: `UnrealEd` (17 KB) and `Editor` (457 KB) are shipped in a
retail build, and `UPreview` is the one package UELib could not identify a build version for.

## 2. The address space

The image is one flat mapping at `0x00010000`; nothing is paged or relocated at runtime, so an address
in the symbol map is the address in the running game. Three structures anchor the layout:

| what | address | size | proof |
|---|---|---|---|
| code | `0x00011000` upward | 14,956 functions | `symbols/xbox_functions.csv` |
| class tables (`.rdata`) | `0x004c78c0`–`0x0053b168` | 462 KB, 492 tables | `inventory/xbe_class_tables.csv` |
| name table | `0x006ba0c8` | 4,096 slots | `docs/NATIVE_PROPERTIES.md` §3 |

Code is ordered **per translation unit, not per module**. `Core` functions span `0x00012620`–`0x0032a840`
and `Engine` functions span `0x00033329`–`0x002980b0`: the ranges interleave across the whole 1.4 MB of
`.text` because the linker placed objects as the build emitted them, with unity-style grouping inside
each module. Anything that assumed "Core lives at this address" would be wrong. Proof: the prefix
ranges in `symbols/xbox_functions.csv`.

Function counts by attributed module — this is what assert-derived file attribution bought, and it is
the honest picture of where the unnamed code is:

| module | functions | bytes |
|---|---:|---:|
| (unnamed) | 13,044 | 3,117,291 |
| Engine | 577 | 279,602 |
| Core | 576 | 388,951 |
| dispatch: declared | 273 | 83,301 |
| other named | 215 | 22,915 |
| dispatch: slot only | 83 | 9,761 |
| Gui | 55 | 13,308 |
| stargategame | 32 | 5,612 |
| D3DDrv | 22 | 15,540 |
| IpDrv | 22 | 7,543 |
| XboxLive | 13 | 7,148 |
| stargatevehicles | 12 | 2,521 |
| XboxAudio | 9 | 9,556 |
| Fire | 9 | 2,558 |
| Gameplay | 9 | 1,604 |

There is no import table: all 13 libraries are statically linked (XAPILIB, UIX, XONLINE, D3D8, D3DX8,
XKBD, XGRAPHC, DSOUND, XBOXKRNL, XVOICE, VOICMAIL, LIBCP, LIBC — every one declaring version `1.0.5849`),
so the renderer, audio, networking and the C runtime are all *inside* the address space above. That is
why the module list above has `D3DDrv` and `XboxAudio` entries: they are not DLLs being called, they are
code in this image (`docs/IMPORTS.md`).

## 3. The object model

UnrealScript objects are C++ objects with a table, and the table is the whole interface.

```
UObject                    0x00077860 (constructor), 0x6c-byte instance
  +0x00  vtable  ->  class table (492 of them, 30..159 slots)
  vtable +0x54  (slot 21)  =  0x0004bf20   ProcessEvent      <- in 492/492 tables
```

`ProcessEvent` takes the script function's `FFrame` and runs the bytecode. Its by-name twin sits at
`0x00064770`, the binder at `0x0003a030`, and `FFrame::Step` — the interpreter loop — at `0x000398e0`
(1,583 bytes). `UObject::ProcessInternal` at `0x00048bf0` is the native half the VM calls into. Those
five addresses are the entire script/native boundary; everything else in `docs/NATIVE_VM.md` is detail
behind them.

Properties are a parallel structure, not part of the object header: a chain of `FProperty` instances
with a stride and a count, 15 classes covering the type space (bool, byte, int, float, object, name,
string, class, array, struct, vector, rotator, map, fixed array, delegate). Field offsets and sizes are
measured in `docs/NATIVE_PROPERTIES.md` §2.

The same layout is what a saved checkpoint stores: a `FPropertyTag` stream — name index, type byte with
a size class in bits 4–6, optional struct name, size, array index, value — read at `0x0003ceb0` and
written at `0x0003cd70` (`docs/NATIVE_PROPERTIES.md` §6–7, cross-checked against UELib). That is why the
nine shipped `.sgs` checkpoints can be parsed with no SDK: the format is the property system, and the
property system is measured.

## 4. The script layer

Packages register their classes at load time; the class table list in §2 is the result. The VM's opcode
dispatch is a table of native functions indexed by declared opcode — 273 declared slots, measured by
seeding candidates and checking which ones the interpreter reaches at runtime (`inventory/gnatives_slots.csv`,
`docs/NATIVE_VM.md`). An early version of that table was wrong: it had captured the CRT's initialiser
array, and the correction is recorded in the same document rather than quietly replaced.

Names are interned in a 4,096-slot table at `0x006ba0c8`, initialised at `0x0005c080`, with 325
registrations and indices 1–16 reserved for the property type names. There is no `GPropPointers`
equivalent: the property classes are reachable through the 15 static constructors, not through a global
array (`docs/NATIVE_PROPERTIES.md` §2).

## 5. Content

| what | count | where |
|---|---:|---|
| mission maps | 61 | `inventory/maps.csv` |
| multiplayer maps | 10 | `inventory/content_census.csv` |
| map actors (mission side) | 31,574 | `docs/CUT_CONTENT.md` |
| classes only used in multiplayer maps | 22 | `docs/CUT_CONTENT.md` |
| classes only used in mission maps | 279 | `docs/CUT_CONTENT.md` |
| speech WAVs | 1,364 | `inventory/speech_index.csv` |
| music WAVs | 1,923 in 16 groups | `inventory/music_index.csv` |
| save checkpoints | 9 | `inventory/sgs_schema.csv` |

The content layer is where the cut content shows: M01, M05 and M11 have music but no map, and the
multiplayer maps use a 22-class vocabulary the campaign never touches (`docs/CUT_CONTENT.md`). The
mission/multiplayer split is measured by joining actor classes per map, not by reading design documents.

## 6. Execution

What is proven: the image boots and runs. `tools/xemu/smoke.py` samples `EIP` over the boot window and
requires the guest to leave the kernel's address space — a real run gives 7 of 8 samples in user space
across 6 distinct addresses (`docs/XEMU.md`). The image is mapped at `0x00010000`, where the guest reads
back a valid executable header (`4d 5a 90 00`) over the QMP monitor.

What is **not** measured: the init call graph between the XBE entry point and the first frame. The five
VM addresses in §3 are the script boundary, but the path from `main` to package mounting to the menu has
not been walked function by function, so this document does not describe it. That is the next structural
question, and it needs either the Ghidra run extended over the launcher region or breakpoints through the
GDB stub — not inference from other UE2 titles.

## 7. Where the seams are

| seam | state |
|---|---|
| script ↔ native | measured (VM entry points, dispatch table, property layout) |
| native ↔ content | measured (package and map parsing, save format) |
| native ↔ hardware | measured as a library list; no call graph per library yet |
| script ↔ gameplay systems | **blocked**: needs the 2006 `Classes` oracle for system-level write-ups (PLAN 8.3) |
| content ↔ assets | **blocked**: mesh/texture export needs an external tool (PLAN 5.1) |

## 8. Checking a claim

| claim | check |
|---|---|
| function count, names, sizes | `make ci` → `symbol-map` check; or `wc -l symbols/xbox_functions.csv` |
| class table count and layout | `python3 tools/class-tables.py` against the dump |
| package sizes and classes | `make inventory` then `inventory/packages.csv` |
| save format | `docs/NATIVE_PROPERTIES.md` §6–7 against a `.sgs` file |
| boot | `python3 tools/xemu/smoke.py --iso=… --bios=… --hdd=…` |
