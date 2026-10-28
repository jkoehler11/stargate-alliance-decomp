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

## The leading records are a bind pose, one per channel

`tools/lipsinc-index.py` (`make lipsinc-index`) measures the payload without guessing at it. After the channel table
there is a **10-byte prelude** — `532` (u32), `0` (u8), then `0x21500` for 21 channels or `0x21300` for 19 (u32),
`0` (u8), so the second word packs the channel count with a flag rather than being a plain count — and after the
prelude comes a **pose block: exactly one 32-byte record per channel name**, in every one of the eight files.

| file | channels | pose block starts | poses | animation payload after it |
|---|---:|---:|---:|---:|
| `sg1_crew_jackson.lad` | 21 | 395 | 21 | 12,896 |
| `sg1_crew_carter.lad` | 21 | 395 | 21 | 26,311 |
| `JAnderson.lad` | 21 | 395 | 21 | 33,073 |
| `Controllers/grey.lad` | 21 | 395 | 21 | 39,583 |
| `Controllers/sg1_crew_tealc.lad` | 19 | 362 | 19 | 649,732 |
| `Controllers/sg1_crew_jackson.lad` | 19 | 362 | 19 | 1,067,713 |
| `Controllers/sg1_crew_carter.lad` | 19 | 362 | 19 | 1,300,403 |
| `Controllers/sg1_crew_oneill.lad` | 19 | 362 | 19 | 1,831,589 |

The block is recognised, not assumed: `inventory/lipsinc_pose.csv` (one row per file, `--check` keeps its invariants)
counts the leading 32-byte records that look like a pose — three plausible floats followed by a `-1.0f` at bytes
24–27 — and stops at the first one that does not. On all eight files that count is exactly the channel count, and the
first pose is the *same* in every file: `(3.0464, 8.6571, 10.3889)` for channel 0, `Controllers/` files included. A
shared bind pose for a shared skeleton, then per-character animation.

A pose's 32 bytes, at the observed offsets:

| bytes | content |
|---|---|
| 0–11 | three float32, small and signed (`\|x\| < 12`), the bind position |
| 12–23 | twelve bytes that are **not** float data — `80000000` (`-0.0f`) and words that read as byte pairs when printed as hex |
| 24–27 | `-1.0f` |
| 28–31 | `2` (u32) |

**Correction to an earlier reading of this page.** A previous revision called the whole body a 32-byte-strided record
stream, because the `-1.0f` recurs at 32-byte steps and only 352 of 424 records in the smallest clip did not fit. That
stopped being defensible once the count was compared with the channel table: the stride holds **inside the pose
block** (21 or 19 records) and nowhere else. The old `records` and `residue_bytes` columns in
`inventory/lipsinc_lad.csv` are the body length divided by 32 and its remainder; the remainder is the signal that the
stride fails, not a damaged final record. They are kept because they bound the payload's size, but they no longer
claim a layout.

What the animation payload *is* remains unread: it is 96–99% of every file and it does not stride at 32. That is
U4's remaining half.

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

## The runtime side, located

The loader is native, but it is not anonymous: the module logs through **43 `[LIPSinc]:` strings**, and the image
still carries its own source path as an ASCII assert —
`\alienbrainWork\Stargate-sg1\Game\GameXBOX\Engine\Src\LIPSincSupport.cpp`. `tools/image-strings.py --topic lipsinc`
(`make lipsinc-runtime`, `inventory/lipsinc_runtime.csv`, `--check` in `make ci`) finds every `push <string>` of those
43 strings and attributes it to the function that contains it: **19 functions, 23,255 bytes**, in
`0x000b24f0`–`0x000ba750` plus three in the actor path (`0x00225cd0`, `0x00228730`, `0x0022e090`).

Nine of them name themselves, which is the convention this project names by (`docs/CONVENTIONS.md`: the name comes
from the string or assert site):

| address | bytes | name | the string that says so |
|---|---|---|---|
| `0x000ba750` | 1,860 | `LIPSincInitLevel` | `[LIPSinc]: LIPSincInitLevel`, `Prefix: %s`, `Searching for .lad files... %s`, `Num Controllers: %i` |
| `0x000ba290` | 563 | `LIPSincInitGame` | `[LIPSinc]: LIPSincInitGame` |
| `0x000ba520` | 370 | `LIPSincShutdownLevel` | `[LIPSinc]: LIPSincShutdownLevel`, `UnBinding Controller for %s` |
| `0x000ba720` | 39 | `LIPSincShutdownGame` | `[LIPSinc]: LIPSincShutdownGame` |
| `0x000b9e90` | 1,015 | `LIPSincLoadDBFromDisk` | `[LIPSinc]: LoadLIPSincDBFromDisk with %i files` |
| `0x000b8340` | 4,977 | `LIPSincReadAnimFile` | `Read Track %s`, `ERROR: Couldn't load file %s.`, the LTF version/validity errors |
| `0x000b7260` | 4,290 | `LIPSincReadControllerFile` | `Parsed %s`, `Attempt to import an obsolete .LBP file!`, the bone-linkup warnings |
| `0x00225cd0` | 164 | `LIPSincLinkBones` | `Linking Bones for %s` |
| `0x00228730` | 521 | `LIPSincPlayAnim` | `PlayLIPSincAnim for %s` |

The other ten carry only pose/transform warnings (`WARNING: pDefBonePose`, `pTargetTransform`, `pBaseTransform`,
`ERROR -> bone %d doesn't exist in base set!`) or the key-lookup assert
`ValuesAtTime, m_nEndTime = %i, time = %f`, so they are located but deliberately unnamed: a warning does not name a
function the way a lifecycle or I/O log line does.

**What this says about the records.** `LIPSincInitLevel` enumerates `L"*.lad"` under a prefix and hands each file to
the two readers above; `LIPSincReadAnimFile` is the *track* reader (`Read Track %s`). A bind pose followed by an
animation payload is exactly what such a reader needs: `ValuesAtTime, m_nEndTime = %i, time = %f` in `0xb24f0`
interpolates between keys up to an end time, and the pose block is the state those keys are relative to. That the
block is one record per channel — and that the rest of the file is not laid out like it — is the measured part; which
fields of the animation payload hold time and which hold value is still U4.

The names are **not applied to the symbol map yet**: that map is regenerated from `symbols/xbe_overrides.csv` by a
Ghidra run, so the rename is its own commit. `inventory/lipsinc_runtime.csv` is the evidence list for it.

## Status

- Header + channel table: parsed in all 8 files, no exceptions.
- Payload: the leading pose block is measured (one 32-byte pose per channel); the animation data after it
  is not 32-byte strided and is unread.
- `Mappings/*.map`: parsed, text, 43 phonemes × 15 targets, identical across characters.
- Runtime: 19 functions located from the module's own log strings, 9 self-evident names proposed (not applied).
- Consumer: native (PLAN 4.8); the script-facing API is the five `LIPSinc` natives above.
