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

**The payload layout is not established.** Recording the container skeleton here is enough to
identify the files and to point the native work at them; the record structure needs either the
middleware's own documentation or the loader from PLAN 4.8, and it is tracked as U4 in
`docs/UNKNOWNS.md`.

## Status

- Header + channel table: parsed in all 8 files, no exceptions.
- Payload: not parsed. `tools/formats.py` classifies the extension as `binary` and stops there.
- Consumer: native (PLAN 4.8); the script-facing API is the five `LIPSinc` natives above.
