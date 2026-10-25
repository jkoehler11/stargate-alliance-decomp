# Writing a package back: the tables are proven, the payloads are not

PLAN 6.1 needs a package writer, and the honest way to build one is to regenerate what we claim to understand and
diff it against the bytes the retail compiler produced. That is what `tools/pkg-write.py` does.

    python3 tools/pkg-write.py DUMP/System     # re-encode every package's tables and diff
    python3 tools/pkg-write.py --selftest      # index codec only, no dump; runs in `make ci`

## Result

**18 of 18 packages re-encode their name, import and export tables byte for byte.**

| | entries | what it proves |
|---|---:|---|
| names | 37,094 | length prefix counts the NUL, flags are four trailing bytes |
| imports | 7,500 | `ClassPackage`/`ClassName`/`ObjectName` are compact indices, `Package` is a plain `uint32` |
| exports | 57,749 | `Class`/`Super`/`ObjectName`/`SerialSize`/`SerialOffset` compact, `Outer` and `Flags` plain `int32` |

57,749 export entries and 37,094 names regenerating exactly is not a plausibility argument — it is the strongest
evidence available short of a rebuild, and it settles the layout questions that took this project six attempts
(`docs/PACKAGE_READER.md`). The compact index needed its own encoder (sign in bit 7 of the first byte, continuation
in bit 6, then 7-bit groups); `--selftest` round-trips it over sign and multi-byte cases so the codec can be checked
with no dump present.

The comparison reports the **first differing offset**, not just "differs": `Engine.u exports DIFF at +0x2a975e` is
information, `exports differ` is not.

## Rebuilding the whole file

`--rebuild` goes further than the tables: it assembles a complete file from the header region, the three
regenerated tables and every export payload placed at its own `SerialOffset`, then diffs the result against the
original.

**18 of 18 packages rebuild byte for byte, with 100% of bytes accounted for and zero gaps** — 8.2 MB of package
data assembled from our own model rather than copied wholesale. The gap report is the part that matters: it lists
any byte range that header, tables and payloads do not explain, so a future writer's mistakes show up as a
*location* instead of a mysterious diff. Here there are none, which also means the payloads tile the file exactly
— no alignment padding between exports.

What that upgrades: the tables being identical (previous section) proves the *tables*; whole-file identity proves
the **offsets, sizes and serial layout** as well, because recomputing the layout from the parsed model is what
produced the bytes.

What it still does not do: writing payloads we do not have. A rebuild of the game needs a compiler to produce
export data; this proves the scaffolding around it, which is the part 6.1 can settle without one.

## Re-laying through the writer's own placement

`--relay` does not copy: it rebuilds each file as header, name table, **payloads**, then the import and export
tables, patching the header's `export_offset` and `import_offset` to the new positions (`docs/PACKAGE_LAYOUT.md`).
It must reproduce every package byte for byte, and it does — **18 of 18**.

That is a stronger statement than `--rebuild`: `--rebuild` places regions at their original offsets, so it proves the
offsets are consistent; `--relay` *chooses* the positions from the model and only then checks the bytes, which is what
a writer will do when a payload changes size.

## What this does *not* say

* **Payloads are not written.** The export data (properties, `ScriptText`, bytecode) is copied, not generated. A
  rebuild gets those bytes from a compiler; the writer's job is the scaffolding around them.
* **The package summary is copied, not modelled.** GUID, generations, engine version, compression flags and the
  fields this project has never needed are carried through verbatim. A writer that had to *invent* a package would
  need those next, and nothing here measures them yet.
* **Nothing was loaded by the engine.** A byte-identical table is not a package the game accepts; that needs the
  rebuild path and xemu.

## Why this matters for the estimate

`docs/PATH.md` calls the compiler the critical path. It still is — but the part of 6.1 that could have been
unknowable turned out to be known: the container format is no longer a risk. What remains before a rebuild attempts
a boot is payload generation (a compiler's job) and the integrity-check bypass, which is decided
(`docs/PACKAGE_CHECK.md`).
