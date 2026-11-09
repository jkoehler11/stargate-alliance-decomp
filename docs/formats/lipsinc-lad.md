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

## The container in the loader's own order

`tools/lad-parse.py` (`make lipsinc-parse`, `inventory/lipsinc_parse.csv`, `--check` in `make ci`) reads a `.lad`
the way the loader does: it replays the order in which `FUN_000bce40` serialises its controller object
(`docs/formats/lipsinc-runtime.md`) instead of recognising a section by its shape. Each of that function's five
stages is one leaf of the grammar:

| stage | what it serialises | which section |
|---|---|---|
| `FUN_00037110` ×2 | four raw bytes each | `0x00`, `0x04`: version `2`, `2` |
| `FUN_00035b60` (element `FUN_00058850`) | an array of strings | `0x08`: the channel table, `n` names |
| `FUN_000bc470` (record `FUN_000b1bf0`) | an array of `{u32, array of 32-byte records}`; a record is a tag, a position and a quaternion | the pose block: 20 frames × `n` records |
| `FUN_000bcb60` | an array of `{u32, bone names, pose array, name}` | the clip section |
| `FUN_000bcd30` | an array of `{u32, name, Map.Anim, u32, f32, f32, key block}` | the track table |
| `FUN_00037110`, `FUN_00058850` ×3 | a `u32` and three strings | the tail: `2`, `BN-Head`, `undefined`, `undefined` |

and the file is

    lad    := u32 version, u32 version2, names, frames, clips, tracks, u32, string x3
    names  := count, count x string
    frames := count, count x { u32, count, count x pose }    pose := u32 2, 3 x f32 (position), 4 x f32 (quaternion)
    clips  := count, count x { u32, names, frames, string }
    tracks := count, count x { u32, string, string, u32, f32, f32, groups, groups x key-group, 0x00 }

where `count` is the loader's 7-bit varint — `FUN_000637e0`, the same reader the key counts and the track table's
declared extent already use: bit `0x40` in the first byte means a second byte follows and contributes `<< 6`, bit
`0x80` is a sign. Every count in the file is one of those, and the byte at `0x08` the earlier reading called a `u8`
channel count is simply the smallest of them.

**All eight files walk to their last byte** under that grammar, and the walk refuses a file whose bytes do not fit,
naming the offset. The counts agree with the shape-matching tools where the two overlap — 2,231 tracks, 58,006 key
groups and 263,900 keys, the same totals `lad-tracks.py` and `lad-keys.py` counted — but nothing here is found by
pattern:

| file | channels | pose frames | records | clips | tracks | key groups | keys | tail |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `sg1_crew_jackson.lad` | 21 | 20 | 420 | 0 | 0 | 0 | 0 | 35 |
| `sg1_crew_carter.lad` | 21 | 20 | 420 | 0 | 2 | 52 | 813 | 35 |
| `JAnderson.lad` | 21 | 20 | 420 | 0 | 3 | 78 | 1,222 | 35 |
| `Controllers/grey.lad` | 21 | 20 | 420 | 0 | 3 | 78 | 1,627 | 35 |
| `Controllers/sg1_crew_tealc.lad` | 19 | 20 | 380 | 4 | 344 | 8,944 | 32,823 | 35 |
| `Controllers/sg1_crew_jackson.lad` | 19 | 20 | 380 | 3 | 485 | 12,610 | 57,316 | 35 |
| `Controllers/sg1_crew_carter.lad` | 19 | 20 | 380 | 4 | 608 | 15,808 | 69,966 | 35 |
| `Controllers/sg1_crew_oneill.lad` | 19 | 20 | 380 | 6 | 786 | 20,436 | 100,133 | 35 |
| **all eight** | **160** | **160** | **3,200** | **17** | **2,231** | **58,006** | **263,900** | **35 each** |

**Corrections to the earlier reading of this page.** The payload does not open with `532`/`0x21500`; those bytes are
`14 02 00 00 00 15 02 00 00 00`, i.e. a varint `20` (the frame count), a `u32 2`, the varint `21` (the channel count)
and another `u32 2` — the first frame of the pose block. Read as `u32`s that landed the pose block four bytes late,
which is also why the old record table put its trailing `2` at bytes 28–31: that word is the *next* record's leading
field. And the "32-byte-strided runs with gaps of 37" were a scan over walked data: 37 is one 32-byte record plus the
5-byte header that opens the next frame.

## The pose block, and what a record holds

