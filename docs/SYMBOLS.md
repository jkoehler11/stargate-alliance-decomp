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
