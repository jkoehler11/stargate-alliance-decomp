# The symbol map (`symbols/xbox.map`)

Status: emitted and checked. **15,423 symbols** — 14,957 functions plus 466 class tables — every one
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
- **dispatch and override names** derived from the build's data (`ucpp_Register_<Class>`, 39
  registrations, plus `appAssertFailed` and `appMallocBeforeInit` named from the strings they log);
- **`Vtbl_<Class>`** for the 466 class tables named by the class registrations (`docs/NATIVE_CLASSES.md`);
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
  falls outside the section table: measured **15,423 of 15,423 inside, 0 outside**.
- **Names are file-level, not unique.** 128 names are used by more than one function — the assert
  attribution names a function by its source file, so `Core_UnTemplate` covers 310 of them,
  `Engine_UnActor` 97, `Core_UnClass` 73. The `.map` keeps the build's own text; the assembled `.s`
  gives every duplicate an `_<address>` suffix (1,168 symbols affected) so a debugger can address
  them separately, and the count is printed on every run.
- **12,971 functions are still `FUN_*`.** They are in the map — a debugger needs the address even
  without a name — and naming them is 4.2's blocked signature work (`docs/UNKNOWNS.md` U9), not a gap
  in this file.
- **No types, no variables, no source lines.** The map carries code addresses and class-table
  addresses only; there is no debug information in this build (`RSDS` absent, PLAN §0).

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
