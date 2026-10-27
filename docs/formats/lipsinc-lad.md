# `.lad` — LIPSinc lip-sync data

**Container:** opaque little-endian binary, no magic. 8 files, in two shapes:

| file | bytes |
|---|---|
| `System/LIPSincData/JAnderson.lad` | 34,140 |
| `System/LIPSincData/sg1_crew_carter.lad` | 27,378 |
| `System/LIPSincData/sg1_crew_jackson.lad` | 13,963 |
| `System/LIPSincData/Controllers/grey.lad` | 40,650 |
| `System/LIPSincData/Controllers/sg1_crew_tealc.lad` | 650,509 |
| `System/LIPSincData/Controllers/sg1_crew_jackson.lad` | 1,068,447 |
| `System/LIPSincData/Controllers/sg1_crew_carter.lad` | 1,301,681 |
| `System/LIPSincData/Controllers/sg1_crew_oneill.lad` | 1,832,579 |

The name is the studio's lip-sync middleware (LIPSinc), and the runtime side is native: `Engine.Actor`
declares `PlayLIPSincAnim`, `HasLIPSincAnim`, `CurrentLIPSincAnim`, `IsPlayingLIPSincAnim`,
`StopLIPSincAnim` as natives with no serialised index (`inventory/natives.csv`), so the loader is in
the XBE, not in script.

## What is measured (all 8 files)

The header and the channel table parse identically in every file, which makes them safe to rely on:

| offset | type | field |
|---|---|---|
| `0x00` | u32 | format version — `2` in all eight files |
| `0x04` | u32 | second header word — `2` in all eight files; meaning not established |
| `0x08` | u8 | channel count `n` — 21 in the five speech clips, 19 in the four controller packs |
| `0x09` | `n` × (u8 length + name) | channel names, each a NUL-terminated ASCII string; they are bone names (`BN-Blink_L`, `BN-Jaw`, `BN-Head`, …) |
| — | — | the table ends at `0x181` for `n = 21` and `0x160` for `n = 19` |

Everything after the table is float-heavy keyframe data. Two measurements constrain it (both recorded
as leads, not as a layout):

- the payload opens with two integers: `532` in all eight files, then `0x21500` when there are 21
  channels and `0x21300` when there are 19 — the second byte of that word *is* the channel count, so
  the field packs a count with something else;
- the last 128 bytes of every file end in `-1.0f` sentinels and a run of `undefined` name strings.

File size tracks clip length, not channel count: the `Controllers/` packs are 16–130× larger than the
speech clips while carrying *fewer* channels (19 vs 21), so they hold far more frames.

**The record structure is measured; the meaning of its fields is not.** The stride, the prelude and the trailer
are below, and the parse is reproducible, but what the three floats *are* needs either the middleware's own
documentation or the loader from PLAN 4.8 — it is tracked as U4 in `docs/UNKNOWNS.md`.

## The record stream is 32 bytes per record

`tools/lipsinc-index.py` (`make lipsinc-index`, `inventory/lipsinc_lad.csv`) measures the payload without guessing
at it. Three things fall out:

* after the channel table there is a **10-byte prelude**: `532` (u32), `0` (u8), then `0x21500` for 21 channels or
  `0x21300` for 19 (u32), `0` (u8) — the second word's low byte is zero and its *second* byte is the channel count,
  so it packs the count with a flag rather than being a plain count;
* then a **32-byte-strided record stream**. The `-1.0f` that occupies a record's last-but-four bytes recurs at
  exactly **32-byte** steps — checked in `grey.lad`, `sg1_crew_jackson.lad` and `Controllers/sg1_crew_tealc.lad`,
  352 of 424 records in the smallest clip carrying it at a stride of 32 with no exceptions in the run;
* the stream ends in a **block of three names**, the same in all eight files: `BN-Head`, `undefined`, `undefined`,
  each length-prefixed like the channel table.

A record's 32 bytes are, at the observed offsets:

| bytes | content |
|---|---|
| 0–11 | three float32, all small and signed (|x| < 5), non-monotonic across records — so not a time |
| 12–23 | three float32 near zero (1e-30 … 1e-15): they vary per record but stay denormal |
| 24–27 | `-1.0f` in most records |
| 28–31 | `2` in most records |

| file | records | bytes after the last whole record |
|---|---|---|
| `sg1_crew_jackson.lad` | 423 | 11 |
| `sg1_crew_carter.lad` | 842 | 18 |
| `JAnderson.lad` | 1,053 | 28 |
| `Controllers/grey.lad` | 1,257 | 10 |
| `Controllers/sg1_crew_tealc.lad` | 20,316 | 14 |
| `Controllers/sg1_crew_jackson.lad` | 33,377 | 0 |
| `Controllers/sg1_crew_carter.lad` | 40,665 | 18 |
| `Controllers/sg1_crew_oneill.lad` | 57,256 | 4 |

The last column is the honest part: on seven of the eight files the record stream does not end on a stride boundary,
so the final record (or the trailer's prefix) is not laid out like the others. It is reported per file rather than
rounded away, and it is what the "trailer length" question below is really about.

## The `.map` files are the viseme set, and they are text

`System/LIPSincData/Mappings/*.map` are **plain text**, four files of ~4.2 KB, an INI whose
`[PhonemeToSpeechTargetMapping]` section holds `NumSpeechTargets=15` and one line per phoneme —
`Iy=0.85, 0.00, …` — with the fifteen targets named in the comment header (`Eat Earth If Ox Oat Wet Size Church
Fave Though Told Bump New Roar Cage`). **43 phonemes × 15 targets**, and the weights take only five values
(`0`, `0.2`, `0.5`, `0.7`, `0.85`): a hand-authored mapping, not measured data.

Two findings from parsing all four (`inventory/lipsinc_phonemes.csv`, 2,580 rows):

* the four maps carry **identical weights** — 0 of 645 entries differ between `carter`, `jackson`, `oneill` and
  `tealc` — so the per-character files are copies and the character differentiation lives in the `.lad` controller
  packs, not here;
* the vectors are almost one-hot: a phoneme maps to one target at 0.85 (or 0.7 for `F`/`V`, 0.2 for `G`/`K`) with the
  rest at zero, which is why 15 targets are enough for 43 phonemes.

## Status

- Header + channel table: parsed in all 8 files, no exceptions.
- Payload: strided record stream measured (32 bytes), field meanings not established.
- `Mappings/*.map`: parsed, text, 43 phonemes × 15 targets, identical across characters.
- Consumer: native (PLAN 4.8); the script-facing API is the five `LIPSinc` natives above.
