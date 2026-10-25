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
layout. It has to be solved to a fixed point. That work is not done; `--layout` is what makes it tractable.

## Status

* `--layout` — done, census in `inventory/package_layout.csv`.
* `--relay` — done: every package is re-laid through the writer's placement path (header, names, **payloads**,
  imports, exports, with the header's two table offsets patched) and comes out **identical, 18 of 18**. That is the
  placement algorithm proven end to end, not an assumption.
* `--grow` (enlarge one payload by N bytes) — **not offered**. Providing a non-zero `extra` makes the re-encoded
  export table mis-decode its first entry, so the re-parse fails with unresolved classes; the symptom is localised
  (entry 1 reads garbage, later entries shift by one), the cause is not yet established. The mode that ships is the
  one that is proven.

Correction to the record: the previous PR said this feature had been reverted. It had not — the broken code was
still on the branch and merged, and the flag simply could not pass its own check. This PR replaces it with the
placement pass that works and removes the claim that it did not exist.

