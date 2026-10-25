# The packages carry their own source

The most consequential thing found so far, and it came out of the export table: **every class in the build
ships its original UnrealScript source as a text export inside its own package.** Not a decompilation — the
text the compiler consumed.

    python3 tools/pkg-exports.py PKG.u --class SGOrder

```
SGOrder: export #94, 34 children
  ScriptText             TextBuffer     size=5857   offset=0x001aba9b
  assignBots             Function       size=268    offset=0x0007fefd
  Initialise             Function       size=221    offset=0x0008134a
  ...
  kinds: {'Function': 10, 'ArrayProperty': 2, 'ObjectProperty': 2, 'TextBuffer': 1, 'StrProperty': 1, ...}
```

Every class has exactly one `ScriptText` child, and it is a `TextBuffer` export — the engine's own container
for UnrealScript source.

## The count is exact

| | |
|---|---:|
| classes in the build | **2,319** |
| `Class` exports | **2,319** |
| `ScriptText` exports | **2,319** |
| source bytes | **6,149,951** (5.9 MiB) |

Per package, the three numbers agree everywhere — `StargateGame` 306/306/306 with 1,840,507 bytes of source,
`Engine` 367/367/367 with 1,769,955, `Core` 7/7/7. `inventory/package_script_text.csv` holds all 18 rows.

Source volume is small enough to be plausible and large enough to be real: 5.9 MiB over 2,319 classes is
2.6 KB per class, which is what a class declaration plus a few functions looks like — not a codebase, and not
nothing. `SGOrder`'s 5,857 bytes against 10 functions and 22 properties matches that ratio.

## What this changes

1. **The oracle problem gets much better.** `docs/ORACLE_DIFF.md` compares the decompiler's output against
   the privately held 2006 `Classes` tree, and could only check 291 classes. The packages themselves contain
   the text for all 2,319, including the 204 that agree and the 56 that drift — so drift can now be checked
   against what shipped, not against a tree from a different date.
2. **3.7's bytecode comparison stops being a guess.** If the exact source that produced the shipped bytecode
   is available, then compile-it-and-compare is a real experiment: same input, same compiler generation,
   compare function bytecode. Without the text, any recompilation differs by unknown amounts of source drift.
3. **The decompilation track's value changes.** UELib's decompiler output is no longer the only readable
   source; it is a cross-check on the shipped text. That is the reverse of the project's current framing.
4. **`ScriptText` explains a pile of things** in `docs/DECOMPILER_GAPS.md`: cases where the decompiler
   produced something odd are now checkable against the original line.

## Handling: measured here, extracted elsewhere

The text is game-derived. Per `AGENTS.md` and `docs/LEGAL.md` it must not be committed: what is committed is
the **counts and sizes** above. Extraction writes to `$WORK` on the machine that owns the dump, and the
repository only ever cites byte sizes and the tool that reads them.

That is not a technicality — it is the difference between an inventory and a copy of the game's source.

## Not measured

* The text itself, by policy, and therefore its quality: whether `ScriptText` is the final source, an
  intermediate, or a stripped variant. The sizes are consistent with real source; only reading it (in
  `$WORK`) would settle it, and that is a deliberate next step for whoever owns the dump.
* Whether the text is byte-identical to what the compiler consumed. The package stores what the build wrote;
  a compiler may have normalised line endings or comments.
* Function bodies. The export table gives each function's name, size and byte range, but no `UFunction`
  record or bytecode is decoded yet, so "native vs script" is still not separable from the table alone —
  `docs/systems/UCPP_NATIVES.md` records the same gap from the other side.
