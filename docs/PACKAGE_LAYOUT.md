# How a package is laid out on disk, and the trap that hides in it

`tools/pkg-write.py --rebuild` proves that every package can be reassembled from header, tables and payloads
(`docs/PACKAGE_WRITER.md`). This page is the *order* those pieces sit in, which is not the order anyone assumes, and
which broke the first attempt at a writer that could change a payload's size.

    make pkg-layout     # tools/pkg-write.py DUMP/System --layout --write inventory

## Every package has the same order

    names < payloads < imports < exports

| package | names | payloads | imports | exports |
|---|---|---|---|---|
| `AmbientCreatures` | `0x44–0x1316` | `0x1316–0x1034d` | `0x1034d–0x1075d` | `0x1075d–0x11e7e` |
| `Core` | `0x44–0x1e03` | `0x1e03–0xfc2c` | `0xfc2c–0xfccd` | `0xfccd–0x1353d` |
| `Engine` | `0x44–0x2ea0b` | `0x2ea0b–0x2a925e` | `0x2a925e–0x2a975e` | `0x2a975e–0x2f28cb` |
| `StargateGame` | `0x44–0x25273` | `0x25273–0x2a9fa3` | `0x2a9fa3–0x2ae5fb` | `0x2ae5fb–0x2e427e` |

All 18 agree, and `inventory/package_layout.csv` carries the exact numbers for each. The header's three table offsets
say where the tables are; what no field says is where the **payloads** begin, and they begin immediately after the
name table — *before* both object tables, which sit at the end of the file.

## The trap

A writer that emits header, then names, then imports, then exports, then payloads — the obvious order, and the one
the writer code started with — produces a file whose export table is in the wrong place: the header still points at
`export_offset`, which on disk is past all the payload data. The result fails to parse, and the error is a
`struct.error` at the end of the file rather than anything that names the mistake:

```
struct.error: unpack_from requires a buffer of at least 734653 bytes for unpacking 4 bytes at offset 734649
(actual buffer size is 734651)
```

That message cost a session's worth of iterations on the wrong hypothesis (offset arithmetic, compact-index widths,
region lengths) before the layout census made it obvious: the tables were in the wrong *place*, not the wrong
*size*. **Read the layout before writing the writer.**

Consequence for PLAN 6.1: a rebuild that changes payload sizes must relocate the two object tables — and because
shifting an export offset can need one more byte in the compact index, the table length is itself a function of the
layout. It has to be solved to a fixed point.

## The resize writer

`--resize` does that: it re-lays a whole file around one payload resized by N bytes. Three things, in the order they
bite:

1. **Place regions in the order they occupy** — header, names, payloads, imports, exports. The payload block begins
   immediately after the name table, and a table emitted before it pushes every offset past its data.
2. **Moving a region means rewriting the header.** `name_offset`, `export_offset` and `import_offset` (0x10, 0x18,
   0x20) are the only pointers to the tables; relocating the data and leaving them stale does not error, it
   mis-decodes the entries.
3. **Iterate the layout to a fixed point.** A payload position feeds the export table, whose encoded length grows when
   a size crosses 0x3F or an offset crosses 0x3FFF. On these 18 packages the export table is last, so it settles in one
   pass — but the order is read from the header rather than assumed, and the tool refuses to emit a file that does not
   converge.

    make pkg-resize                      # --resize 64 --write inventory; RESIZE_BYTES to change it

**Result: 18 of 18 packages, 84 sampled resizes, grow-then-shrink byte-identical** (`inventory/package_relayout.csv`).
The sample per package is the extremes plus the export nearest a 0x3F size and a 0x3FFF offset, because those are the
entries whose width the move can change. Each resize must also re-parse **from disk** through the reader, leave every
other payload byte-identical, and have its own output rebuild from its own tables.

A shrink is not reversible — it discards the payload's tail — so the negative direction is checked against the
container instead of against bytes. And the resized payload's *contents* are meaningless (fill appended, or a truncated
tail): this proves the container around a payload, not the export inside it.

## Status

* `--layout` — done, census in `inventory/package_layout.csv` (committed; `make pkg-layout` regenerates it).
* `--relay` — done: every package is re-laid through the writer's placement path with a zero delta and comes out
  **identical, 18 of 18**. It runs through the same function as `--resize`, deliberately: a placement rule that drifts
  between the "prove it" mode and the "move it" mode would make the proof describe code that no longer runs.
* `--resize` — done: relayout around a resized payload, fixed point included, round-trip proven 18/18
  (`inventory/package_relayout.csv`).

Correction to the record, again: the previous two revisions of this page described `--grow` as reverted, then as
shipping-but-unproven with growth "not offered" because a non-zero `extra` mis-decoded the first export entry. Driving
the merged `regrow` with `extra=64` at export 1 does **not** reproduce that — `GamePlay.u` 334,722 → 334,786 and
`Core.u` 79,165 → 79,234 both re-parse with zero unresolved classes — so the earlier symptom was narrower than the
wording (a particular export, or the pre-placement version of the function). What was actually missing was not
arithmetic but **verification**: a re-read from disk, a payload-by-payload comparison, and a grow-then-shrink round
trip. Those are what `--resize` carries now, on the boundary cases a naive offset shift gets wrong.