The block is **20 frames of one 32-byte record per channel** (21 in the speech files and `grey`, 19 in the controller
packs) — 3,200 records in the corpus — and each frame opens with a `u32 2` before its record array. The frames are not
copies of each other (247 of `sg1_crew_jackson.lad`'s 420 records are distinct), but frame 0's record for channel 0 is
the same three floats in all eight files, `(3.0464, 8.6571, 10.3889)`: a shared bind position for the shared skeleton.

A record, in the loader's order (`FUN_000b1bf0` is its serialiser):

| bytes | content |
|---|---|
| 0–3 | `u32` `2` |
| 4–15 | three float32, small and signed (`\|x\| < 12`) — the bind position |
| 16–27 | three float32 — the quaternion's vector part (denormal-small at rest, not zero) |
| 28–31 | the quaternion's `w`: `-1.0f` at rest |

The last four floats are a **unit quaternion, in the negated convention**. `tools/lad-bones.py`
(`make lipsinc-bones`, `inventory/lipsinc_bones.csv`, `--check` in `make ci`) reads all seven floats of every
record: `|q| = 1` to within `5.5e-7` in **all 3,200**, `w` is never positive (the stored rotation is the
canonical one negated, which is the same rotation), and the rest pose's words `6.3e-30, -0, 3.6e-30` are its
`x, y, z` — denormal-small rather than zero, which is why four earlier readings looked at them as integers.
So a record is a position plus a rotation about an axis, `angle = 2*atan2(|v|, -w)`: the `-1.0f` at rest is a
zero rotation, the corpus maximum is `77.0` degrees, and 522 of the 3,200 records turn at all — 506 about
`±X` and 16 about `(0.008, 0.239, 0.971)`. The angles land on a **whole-degree** grid in 97.6% of the corpus
and take 42 distinct values, so the pose block is a pose set rather than a curve. Two corrections follow: the
"three dwords that are not float data" are the quaternion's vector part, and the angle an earlier page read
as `-cos(theta)` with `acos` is **twice** that (`w = -cos(theta/2)`), which halved every angle and made the
grid look like half a degree.

`FUN_000b1bf0` reads those fields but cannot name them: `FUN_00037110` and `FUN_00037170` are **byte-identical**
(48 bytes, a 4-byte read through the archive's `Serialize` plus a running byte counter), so the serialiser says
"one tag and seven 4-byte scalars" and the 3 + 4 grouping is measured from the data. Its eight reads are the
confirmation, not the evidence.

## The clip section is in the container, and a clip carries its own bones

`FUN_000bcb60`'s array holds the clips: 17 across the four controller packs, none in the five speech files, and each
one is `{u32 2, an array of bone names, 20 frames of one record per listed bone, its own name as a string}`. The bone
lists are a subset of the channel table — 19, 11, 9, 5 or 3 bones — so a clip animates the bones it needs rather than
all channels: `oneill` carries `default`, `frown`, `yelling`, `happy`, `oneill_perplexed`, `oneill_squint` at 19, 5,
19, 11, 5 and 3 bones, each 20 frames deep. A clip's frames are indexed by the *clip's* bone list, not by the channel
table, so the same 32-byte record layout appears at two strides in one file.

## The track table

`FUN_000bcd30`'s array is the 2,231 `<Map>.<Animation>` records the track census found, and each record is
`{u32 2, short name, <Map>.<Animation>, u32 1, f32 160.0, f32 220.0, 26 key groups, 0x00}`: the short name is the
animation (`Beware`), the long one prefixes the level (`AlkeshBomber.Beware` — `AlkeshBomber` is the only map named
in all eight files), and `160.0`/`220.0` are the same two floats in every one of the 2,231 records, so they are a
default time range, not a measurement. The 26 groups and their keys are decoded below and in `inventory/lipsinc_keys.csv`.

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
- Body: **three parts, all located** — 20 pose frames (one 32-byte record per channel), a clip section in the four
  19-channel packs (17 clips, named, each 20 frames), and a track table of 2,231 `<Map>.<Animation>` records. The key
  blocks that follow each track header are 4.36 MiB across the corpus and decode as **26 groups of (time, value)
  keys** per track (`tools/lad-keys.py`, `inventory/lipsinc_keys.csv`).
- `Mappings/*.map`: parsed, text, 43 phonemes × 15 targets, identical across characters.
- Runtime: 19 functions located from the module's own log strings, 9 self-evident names proposed (not applied).
- Consumer: native (PLAN 4.8); the script-facing API is the five `LIPSinc` natives above.

## What a census of the payload can and cannot say (measured 2026-11-04)

`tools/lad-scan.py` measures the payload without interpreting it, and two rows of its output dispose of the two
heuristics this file used to lean on -- use `-1.0f` as a record terminator, and treat a fixed 12-byte tail as one:

| file | bytes | channels | `-1.0f` hits | of those, records (first word 2) | distinct 12-byte middles | strides |
|---|---:|---:|---:|---:|---:|---|
| `Controllers/grey.lad` | 40,650 | 21 | 352 | **352** | 91 | 32x151, 37x16, 64x12, 128x9 |
| `sg1_crew_jackson.lad` | 13,963 | 21 | 352 | **352** | 91 | identical to the row above |
| `Controllers/sg1_crew_carter.lad` | 1,301,681 | 19 | 1,139 | **1,139** | 148 | 32x616, 37x86, 128x27, 101x3 |

The first version of this census counted only the records that shared a file's *commonest* 12-byte middle -- 222 of
`grey.lad`'s 352 -- and called the rest noise. They are not noise: every `-1.0f` hit in that file is preceded by a
well-formed record, and the middles come in 91 distinct values across 163 contiguous runs, so the middle varies per run
rather than per file or per channel. What that middle *is* is open (see the loader section below).

**`-1.0f` alone over-counts**: 352 hits in a 21-channel file are not 352 records, and the extra hits are the four bytes
`00 00 80 bf` occurring by chance inside float data. A terminator needs *something* else to be identified by.

**A single 12-byte tail does not identify records either.** `grey.lad` and `sg1_crew_jackson.lad` are both 21 channels and
both yield 222 records on the same stride histogram, yet their tails differ -- and `grey.lad` is three times the size, so
the 222 are a *subset* of its records, not all of them. A file therefore carries more than one tail population, and the
"constant tail" idea is a property of the subset a file happens to repeat most, not of the format.

**Where that leaves U4.** The payload is not decodable by pattern-matching terminators; the facts above bound the problem
(a record stride of 32 bytes with 37-byte insertions, most of the payload covered) without naming a single field. The next
step is the one this project prefers anyway: read the loader. `LIPSincReadAnimFile` is at `0x000b8340` (4,977 bytes, named
from its own `Read Track %s` log string, `inventory/lipsinc_runtime.csv`) and its loop over the payload is what says which
bytes are time and which are value.

## The loader's record is eight floats, and the `-1.0f` is the eighth (measured 2026-11-04)

The census in the section above bounds the payload but cannot name a field, so the next step was the loader -- and the
loader says what a record *is*, in the one form that survives a mangled decompilation: its arithmetic.

`LIPSincReadControllerFile` (`0x000b7260`, 4,297 bytes, named from its own `Parsed %s` string) builds a pose array
indexed with a **32-byte stride** -- the index expression is `count * 0x20` plus the array base -- and fills one record
by writing a 4-byte value and then copying **eight consecutive floats** into it. The 4-byte value it writes first is the
integer **2** (`0x00000002`, which a float view renders as `2.8e-45`), and the copy overwrites it from the first float of
the source, so the eight floats are the record and the "2" is either the first float's value or a default the copy
replaces.

That identifies the payload's `-1.0f` for what it is: **the eighth float of a 32-byte record**, not a sentinel after one.
It also matches the census exactly -- the stride histogram's dominant 32 bytes, the trailing `-1.0f`, and the twelve bytes
between the third float and it are floats four, five and six, which this project had been calling "the tail".

What the eight floats *mean* is still U4, and the source of the numbers is the surprise: this reader converts wide
strings to floats (`__wtof`) before filling the array, so the runtime side parses text and the binary `.lad` payload is
that same 32-byte structure dumped -- which is why the file's records line up with the runtime array's stride. The
37-byte gaps in the census (five bytes past a record) are run headers of some kind and are the next thing to read.

**Not measured, and not claimed**: which of the eight floats is time, which are a rotation and which are a translation.
The decompilation of this function is heavily inlined logging, so the field order needs the *writer* -- the offline tool
that produced the `.lad` -- or a value-by-value check against a known clip.

## The five bytes past a record are a frame header, and a frame is one record per channel (measured 2026-11-09)

The census above left one question: `37 = 32 + 5`, so five bytes sit between some records and no record was five bytes
longer than another. `tools/lad-frames.py` (`make lipsinc-frames`, `inventory/lipsinc_frames.csv`) walks the body
instead of pattern-matching it, and the five bytes read as a **frame header**: the same `u32 2` that opens a record,
followed by a `u8` **equal to the channel count**, and after it exactly `channel_count` records of 32 bytes.

| file | channels | frames | records | records/frame | short frames | `-1.0f` records | body bytes unread |
|---|---:|---:|---:|---:|---:|---:|---:|
| `sg1_crew_jackson.lad` | 21 | 20 | 420 | 21 | 0 | 352 | 6 |
| `sg1_crew_carter.lad` | 21 | 20 | 420 | 21 | 0 | 346 | 13,421 |
| `JAnderson.lad` | 21 | 20 | 420 | 21 | 0 | 352 | 20,183 |
| `Controllers/grey.lad` | 21 | 20 | 420 | 21 | 0 | 352 | 26,693 |
| `Controllers/sg1_crew_tealc.lad` | 19 | 20 | 380 | 19 | 0 | 323 | 637,865 |
| `Controllers/sg1_crew_jackson.lad` | 19 | 20 | 380 | 19 | 0 | 319 | 1,055,803 |
| `Controllers/sg1_crew_carter.lad` | 19 | 20 | 380 | 19 | 0 | 311 | 1,289,037 |
| `Controllers/sg1_crew_oneill.lad` | 19 | 20 | 380 | 19 | 0 | 323 | 1,819,935 |

Three things follow, and the first is a correction.

* **There is no separate 10-byte prelude and no separate pose block.** The bytes this document used to read as
  `532, 0, 0x21500, 0` are `0x14` (a single leading byte), then `02 00 00 00` (the first frame header's `u32 2`) and
  `0x15`. `532` was `0x14` read with the next three bytes, and `0x21500` was the frame header read two bytes late.
  The "pose block, one record per channel" is the **first frame** -- frame 0 of 20, the bind pose.
* **A frame holds exactly the channel count**, in all eight files, 160 frames in total, with **no short frame and no
  empty frame**, and every record's first word is `2`. That is what `37` was: `32 + 5`, a frame header, not a longer
  record. The leading byte is `0x14` = 20 in every file, the same number as the frames, so it is most likely the frame
  count; all eight clips holding 20 frames means the identity is not proven by variety.
* **The stream does not close at the trailer in six of the eight files.** `sg1_crew_jackson.lad` ends 6 bytes past its
  twentieth frame; the two 1.3 MB and 1.8 MB `Controllers/` packs leave 96-99% of their bodies unread by this walk.
  Where the twentieth frame ends the body starts a **second section with its own channel table**: at
  `Controllers/sg1_crew_carter.lad` offset 12,613 the bytes are `04 02 00 00 00 13` and then `0b "BN-Blink_L\0"`,
  `0b "BN-Blink_R\0"`, ... -- the same 19 names again. So the body is at least *a table of 20 pose frames* followed by
  *a per-bone section*, which is what `LIPSincReadControllerFile` filling a pose array and a separate per-bone
  `ValuesAtTime` reader had already implied.

`-1.0f` recurs in 84% of one file's records, so it is a value (the pose weight), not a record terminator; the twelve
bytes between the third float and it take 125-145 distinct values per file, so they are not padding either. What the
seven floats *mean* is still U4.

## The body is three parts: pose frames, a clip section, and a track table (measured 2026-11-09)

The frame walk above closed the first part and left 96–99% of the `Controllers/` packs counted as `coverage_bytes`.
`tools/lad-tracks.py` (`make lipsinc-tracks`, `inventory/lipsinc_tracks.csv`, `--check` in `make ci`) walks on from
where it stops, and the rest of the body is **two more parts**, not padding:

* a **clip section**, present in the four 19-channel packs. A count, then that many clips, each `u32 2`, a bone count,
  that many `BN-` names, a frame count with the same 5-byte header and 32-byte records as the pose section, and finally
  a **clip name** (`carter_happy`, `tealc_yelling`, `oneill_squint`). 17 clips in total, every one exactly 20 frames,
  and the bone list is a *subset* of the file's channel table — 19, 11, 9, 5 or 3 bones, not all 19.
* a **track table**, present in seven of the eight files. One record per `<Map>.<Animation>` name
  (`AlkeshBomber.ca_firing_ge_s_02`), each with a constant header — `u32 2`, the short name, the qualified name,
  `u32 1`, `160.0`, `220.0`, and a count of **26** — followed by the record's key block, which the walk below
  reads as 26 groups.

| file | channels | clips (bones) | tracks | key bytes | of the file |
|---|---:|---|---:|---:|---:|
| `Controllers/sg1_crew_carter.lad` | 19 | 4 (19, 5, 19, 5) | 608 | 1,214,974 | 93.3% |
| `Controllers/sg1_crew_jackson.lad` | 19 | 3 (19, 5, 19) | 485 | 993,252 | 93.0% |
| `Controllers/sg1_crew_oneill.lad` | 19 | 6 (19, 5, 19, 11, 5, 3) | 786 | 1,725,593 | 94.2% |
| `Controllers/sg1_crew_tealc.lad` | 19 | 4 (9, 19, 19, 5) | 344 | 579,194 | 89.0% |
| `Controllers/grey.lad` | 21 | 0 | 3 | 26,511 | 65.2% |
| `JAnderson.lad` | 21 | 0 | 3 | 20,029 | 58.7% |
| `sg1_crew_carter.lad` | 21 | 0 | 2 | 13,327 | 48.7% |
| `sg1_crew_jackson.lad` | 21 | 0 | 0 | 0 | 0% |

Three things this settles, and three it does not.

* **The unread mass is named.** 4,572,880 bytes (4.36 MiB) of the 6.5 MiB corpus sit inside the key block of a track
  named after an animation — the four packs are 89–94% key bytes, not 96–99% mysterious. The 21-channel clips are
  small because they carry two or three tracks each, which is what a speech clip is: one line of dialogue, not a
  character's whole animation set.
* **The track header is invariant, which is what makes the table findable.** All 2,231 records agree on `u32 2`, the
  `u32 1`, both sentinel times `160.0`/`220.0`, and the key count **26** — with a single map owner (`AlkeshBomber`) in
  every file. The count is 26 in a 269-byte block and 26 in a 19,568-byte one: a fixed *number* of records over a
  variable-length payload. They are **groups**, each with its own key list, so 26 is the group count and not a key
  count — which is what the walk below measures.
* **A clip's bones are a subset, so a clip is not a copy of the pose frames.** `carter_worried` and `carter_frown` are
  five bones (the brows, the lids and the jaw); `carter_happy` and `carter_yelling` are all nineteen.

What is still open:

* **Which curve a group is.** The key block decodes (below): 26 groups of `(time, value)` keys per track. What a
  group indexes and what a value means is the rest of U4.
* ~~The bytes before the first track header~~ — **closed**: they are the table's own count (below). The six bytes in
  the same place in `sg1_crew_jackson.lad`, which has no table at all, are that file's own residue (`000002000000`).
* **Names are not unique keys.** `JAnderson.lad` lists `AlkeshBomber.Beware` twice, so a name indexes more than one
  record and a reader cannot key on it alone.

The reader this matches is `LIPSincReadAnimFile` (`0x000b8340`, `Read Track %s`): a table keyed by
`<Map>.<Animation>` with a per-track key list is exactly what its log line and its `ValuesAtTime, m_nEndTime` assert
describe, and `26` is then the number of **groups** such a lookup walks — each group a curve with its own keys, not
one key (below).

## The key block is 26 groups of `(time, value)` keys (measured 2026-11-09)

The track table above stopped at each header because 26 keys over a 269-byte block and 26 over a 19,568-byte one rule
out a fixed key struct. They are not 26 keys: a block is **26 groups**, and each group carries its own key count — so
the `26` the header stores is a group count, and it holds in all 2,231 blocks. `tools/lad-keys.py` (`make lipsinc-keys`,
`inventory/lipsinc_keys.csv`, `--check` in `make ci`) walks a block strictly: a group or key that does not parse
refuses the whole block rather than consuming a guess.

| bytes | field |
|---|---|
| 4, 1 | group header: `u32 2`, then the group's **name** as an `FString` — a count byte of `0` in all 58,006 groups, so no group in the corpus is named |
| 1 or 2 | key count, a 7-bit varint: bit `0x40` in the first byte means a second byte follows and contributes `<< 6`, so the 69-key group 2 of `AlkeshBomber.Beware` stores its count as `45 01`, and a group of 261 keys needs the second byte |
| count × 16 | one key: `u32 2`, `i32` time, `f64` value |
| 1 | block terminator, `0x00` |

All 2,231 blocks parse, none refused, and the blocks consume exactly the key bytes the track table counted:

| file | tracks | key bytes | groups | keys | empty groups | largest group |
|---|---:|---:|---:|---:|---:|---:|
| `Controllers/sg1_crew_carter.lad` | 608 | 1,214,974 | 15,808 | 69,966 | 6,588 | 261 |
| `Controllers/sg1_crew_oneill.lad` | 786 | 1,725,593 | 20,436 | 100,133 | 7,664 | 183 |
| `Controllers/sg1_crew_jackson.lad` | 485 | 993,252 | 12,610 | 57,316 | 5,116 | 186 |
| `Controllers/sg1_crew_tealc.lad` | 344 | 579,194 | 8,944 | 32,823 | 3,597 | 136 |
| `Controllers/grey.lad` | 3 | 26,511 | 78 | 1,627 | 5 | 137 |
| `JAnderson.lad` | 3 | 20,029 | 78 | 1,222 | 3 | 70 |
| `sg1_crew_carter.lad` | 2 | 13,327 | 52 | 813 | 2 | 69 |

**2,231 blocks, 58,006 groups, 263,900 keys, 4,572,880 key bytes** — the same 4.36 MiB the track table counted, now
attributed to a structure. A group is not anonymous in the schema: `FUN_000bc330` reads each one as a **28-byte
record** — a `u32` tag, a 12-byte `FString` **name** and a 12-byte `TArray` of keys (`FUN_00058850` and
`FUN_000bb650`) — so the `u8 0` at offset 4 is that name's count byte and the varint after it is the array's
count, in the same encoding every string in the file uses. **All 58,006 names are empty** (`named` in
`inventory/lipsinc_keys.csv`, `--check` refuses a row that says otherwise): the one field that could name a curve
is unused in this corpus. Keys per track run 7 to 1,213 (mean 118.3) and 39.6% of groups are empty, which is what a
bone subset looks like: a group with no keys is a channel that does not move in this clip.

Three measured facts say the middle field is a time and not an index:

* **it never runs backwards** — inside all 58,006 groups the `i32` is non-decreasing, and only 2 groups repeat a time;
* **its range suits a clip** — −304 to 23,876, read as milliseconds a track of up to 24 s, which is the order a table of
  `<Map>.<Animation>` names implies;
* **the value is the pose-like number, stored wider than a pose** — −9.45942 to 9.45942, inside the `|x| < 12` the pose
  records occupy, but as an **`f64`** where a pose record's eight values are `f32`.

The groups are also paired, which is the first evidence about what they *are*: in `AlkeshBomber.Beware` group 19 carries
`0.4289, 4.887` and group 24 carries `-0.4289, -4.887`; groups 22 and 23 are identical at `-0.6`; groups 15 and 16
differ in exactly one value (0.4602 against 0.4735). Two curves that mirror each other is what a left/right bone pair
looks like, and a pair that differs in one value is what a shared bone with one differing component looks like.

What is still open is no longer the encoding: **which group is which curve**, **what a value means** (a weight, an
angle, a coordinate — the eight-float pose record is the only other numeric structure in the format), and the two
header constants `160.0`/`220.0`, invariant across all 2,231 records and unexplained. The runtime half is unchanged:
`LIPSincReadControllerFile` fills a pose array and a separate `ValuesAtTime` reader interpolates
(`ValuesAtTime, m_nEndTime = %i, time = %f`), which is what a table of per-group key lists feeds.

## The 26 slots are a slot table, and a value's sign says what it is (2026-11-09)

`tools/lad-groups.py` (`make lipsinc-groups`, `inventory/lipsinc_groups.csv`, `--check` in `make ci`) measures
every group against every other group **in the same track** — 208 file × slot rows over the 58,006 groups — and
two things fall out that the per-track census could not see.

**Slots 0–18 carry weights, slots 19–25 carry signed values.** Across all 208 rows no slot below 19 ever holds a
negative value, and the widest one holds at most `1.33783`; every slot from 19 up holds a negative value somewhere
and reaches past the weight range. So the table is **19 weight curves and 7 signed curves**, split exactly at the
nineteenth slot — which is also the channel count of the four `Controllers/` packs. That coincidence is recorded,
not explained.

| slot | corpus value range | reading |
|---|---|---:|
| 0–9 | 0 to 0.850–1.181 | weight |
| 10, 11 | 0 to 1.146, 1.338 | weight |
| 12–14 | 0 to 0.862, 0.863, 0.231 | weight |
| 15, 16 | 0 to 1.215, 1.216 | weight |
| 17, 18 | 0 to **1** exactly | weight, saturating at 1 |
| 19 | −2.97284 to 9.45942 | signed |
| 20 | −4.18694 to 4.66299 | signed |
| 21 | −6.22496 to 5.56291 | signed |
| 22, 23 | −6.12772 to 6.82496 | signed |
| 24, 25 | −9.45942 to 3.88037 | signed |

**Six of the 26 slots are recoverable from the others, so a track carries at most 20 independent curves.** The
test is exact — two slots are compared only when they carry the *same* time sequence, and then every `f64` must
match bitwise or be its exact negation; a near miss is reported as `other`, and no pair is `other` in any track:

| relation | slots | tracks where it holds |
|---|---|---:|
| identical | 24 = 25 | 2,230 (of 2,231) |
| identical | 17 = 18 | 1,845 |
| identical | 22 = 23 | 883 |
| identical | 15 = 16 | 2 |
| negated | 19 = −24 = −25 | 1,890 |
| negated | 21 = −22 = −23 | 193 |

The counts differ from 2,231 because a pair is only compared where both grids agree: slot 19 and slot 24 share a
grid in 1,890 of the 2,230 tracks where both are populated, and the other 340 are not evidence either way. Slots
19, 24 and 25 are the table's workhorses — populated in 2,230 of 2,231 tracks — while 20 and 21 appear in 598 and
596, and the pairs 15/16 in 1,637. One pair is recorded without an explanation: slots 20 and 21 share a grid in a
handful of tracks and are then neither equal nor negated, which is the only `other` relation in the corpus.

**The header constants are not data.** `--headers` re-scans every `u32 2` marker with a *relaxed* test — a
length-prefixed name pair, a `u32 1`, two `f32` and any count byte — and finds **2,231 track-like records carrying
exactly one tuple, `(160.0, 220.0, 26)`, and zero records the strict reader refuses**. A duration, a sample rate or
a value range would vary with the clip; these do not vary in the corpus at all, so they are format constants and
the question is what the *format* means by them, not what a track's `160.0` is.

## The track table is preceded by its own count (measured 2026-11-09)

The `gap_bytes` the walk counted before the first `<Map>.<Animation>` header are **the table's record count**, in the
same 7-bit varint the key counts use: one byte under 64, two when the first byte carries the `0x40` continuation bit
and contributes `<< 6`.

| file | bytes before the first header | count | records the walk found |
|---|---|---:|---:|
| `Controllers/sg1_crew_carter.lad` | `60 09` | `0x20 + (9 << 6)` = 608 | 608 |
| `Controllers/sg1_crew_oneill.lad` | `52 0c` | `0x12 + (12 << 6)` = 786 | 786 |
| `Controllers/sg1_crew_jackson.lad` | `65 07` | `0x25 + (7 << 6)` = 485 | 485 |
| `Controllers/sg1_crew_tealc.lad` | `58 05` | `0x18 + (5 << 6)` = 344 | 344 |
| `Controllers/grey.lad` | `00 03` | 3 | 3 |
| `JAnderson.lad` | `00 03` | 3 | 3 |
| `sg1_crew_carter.lad` | `00 02` | 2 | 2 |

Seven of eight files, every count exact. The leading `0x00` in four of them is the previous section's last byte, not
part of the count — the count **ends** where the first record begins, so `tools/lad-tracks.py` decodes it backwards
from a record the pattern walk already found. That order matters: the count **verifies** the walk instead of steering
it, so a disagreement would be a finding rather than something to silently obey, and `--check` refuses a CSV whose
`declared` column disagrees with its `tracks` column. `sg1_crew_jackson.lad` has no table and its six bytes
(`000002000000`) are not a count; they stay recorded as that file's residue.

This closes one of U4's two "recorded rather than explained" items, and it is the third place the same varint codec
appears — the group key counts, the track count, and the two-byte counts a group of 69 keys already needed.
## The channels are an ordered bone list, and a pose record's last word is an angle (measured 2026-11-09)

The slot table above left "which weight slot is which bone", and the format has only one place that names a bone in
order: the channel table. `tools/lad-bones.py` (`make lipsinc-bones`, `inventory/lipsinc_bones.csv`, `--check` in
`make ci`) reads that table as a list, then reads each channel's twenty frame records, and two of the three answers
were already in the bytes.

**The list is stable, and the two shapes differ by exactly two bones.** All eight files share the same **eighteen-name
prefix**, and the difference between the shapes is at the end:

| shape | indices 18–20 |
|---|---|
| 19 channels (the four `Controllers/` packs) | `BN-Head` |
| 21 channels (the four speech clips) | `BN-Tongue-back`, `BN-Tongue-front`, `BN-Head` |

So the controller packs are the speech list **minus the two tongues**, in the same relative order — the same
skeleton, differently covered. `--check` holds both tails: a 21-channel file must end with the two tongues and
`BN-Head`, a 19-channel one with `BN-Head`, and any file whose first eighteen names differ from the others fails.
160 bone rows over 8 files.

**A clip's bone list is an explicit order, not the table's.** Each of the 17 clips names its own bones and its frames
carry one record per *listed* bone, in list order. Sixteen of the seventeen lists happen to follow table order; the
seventeenth does not: `Controllers/sg1_crew_jackson.lad`'s `jackson_frown` lists `BN-Head` **first**, then the four
brows (`18, 4, 5, 6, 7`). That is the useful negative, because it says the file never states "slot *i* is bone *i*":
a clip states its order by position in its own list, and a track's key block states nothing of the kind at all.

**The pose record's eighth word is a rotation angle, not a sentinel.** *Superseded in part by the reading at the top
of this page (`tools/lad-bones.py`, 2026-11-09): the four floats from 16 are a **unit quaternion**, so the last word is
its `w` and the angle is `2*atan2(|v|, -w)`, twice the figure below. The table's "0.5° grid" and its 38.5°
maximum are the halved-angle artefacts of `-acos` and are kept as the record of that measurement — as is the
"151 of 160 bones" peak-angle count beside it; on the corrected
reading the corpus lands on a **whole-degree** grid in 97.6% of records (3,124 of 3,200) and tops out at **77.0°**.*
Every one of the 3,200 records in the corpus
has its last float in `[−1, 0]`, and `-acos` of it lands on a **half-degree grid** in 95.0–99.0% of records per file:

| file | records | last word outside `[-1,0]` | on the 0.5° grid | at exactly 0° | commonest non-zero |
|---|---:|---:|---:|---:|---|
| `Controllers/grey.lad` | 420 | 0 | 98.6% | 352 | 10° (26) |
| `Controllers/sg1_crew_carter.lad` | 380 | 0 | 95.0% | 311 | 10° (17) |
| `Controllers/sg1_crew_jackson.lad` | 380 | 0 | 98.4% | 319 | 10° (16) |
| `Controllers/sg1_crew_oneill.lad` | 380 | 0 | 98.4% | 323 | 10° (16) |
| `Controllers/sg1_crew_tealc.lad` | 380 | 0 | 98.4% | 323 | 10° (16) |
| `JAnderson.lad` | 420 | 0 | 99.0% | 352 | 10° (26) |
| `sg1_crew_carter.lad` | 420 | 0 | 96.7% | 346 | 10° (26) |
| `sg1_crew_jackson.lad` | 420 | 0 | 98.6% | 352 | 10° (26) |

2,678 of 3,200 records are exactly 0° — the `-1.0` the earlier page called a sentinel, and 98.6% of one file's
records, is the **zero-angle case of the same value**. The largest angle in the corpus is 38.5°, and per bone the
peak angle is on the grid for 151 of 160 bones. The earlier reading of the twelve bytes before it as "a pose weight"
is corrected here: the record is `u32 2`, a position (three `f32` that move across the twenty frames — 17 or 18 of
19 bones move, by up to 3.4 units), three words that are not float data, and `-cos(theta)`.

**The 19 weight slots are not one per channel.** The 21-channel speech clips carry the *same* 26-group key blocks as
the 19-channel packs (`inventory/lipsinc_keys.csv`), so the weight-slot count is a property of the key schema and not
of the channel table; the "19 weights, 19 channel names" coincidence the slot section recorded does not survive this
measurement, and the group-to-bone question is not answered by counting.

What remains is smaller and named: **which curve a group is**. The four floats from 16 are read (above), so the
"three words between the position and the angle" are no longer open — they are the quaternion's vector part, and
the bytes this page first listed (`0x0f000004`, `0x9e12217c`, denormals) are that part seen as integers. The reader
half is now answered in [lipsinc-runtime.md](lipsinc-runtime.md): the loader links
the channel table to the mesh's bone names (`_wcsicmp`) and writes one mesh bone index per channel, so a channel is a
bone and a key-block slot is not; the two functions this page used to name (`LIPSincReadAnimFile` `0x000b8340`,
`LIPSincReadControllerFile` `0x000b7260`) parse the middleware's **text** LTF/LBP files and never see a `.lad` byte.
Which slot is which bone is not in the file at all: a key group's name field is empty in all 58,006 groups
(`inventory/lipsinc_keys.csv`).

## The schema is the loader's, and the walk ends at the last byte (measured 2026-11-09)

`tools/lad-parse.py` (`make lipsinc-parse`, `inventory/lipsinc_parse.csv`, `--check` in `make ci`) reads all eight
files in the order `FUN_000bce40`'s chain serialises them and consumes every byte of every one: 8 files, 160
channels, 3,200 pose records, 17 clips, 2,231 tracks, 58,006 key groups, 263,900 keys, 35-byte tail each. The totals
are the ones the shape-matching tools counted, so the two walks corroborate each other, but this one fails a file
rather than skipping bytes. The grammar is at the top of this page; what a pose record's three opaque dwords and its
`-1.0f` mean is still U4's open half.
