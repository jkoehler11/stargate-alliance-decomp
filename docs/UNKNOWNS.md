# Unknowns

Things this project does not know yet, with the evidence that raised the question and what would
settle it. A guess does not belong here either — only a gap that is being tracked.

## U1. 523 of the 797 `native()` declarations serialise no index

**Observed.** `inventory/natives.csv` records 797 native functions across 18 packages. 274 carry an
explicit index, all 274 distinct (`inventory/native_names.csv`), and 273 of those indices have one
declarer inside the runtime packages. The other 523 carry `native_token` 0 — for example
`Engine.Actor.KWake`, `Engine.Canvas.Clear`, `GUI.GUIComponent.ActualWidth` and every `IpDrv`
native.

**Why 0 is the package's value, not a parser default.** UELib reads `NativeToken` unconditionally
from the serialised `UFunction` (`src/Core/Classes/UFunction.cs`, `NativeToken = _Buffer.ReadUShort()`
in `Deserialize`), and it reads the right value for the 274 indexed declarations in the same
packages, so the offset is correct for build 130/4. The zeros are what the build shipped.

**Why it matters.** A declaration with no index claims no dispatch slot, so once the runtime table is
read (U7) those 523 still cannot be named from it — they are reached some other way, or not through
it at all.

**Hypotheses, none confirmed.**

1. They are declared `native` without a number and the compiler resolved their call sites to a
   per-function C++ pointer (`EX_FinalFunction` with the `UFunction` reference) instead of a
   `GNatives` index.
2. They are the script-to-C++ (`ucpp`) classes: Perception compiled hot script classes to C++, and
   the declaration survives as an interface with no VM entry (the project already expects this for
   `Pawn` and `ShadowProjector`, PLAN risk 4).
3. This build writes 0 into the index field for declarations that did not say `native(N)`, and the
   real binding is by name at engine start.

**What would settle it.** Disassemble a script function that calls one of them (`Actor.KWake` is a
good target: `Engine.Actor` has dozens of callers) and read the emitted opcode and operand — an
index opcode settles hypothesis 3, a function reference points at 1 or 2. Cross-check with 4.7
(the `.ucpp` bodies) if a C++ implementation exists for the same class.

**Cross-checked 2026-10-26 against the other half.** `tools/native-reconcile.py` (`inventory/native_reconcile.csv`,
`docs/NATIVE_CLASSES.md`) compares these records with the `native()` declarations in the shipped script: 256
declarations carry an explicit index and every one of them agrees with its record's token — 0 disagreements. So the
count in the header of this entry needs one refinement: **274 records carry a token, 256 declarations spell one out.**
The 18-record difference is not a parser artefact and is measured below (U3).

**Partially answered 2026-10-23.** Hypothesis 3 is what the build does. The routine that fills
`UFunction::Func` (`0x0003a030`, `Core\Src\UnClass.cpp`) has an explicit branch for
`FUNC_Native` with `iNative == 0`: it formats a name with `"int%sexec%s"`, looks it up, and stores the
first dword of the result as `Func`. So an index-less `native()` declaration claims no dispatch slot
because it is bound by *name*, not by index — which is also why no amount of reading `GNatives` will
name them (`docs/NATIVE_VM.md`, *The body of a script function*).

**Answered 2026-11-08, no boot: the two `%s` are the class's C++ name and the function's own name, and the names are
in the image.** `tools/native-binder.py` (`make native-binder`, `inventory/native_binder_names.csv`) extracts every
`int<prefix><Class>exec<Function>` wide literal from `default.xbe`: **883 distinct names in one 440.8 KB span**, i.e. a
pool shipped with the build rather than a symbol lookup that needs a map. The prefix is `A` exactly when the class
descends from `Actor` (**769 of 769** joined rows agree with `inventory/classes.csv`), and the literal `exec` is part of
the format rather than a mark of an `exec` function -- `Actor.KWake` is index-less, not `exec`. **769 of 797**
declarations have a name, including **508 of the 523** without an index; all 15 index-less misses are
`Editor.BrushBuilder` (13), `Editor.MaterialFactory` (1) and `UPreview.UPreviewSkinnedButton` (1), whose C++ side is not
in the Xbox image. The twelve misses that carry an index are `Core.Object` string operators spelled `_StrStr` in script
and `_StringString` in the pool, so they need no name. The 114 pool names no declaration matches are all `UObject`:
`Iterator`, `HighNative0`..`HighNative15`, and those twins. Depth: `docs/NATIVE_VM.md`, *What the two `%s` hold*.

**Status.** Answered. The index question is settled (the 523 claim no slot), the binding route is the binder at
`0x0003a030`, and the shipped prototype resolves every game-side declaration because the names it looks up are in the
image. **And the map those names are looked up in is in the image too (2026-11-09).** A `{name, body}` record per
registration — 1,104 records in 203 contiguous runs of `.data`, 883 distinct names — gives every declaration an
address with no boot, and the 261 records whose declaration also carries an index agree with the runtime
`GNatives` dump 261 of 261. `tools/native-table.py`, `inventory/native_targets.csv`, `docs/NATIVE_VM.md`
*The registration table*. 274 of the table's 883 distinct body addresses are outside every function in the call-graph census (275
records: one address carries two registrations) and all 274 are verified function starts — one Ghidra import
accepted 274, rejected 0, `inventory/native_census.csv` — so the records also seed the function map and correct its
denominator to 15,231 (`docs/PATH.md`). **And the records that *do* carry a token are now explained one by one**
(`inventory/native_tokens.csv`, *Every token has a basis* in `docs/NATIVE_CLASSES.md`): 166 spell their index in the
class's own text, 107 are operator signatures the compiler expands, 0 come from a declaration that spelled no index,
1 is an artefact of the editor package `UPreview` — so a token never means "the compiler filled this in".

## U2. Three `Engine.Actor` natives declare indices beyond the table — closed

**Closed 2026-10-23.** They are not beyond the table. `GNatives` has `EX_Max` = 4,096 entries
(`docs/NATIVE_VM.md`); 3969–3971 are ordinary indices inside it. The bound of 994 that made them look
impossible came from the pointer array that was wrongly identified as the table.

**Observed.** `Actor.MoveSmooth`, `Actor.SetPhysics` and `Actor.AutonomousPhysics` declare indices
3969, 3970 and 3971. Under the bound of 994 entries reported for the table at `0x00598e00`, they were
past its end, and U2 asked whether they belonged to a different numbering space.

**Resolution.** The table they were measured against was not the dispatch table (it is the CRT
initialiser list). The real bound is `EX_Max` = 4,096, read from the interpreter's own assert, so all
three indices are ordinary slots. Nothing about them is anomalous after all.

**Status.** Closed 2026-10-23. What remains unknown about them is U7, not their index.

## U3. `UPreviewMapListClient.Created` claims index 2

**Observed.** The only declarer of index 2 is `UPreview.UPreviewMapListClient.Created`. The claim was
written when slot 2 of the array at `0x00598e00` was null; that array is the CRT initialiser list, not
`GNatives`, so the null-slot half of the observation no longer holds. `UPreview` is an editor-side
package that the shipped game does not load, and its small indices remain suspicious on their own.

**Hypotheses.** Editor packages number their natives inside the package rather than in the engine's
global index space, so their small indices are unrelated to the runtime table.

**What would settle it.** Reading the runtime table (U7) and checking whether slot 2 is used at all,
or the same test on a second editor package with indexed natives (`UnrealEd` and `Editor` have none
with an index here, which is consistent with the hypothesis but does not prove it).

**Status.** **Answered 2026-11-08** — see *Answered* below; the index is not a compiler assignment, and the entry's
guess was wrong. `Editor`, `UPreview` and `UnrealEd` are excluded from naming (`tools/native-names.py`), so nothing
depended on the answer.


**Measured 2026-10-26.** The class is one of exactly **two** where the record carries a token and the shipped text
does not: `Core.Object` has 160 token-carrying records against 143 indexed declarations (17 more), and
`UPreview.UPreviewMapListClient` has 1 against 0. So `Created`'s index 2 is not a stray value — it is the compiler
assigning an index to a declaration that does not spell one out, and the same happens 17 times in `Core.Object`,
mostly for the per-signature operator records (`Concat_StrStr`, `EqualEqual_ObjectObject`, …) the compiler
synthesises. The practical consequence for the native work: **the record's token is authoritative and the text's
index is not exhaustive**, so a rebuild must read indices from the packages, never from the source.

Still open: which 17 of `Object`'s records are compiler-synthesised and which are hand-declared without an index —
the class's own text does not distinguish them.

**Corrected and answered 2026-11-08, no boot: neither half of the 2026-10-26 sentence holds.**
`tools/native-reconcile.py --tokens` (`inventory/native_tokens.csv`, one row per token-carrying record) classifies
all **274** records against the class's own text: **166** spell their index on a declaration of that name, **107**
are `operator` signatures no declaration names, **0** belong to a declaration that named a function and spelled no
index, **0** disagree, and **1** — this class's `Created` — is unresolved. So the compiler never assigns an index to
an index-less `native` on this build.

* **`Core.Object`'s 17 are all operator records.** 160 token records = **57** function records that spell their
  index + **103** operator records; the text spells 143 indices = **57** functions + **86** `operator` statements, so
  the surplus is 103 − 86 = 17 — `Add_IntInt` (146), `Concat_StrStr` (112), `EqualEqual_ObjectObject` (114) and the
  like. None is a hand-declared function without an index.
* **`Created` is not a native declaration at all.** The class's shipped text carries `function Created()` **with a
  body** (`Super.Created()`), and the package holds two `Function` exports of that name (indices 59 and 78) whose
  outers resolve to a `FloatProperty` (`XL`, export 7) and a `TextBuffer` (`ScriptText`, export 81) — neither can be
  a function's outer — while every function export in the file shares the flag word `0x70004` and both payloads end
  in the same eight bytes `47 00 00 00 02 00 02 00`. The token 2 is therefore the second dword of a tail that belongs
  to a script function, i.e. an artefact of `UPreview`'s export table rather than an index the compiler assigned.
  Depth: `docs/NATIVE_CLASSES.md`, *Every token has a basis*.

What survives for a rebuild is narrower and still actionable: read indices from the package records, because the
runtime packages' **text** is incomplete (2 records with no declaration, 9 declarations with no record — the
reconciliation table above), not because the text omits indices it should have spelled.

## U4. The `.lad` payload layout is not established

**Observed.** All 8 files share a header (`u32 2`, `u32 2`, `u8` channel count, then that many
length-prefixed bone names) which ends at `0x181` for 21 channels and `0x160` for 19
(`docs/formats/lipsinc-lad.md`). The payload after the table is float data: it opens with `532` and a
second word whose second byte equals the channel count (`0x21500` for 21, `0x21300` for 19), and the
last 128 bytes of every file end in `-1.0f` sentinels and `undefined` name strings. File size tracks
clip length, not channel count, so it is per-frame curve data.

**Why it is not parsed yet.** Nothing in the script reads it: the five `Engine.Actor`
`*LIPSincAnim` natives carry no serialised index, so the loader is engine-side. Guessing a record
layout from one clip would produce exactly the plausible-looking wrong output this project bans.

**What a census rules out (2026-11-04).** `tools/lad-scan.py` counts the `-1.0f` hits against the records that share a
file's commonest 12-byte tail: 352 hits vs 222 records in a 21-channel file (the rest are the four bytes of `-1.0f`
occurring inside float data), and the commonest tail differs between two 21-channel files whose stride histograms are
identical -- so neither `-1.0f` alone nor a fixed tail identifies a record. **What the loader says (2026-11-04).** `LIPSincReadControllerFile`
(`0x000b7260`) indexes its pose array with a **32-byte stride** and fills a record by copying **eight floats** into it
after writing the integer 2 -- so the payload's `-1.0f` is the *eighth float of a record*, not a sentinel after one, which
is what the census's twelve "tail" bytes really are (floats four to six). `LIPSincReadAnimFile` (`0x000b8340`) is the
track reader and parses text through `__wtof`, so it is not the `.lad` path. **What would settle the field meanings:** a
value-by-value check against a known clip, or the offline writer, since the runtime function is too inlined to read its
field order.

**What would settle it.** The middleware's own format notes (LIPSinc authoring tool), or the native
loader from PLAN 4.8. Two cheap experiments in the meantime: compare a clip whose speech waveform is
short with the `Controllers/` pack for the same character (the difference is the per-frame data), and
test whether the `0x21300`/`0x21500` word is a count of visemes or frames (both files with the same
channel count share the value exactly, so it is not per-file).

**Narrowed 2026-10-27: the container and the stride are measured, the fields are not.** `tools/lipsinc-index.py`
(`make lipsinc-index`, `inventory/lipsinc_lad.csv`, `inventory/lipsinc_phonemes.csv`) establishes:

* after the channel table a **10-byte prelude** (`532`, `0`, `0x21500`/`0x21300`, `0`), then a **32-byte-strided
  record stream** — the `-1.0f` at a record's bytes 24–27 recurs at exactly 32-byte steps in `grey.lad`,
  `sg1_crew_jackson.lad` and `Controllers/sg1_crew_tealc.lad` (352 of 424 records in the smallest clip, no
  exceptions in the run) — ending in a block of three names (`BN-Head`, `undefined`, `undefined`);
* a record is 12 bytes of three small signed float32, 12 bytes of three near-zero float32, `-1.0f`, and a `2`;
* **seven of the eight files do not end on a stride boundary** (0–28 bytes over), so the last record or the
  trailer's prefix has a different shape — that residue is the open question, not a rounding error;
* the `.map` siblings are **plain text**: `[PhonemeToSpeechTargetMapping]`, `NumSpeechTargets=15`, 43 phonemes,
  and **all four maps carry identical weights**, so character differentiation is in the `.lad` packs.

What is still open: what the three floats mean (position? rotation?), whether the near-zero triple is padding, and
the trailer's exact length. The route is unchanged — the LIPSinc authoring tool's notes, or the native loader from
4.8 — but the file is no longer opaque, and `--check` in `make ci` keeps the measurement honest.

**Correction, and one more measured step (same day, later).** An earlier revision of this entry and of
`docs/formats/lipsinc-lad.md` described the whole `.lad` body as 32-byte-strided records with a small remainder. That
is wrong, and the comparison with the channel table is what shows it: the stride holds for **exactly one record per
channel** — a 21- or 19-entry bind pose whose first entry is the same `(3.0464, 8.6571, 10.3889)` in every file — and
nowhere after it. `inventory/lipsinc_pose.csv` records the block per file (it starts 10 bytes after the channel table,
i.e. at 395 or 362) and `--check` enforces that it never exceeds the channel count and that the scan reaches the end
of the file. The animation payload that follows is 96–99% of every file: **32-byte-strided runs, not a uniform
array** — 1 of 8 payloads is a whole number of 32-byte records, and the block each record carries recurs at gaps of 32,
37, 64, 128 and 192, so headers or longer records punctuate the runs (37 = five bytes more than the common case). Its
key times and values remain unread, and the questions are now "what does a record hold" and "what makes one 37 bytes". So U4 has narrowed twice in one day: the container is known to a bind pose plus an
unread animation payload, and what is left is a keyframe decoder, not a container.

**The runtime side is located too (same session).** The loader is native but not anonymous: the module logs through 43
`[LIPSinc]:` strings and the image carries `Engine\Src\LIPSincSupport.cpp` as an assert path.
`tools/image-strings.py --topic lipsinc` (`inventory/lipsinc_runtime.csv`) attributes 19 functions to those strings, and nine name
themselves — `LIPSincInitLevel` (`0xba750`), `LIPSincInitGame`, `LIPSincShutdownLevel`, `LIPSincShutdownGame`,
`LIPSincLoadDBFromDisk`, `LIPSincReadAnimFile` (`0x8340`, the *track* reader: `Read Track %s`, the LTF version
errors), `LIPSincReadControllerFile` (`0x7260`, `Parsed %s` and `Attempt to import an obsolete .LBP file!`),
`LIPSincLinkBones`, `LIPSincPlayAnim`. `LIPSincInitLevel` enumerates `L"*.lad"` and hands each file to the two
readers, and `ValuesAtTime, m_nEndTime = %i, time = %f` in `0xb24f0` says the container is per-bone key lists with an
end time — which is what the 32-byte stride and the `-1.0f` sentinel are. The names are evidence for a rename, not
applied: the symbol map only changes in a Ghidra run.

**Answered for the container, and the `37` was a header (2026-11-09).** `tools/lad-frames.py`
(`make lipsinc-frames`, `inventory/lipsinc_frames.csv`) walks the body instead of pattern-matching it, and the five
bytes past a record are a **frame header**: `u32 2` (the same word that opens a record) followed by a `u8` equal to the
channel count, then exactly `channel_count` 32-byte records. All eight files, **160 frames, 3,200 records, no short
frame, no empty frame**, and every record's first word is `2`. The earlier "10-byte prelude -- 532, 0, 0x21500, 0" was
this header read two bytes late (`0x14` is a single leading byte equal to 20, the frame count in all eight), and the
"bind pose block, one record per channel" is **frame 0 of 20**. What is left is a second section: where the twentieth
frame ends, six of the eight files continue into a body that repeats the channel table (`04 02 00 00 00 13` then
`0b "BN-Blink_L"` at `Controllers/sg1_crew_carter.lad` 12,613), leaving 6 bytes to 1.8 MB of the body unread by this
walk; the two 1.3 MB and 1.8 MB `Controllers/` packs are 96-99% second section. `-1.0f` recurs in 84% of one file's
records, so it is the pose weight, not a terminator, and the twelve bytes before it take 125-145 distinct values per
file, so they are not padding. **The open question is now the second section's records, not the 37.**

**The second section is a clip section and a track table (2026-11-09).** `tools/lad-tracks.py` (`make lipsinc-tracks`,
`inventory/lipsinc_tracks.csv`, `--check` in `make ci`) walks on from the twentieth frame and the body's remaining 96-99%
splits into two parts rather than one. In the four 19-channel packs, a **clip section**: a count, then that many clips,
each `u32 2`, a bone count, that many `BN-` names, a frame section, and a **clip name** (`carter_happy`,
`tealc_yelling`, `oneill_squint`) — 17 clips, every one 20 frames, and each clip's bone list a subset of the channel
table (19, 11, 9, 5 or 3 bones). In seven of the eight files, a **track table**: 2,231 records named
`<Map>.<Animation>` (`AlkeshBomber.ca_firing_ge_s_02`), each with a constant ten-field header — `u32 2`, short name,
qualified name, `u32 1`, `160.0`, `220.0`, key count **26** — followed by a key block. Those key blocks are
**4,572,880 bytes, 4.36 MiB of the 6.5 MiB corpus**, which is where the mass that this entry called "96-99% unread"
actually lives. **What is left of U4 is the key block alone**: its `u32 2` marker recurs 33-1,247 times per block at
11-15 bytes per marker, which with a constant key count of 26 means variable-length key entries, not a struct array.
One smaller unknown is recorded rather than explained: `JAnderson.lad` lists `AlkeshBomber.Beware` twice, so a name
is not a unique key. **What would settle the key
block:** the same route that has worked three times on this format — read `LIPSincReadAnimFile`'s loop at `0x000b8340`
for the entry stride (it is 4,977 bytes and already located), or count `ValuesAtTime` lookups against a clip whose
end time is known, since 26 keys over `160.0`-`220.0` milliseconds gives a 2.4 ms step to test against.

**The key block decodes (2026-11-09).** `tools/lad-keys.py` (`make lipsinc-keys`, `inventory/lipsinc_keys.csv`,
`--check` in `make ci`) walks all 2,231 blocks: a block is **26 groups**, each `u32 2`, a `0` byte and a 7-bit varint key
count, then that many 16-byte keys of `u32 2`, `i32` time, `f64` value, and a `0x00` terminator. All 2,231 parse with
none refused — 263,900 keys, 58,006 groups, 4,572,880 bytes, exactly the key bytes the track table counted — the time
field is non-decreasing in all 58,006 groups, the values stay inside the pose range (−9.45942 to 9.45942), and the groups
come in mirrored pairs (`0.4289, 4.887` against `-0.4289, -4.887`). The `26` in the track header is therefore the group
count, not a key count. Still open: which group is which curve, what a value means, and the header's invariant
`160.0`/`220.0`.

**A value's sign splits the slots at 19, and six of the 26 are redundant (2026-11-09).** `tools/lad-groups.py`
(`make lipsinc-groups`, `inventory/lipsinc_groups.csv`, `--check` in `make ci`) measures every group against every
other group in the same track, 208 file x slot rows over the 58,006 groups. **Slots 0-18 never carry a negative
value in any row** and their widest is `1.33783`; **slots 19-25 are signed**, -9.45942 to 9.45942. So the table is
19 weight curves and 7 signed curves, split exactly at the nineteenth slot -- the same 19 as the channel count of
the four `Controllers/` packs, which is recorded rather than explained. And the slots repeat: 24 = 25 in 2,230 of
2,231 tracks, 17 = 18 in 1,845, 22 = 23 in 883, 15 = 16 in 2, while 19 = -24 = -25 in 1,890 and 21 = -22 = -23 in
193. A pair is compared only when both slots carry the *same* time sequence and then every `f64` must be bitwise
equal or the exact negation, so those counts are evidence and the gaps are silence: 19 and 24 share a grid in 1,890
of the 2,230 tracks where both are populated, and the other 340 say nothing either way. **At most 20 of the 26
slots are independent curves**, which is what a decoder has to read. The header constants are now a negative:
`--headers` re-scans every `u32 2` marker under a relaxed test (name pair, `u32 1`, two `f32`, any count byte) and
finds 2,231 track-like records with exactly one tuple `(160.0, 220.0, 26)` and zero the strict reader refuses, so
they cannot be per-clip data (a duration or a rate would vary) and the open question is what the *format* means by
them. What is left of U4: which weight slot is which bone (the clip sections' bone subsets are the only ordered
bone list in the file), and the meaning of the 7 signed slots' values.

**The track table's extent is declared, not inferred (2026-11-09).** The bytes the walk counted before the first
header are the table's **record count**, in the same 7-bit varint the key counts use (`0x40` in the first byte means a
second follows and contributes `<< 6`): `60 09` is `0x20 + (9 << 6)` = **608** in the carter pack, `52 0c` = 786,
`65 07` = 485, `58 05` = 344, and `00 03` = 3 in `grey.lad` and `JAnderson.lad`, `00 02` = 2 in
`sg1_crew_carter.lad`. **Seven files, every count exactly the number of records the pattern walk found**, which
retires one of the two items this entry recorded as unexplained and turns the table's extent from a pattern into a
declared one. The count is decoded *backwards* from a record the walk already found and `--check` refuses a CSV whose
`declared` column disagrees with its `tracks` column, so it verifies the walk rather than steering it. The leading
`0x00` in four of the seven is the previous section's last byte, not part of the count.
`sg1_crew_jackson.lad`, which has no table, keeps its six bytes (`000002000000`) as that file's residue -- they are
not a count of 0. This is the third appearance of the same varint in the format (a group's key count, a group of 69
keys' two-byte count, and now the table's count), which is worth knowing before reading `LIPSincReadAnimFile`.

**The runtime half is measured, and the reader the entry kept naming is the wrong two functions (2026-11-09).**
`docs/formats/lipsinc-runtime.md` reads the engine side: `LIPSincInitLevel` (`0xba750`) finds `*.lad` itself and
matches the names against the level's prefix list; `LIPSincLoadDBFromDisk` (`0xb9e90`) reduces each file to its
**stem** (the controller name) and parses it through an object of the class at `PTR_005bf508`, whose parse is
`FUN_000bce40` (a five-stage chain, not yet mapped to sections). **The channel-to-bone link is a name match**:
`FUN_000b4930` compares each channel name with the mesh's bone names using `_wcsicmp` and writes the matching
**mesh bone index** into a dword array at controller `+0x10`, one per channel, and rejects a pack
(`NOT VALID CONTROLLER!`) unless every channel matched. So the channel table is a bone list because the loader
resolves it as one, and **the 26 key-block slots are not channels** — the link writes channel entries only.
The names are real, too: `tools/lad-bone-owners.py` (`inventory/lipsinc_bone_owners.csv`) finds all 21 declared
by five `Animations/*_anim.ukx` skeletons and by no other package in the corpus. And `LIPSincReadAnimFile`
(`0x8340`) / `LIPSincReadControllerFile` (`0x7260`) are **text** readers — `Core_UnTemplate(..., L" ")`,
`__wtof`, `Read Track %s`, `not a valid LTF file!`, `obsolete .LBP file!` — so no `.lad` byte goes through
either; what stays open is which parse stage reads which section, and what the 7 signed slots mean.

**The five stages are the file's sections, and the walk ends at the last byte (2026-11-09).** `tools/lad-parse.py`
(`make lipsinc-parse`, `inventory/lipsinc_parse.csv`, `--check` in `make ci`) replays `FUN_000bce40`'s order instead
of recognising sections by shape: two 4-byte scalars (the `2`, `2` header), the channel table, the pose block (20
frames of one 32-byte record per channel), the clip array (`{u32, bone names, 20-frame pose array, name}`), the track
array (`{u32, name, Map.Anim, u32, f32, f32, 26-group key block}`), then a `u32` and three strings as the 35-byte
tail. Every count in the file is `FUN_000637e0`'s 7-bit varint, which is why the channel count reads as a `u8` and the
track table's extent is two bytes. **All eight files parse to their last byte** — 8 files, 160 channels, 3,200 pose
records, 17 clips, 2,231 tracks, 58,006 groups, 263,900 keys — and the totals match the shape-matching tools, so the
container half of U4 is closed: `docs/formats/lipsinc-lad.md` carries the grammar and `docs/formats/lipsinc-runtime.md`
the stage-to-section table. Two readings are corrected by it: the payload does not open with `532`/`0x21500` (those
bytes are the first frame's `20`, `u32 2`, `21`, `u32 2`), and the pose record begins with a `u32 2` and ends with
`-1.0f`, so the `2` the old table put at bytes 28–31 is the next record's leading word.
**The record is a position and a unit quaternion (2026-11-09).** `tools/lad-bones.py` now reads all seven floats:
the last four have `|q| = 1` to within `5.5e-7` in **all 3,200** records and `w` is never positive, so
bytes 16-31 are a rotation in the negated convention and the three "opaque dwords" are its `x, y, z`
(`6.3e-30, -0, 3.6e-30` at rest -- denormal, not zero, which is why they did not read as float data). The angle
is `2*atan2(|v|, -w)`: a whole-degree grid in 3,124 of 3,200 records, 522 turning at all, `77.0` degrees at most
and 42 distinct values, 506 of the turns about `±X` and 16 about `(0.008, 0.239, 0.971)`. The earlier
"half-degree grid" was this grid read with `acos`, which reports half the rotation (`w = -cos(theta/2)`) and
loses small angles to float resolution. **`FUN_000b1bf0` cannot name the fields**: `FUN_00037110` and
`FUN_00037170` are byte-identical 4-byte reads through the archive's `Serialize`, so its eight reads say "one tag
and seven scalars" and the 3 + 4 grouping is measured from the data.
**A group is a named key array, and the name is empty everywhere (2026-11-09).** `FUN_000bc330` reads the key
block as 26 elements of a **28-byte** record -- a `u32` tag, a 12-byte `FString` and a 12-byte `TArray` of keys
(`FUN_00058850` and `FUN_000bb650`) -- so the `u8 0` the format notes called a group flag is the **name's count
byte**, and the varint after it is the key array's count, in the same encoding the strings use. `lad-keys.py`
now reads the name and counts it: **58,006 groups in 2,231 tracks, 0 named** (`inventory/lipsinc_keys.csv`, a
`named` column, `--check` refusing a named row), with the corpus totals unchanged and no block refused. That is
a format-level negative for this entry's oldest question: the one place a group could say which curve it is, is
present in the schema and empty in every shipped block, so the slot-to-bone link has to come from the runtime
or from the values themselves.
**What would settle the rest:** the clip record's `u32 2` and the track header's `160.0`/`220.0` are read by
`FUN_000bc620` and the four record readers into their first dwords and two float fields **without any test**, so
the serialisers answer only that the tag is carried and the pair is constant; naming them needs a caller that
consults those fields. The 7 signed key slots are a separate question, unchanged.

**The sampler fills one vector from one bound, and the file never shares a time axis (2026-11-09).**
`ValuesAtTime` (`0x000b24f0`) fills `*(this + 0x34)` doubles at one `double time`, bounded by a single int at
`+0x1c` (its own warning names it `m_nEndTime`) and a global floor, with no per-slot branch and no slot index —
so the seven signed slots are sampled exactly like the nineteen weights, and their meaning is downstream of the
sampler, not a file property. `tools/lad-groups.py --vector` adds the matching negative: **0 of 2,231 tracks**
share a start, an end, an extent or a key grid across their populated slots (3–23 distinct grids per track,
median 10; median spread between the earliest and latest slot start 696 ticks in an 8,688-tick corpus). What
would settle the remaining half: the evaluator the sampler calls (what it answers outside a curve's own extent —
hold, clamp or extrapolate) and the code that consumes the 26 doubles, where a signed slot would acquire one.

**Answered: the evaluator holds, and a segment is a smoothstep (2026-11-09).** `ValuesAtTime` calls
`Engine_LIPSincSupport` (`0x000b2230`, 435 bytes) once per slot, and its body settles the hold/clamp/extrapolate
question: before a slot's first key and after its last it returns that key's value with no arithmetic in between
(hold, not extrapolation), no keys returns `0.0`, one key returns that value, and between keys it computes
`v0 + (v1-v0)(3a^2-2a^3)` with `a = (t-t0)/(t1-t0)` -- `3.0` at `0x4ea138`, `6.0` at `0x4ea130` -- then snaps
`|v| < 2^-51` to zero on the common exit, which is the `0x4ea0c8` constant. `tools/lad-sampler.py`
(`inventory/lipsinc_sampler.csv`) implements it and counts the corpus: 57.4% of the 26 x domain rectangle over
2,231 tracks is a hold (median track 75.4%, p10 48.7, p90 82.8, max 94.4), 0 tracks have every populated slot
covering the domain, 0 adjacent key pairs decrease, 2 pairs share a tick (slot 2 of `m07_17_h_carter` at 4086 and
`m21_09_b_carter` at 4022 in `sg1_crew_carter.lad`), and no key value is a non-zero magnitude below `2^-51`. What
is left of U4 is not the format or the sampler: it is what a slot means, which lives in the two consumers of the
vector (`0x000b5f10`, `0x000b63f0`) and the accessor `0x000b4710` with its lookup `0x000b45c0`.

**Answered: a slot is read by name, and a zero means "no contribution" (2026-11-09).** The accessor
`0x000b4710` copies a caller's `FString`, resolves it with `0x000b45c0` (`__wcsicmp` over the key block's
28-byte groups, count `+0x40`, returning `index*0x1c + base` or 0) and evaluates the group it finds with
`Engine_LIPSincSupport`, answering `0x4c8848` when nothing matches -- so **a slot's address is the name in
its own group record**, which the corpus leaves empty in all 58,006 groups. The pose-pair blend
`0x000b5f10` evaluates a record only on its `alpha == 0` side (the first frame of a fade), stores the
answer at record `+0x40` and calls pose assembly `0x000b29a0` **only when that answer is non-zero**; the
fade driver `0x000b63f0` runs state 1 = fade in (duration `[request+0x28]`), state 2 = play (its own clock
at `+0x20`, `ValuesAtTime` as the bounds check), state 3 = fade out (duration `[request+0x2c]`, then every
record zeroed), and its play state repeats the same accessor-and-assembly loop. Both paths make the double
a blend weight whose zero is a skip; a negative value is applied like a positive one, so the 7 signed
columns are ordinary weights and not a second curve type. `tools/lad-weights.py` (`make lipsinc-weights`,
`inventory/lipsinc_weights.csv`, `--check` in `make ci`) counts it: at tick 0 **12,080 of 35,031** populated
slots (34.5%) are applied and **22,951 (65.5%)** skipped (median 36.4% per track, p10 9.1, p90 66.7), at a
track's own end only **26** (0.07%) are non-zero, **24 == 25 at tick 0 in all 2,230** tracks that carry
them, **19 == -24 by value in 2,137** and **exact key for key in the 1,890** that share a grid. What is
left of U4 is the binding -- which caller fills a record's name at `+0x34` -- and that is answered below
(the record is a clip and the name is read from the file, not stored).

**The name writer is outside the support region (2026-11-09, one Ghidra import).** All 58 functions of
the LIPSinc support region (`0x000b1770`..`0x000b6fc0`, `~/sg1/work/lip10`) are exported together, and
**no body in that range stores to a 0x48-byte record's `+0x34`/`+0x38`**: the only stores to those two
offsets in the whole set are stack locals plus the fade driver's own request field (`0x000b63f0` writes
`+0x38` of the request, not of a record). The arrays are therefore built outside the region -- by
whatever the object factory at `PTR_005bf508` constructs -- so the next instrument is the
`0x000b9d50`/`0x000bce40` loader, not another pass over these bodies.

**Answered: the record is a clip and its name arrives through the string stage (2026-11-09).** The loader
`0x000bce40` pushes all four destinations before its first call and each callee pops only its own two, so
the stages read `+0x18` (channel names), `+0x30` (pose block), `+0x48` (**clips**) and `+0x54` (**tracks**)
in order. The clip reader `0x000bcb60` names each `0x48`-byte element with `0x00058850(Ar, record + 0x34)`
-- an inline `FString` read, **not a store**, which is exactly why the support-region export saw no writer.
`0x000bb2a0` is the getter that copies that `FString` (`+0x34` data, `+0x38` count), and both consumers
(`0x000b5f10`, `0x000b63f0`) call it over the `+0x48` array, so **the runtime evaluates clips, not tracks**;
the track array is `+0x54`, and a track's `+0x34` is its second group array's base (`0x000bc620`), which is
what separates the two 0x48-byte families. The corpus has **17 clip records against 2,231 track records**,
clips in four files only. What is still open is not the binding but the *names*, and that is answered by
the call graph in the paragraph below.

**Answered: the play path resolves a track, and no name in the shipped data can match (2026-11-09).**
`LIPSincPlayAnim` (`0x00228730`) calls only the track getter `0x000b2ce0` (three sites) and bails when
`[track + 0x24] == 0`, so a play call selects a **track**; the clip array's getter `0x000b2cb0` has a single
caller, `0x00220ba0`, which walks the array once for the bone link `0x000b4930`, i.e. the pose side. The fade driver `0x000b63f0` has one
caller (`0x0022e090` at `0x0022fe9b`) and the blend one (`0x000b63f0` at `0x000b64f0`, key block in `ECX`),
and the blend compares **each clip's `+0x34` name** against every group name of that track's block
(`0x000b4710` -> `0x000b45c0`, `__wcsicmp` over `0x000bb2f0`). All 58,006 groups are unnamed, so no clip
name can match and the per-clip answer is always the `0x4c8848` zero; the `.map` files cannot supply the
names either -- they name 15 near-one-hot *speech targets*, not 26 groups. The runtime half of U4 is
therefore closed on a negative: the shipped data cannot exercise the named-slot path, and which design
fills a group's name is not measured.

What the region does settle is the object model around the records:

* **the controller holds two of those arrays, not one** -- and they are now named: `+0x48` is the **clip**
  array and `+0x54` the **track** one. `0x000b2cb0` and `0x000b2ce0` are a matched
  pair of bounds-checked getters -- `if (count < 1 || count <= i || i < 0) return 0; return base + i *
  0x48` -- over `+0x48`/`+0x4c` and `+0x54`/`+0x58`, which is the pose pair `0x000b5f10` copies between:
  the accessor writes one array's `+0x40` and the blend reads the other's `+0x04`/`+0x10`;
* **the key block has a write-side size function**: `0x000b4200` and `0x000b43e0` are 466-byte twins
  that walk a group array (`+0x30`/`+0x34`) and sum `keys * 0x10 + (namelen - 1) * 2` per 28-byte group,
  i.e. the 16-byte key record and the wide name -- the save path for exactly the record `0x000bc330`
  reads, and the counterpart the earlier notes were missing;
* `0x000b55f0` is the `.map` writer (`; %s.map`, `LIPSinc Phoneme Mapping File for %s`), a text file
  outside this container and not a second home for the track data;
* in the exported set `0x000b4710` has exactly two callers, `0x000b5f10` and `0x000b63f0`; there is no
  third consumer of a single slot.

## U5. Which script path writes and reads a `.sgs` — the script half is measured

**Observed.** The checkpoint container is settled (a UE2 package whose exports are the level's actors). The engine side
is `Engine.GameInfo.SavePackage` / `LoadSaveGame`, both natives without a serialised index; the game side ships
`StargateGame.AutosaveCheckpoint` (a `Trigger` subclass), `StargateGame.ACTION_SaveGame` (a `ScriptedAction`),
`Core.Object.MarkForSaveGame`, and the menu classes `StargateMenus.SaveGameMenu` / `SavedGameInfo` / `SavedGameList` /
`OverwriteSavedGamePage` / `DeleteSavedGamePage`.

**This did not need PLAN 3.10.** The packages carry their own `ScriptText`, so the call sites are readable today:
`tools/pkg-script-text.py` extracts them and `tools/savegame-paths.py` (`make savegame-paths`,
`inventory/savegame_paths.csv`) finds **16 rows** — 5 declarations, 9 calls, 2 console commands:

| role | class.routine | line | what it says |
|---|---|---|---|
| declare | `GameInfo` | 268 | `native final function bool SavePackage(string packageName)` |
| declare | `GameInfo` | 273 | `native simulated final static function bool LoadSaveGame(string GameFile)` |
| declare | `Object` | 556 | `native final function MarkForSaveGame()` |
| declare | `GameInfo` / `StoryModeGame` | 277 / 47 | `event LoadSaveGameInfo(GameInfo SavedGameInfo)` — base and override |
| **call** | `GameProfile.StartNewMatch` | 160 | `CurrentLevel.Game.SavePackage(PackageName)` |
| **call** | `GameProfile.ContinueSinglePlayerGame` | 191 | `Level.Game.SavePackage(PackageName)` |
| **call** | `GameInfo.PostLogin` | 1860 | `LoadSaveGame(SaveGameFileName)` |
| **console** | `PlayerController.RestartCheckpoint` | 1002 | `ConsoleCommand("LoadSaveGame" @ "c_" $ Left $ "_" $ LastCheckpointFilename $ ".sgs")` |
| **console** | `PlayerController.LoadGame` | 3593 | `ConsoleCommand("LoadSaveGame" @ Filename)` |
| call | `Pawn.PossessedBy`, `KActor.PreSaveGame`, `PlayerReplicationInfo.PreSaveGame`, `UnrealTeamInfo.PreSaveGame`, `GravCrane.PostNetBeginPlay`, `GravCraneLifter.OnMove` | | `MarkForSaveGame()` |

So the chain is: **both writes come from `GameProfile`** (profile start and continue), through `GameInfo.SavePackage`;
**both loads come from `PlayerController` as a console command**, not as a call to the native — and the checkpoint
filename is composed there, `c_<mission>_<checkpoint>.sgs`. `GameInfo.PostLogin` is the resume path: when
`bIsSaveGame` is set it calls `LoadSaveGame(SaveGameFileName)` for the filename a console command left pending.
Eligibility is opt-in and per-class: `MarkForSaveGame` is called from six places, four of them the engine's own
`PreSaveGame` hook, which is consistent with the exporter's actor set rather than a bespoke selection pass.

**Measured 2026-11-12: the exec route to a checkpoint does not load, and it costs the guest.** From a loaded
`M00`, `checkpointload c_m02a_drillsite.sgs` (`exec function CheckpointLoad(Filename)` ->
`ConsoleCommand("LoadCheckPoint" @ Filename)`, `Engine/PlayerController.uc`) is accepted and executed -- the console
line drew and the submit cleared it -- and the guest is at the kernel's `Please insert an Xbox disc...` prompt inside
50 s. The file is one of the nine shipped `System/Profiles/DefaultProfile/C_*.sgs`, and the command names it exactly,
so the failure is not a missing name: a checkpoint appears to be loadable only inside the level that wrote it
(`RestartCheckpoint` composes `c_<current level>_<checkpoint>.sgs`). Any future save-based route into the other 30
maps has to start from a `Continue` profile, not from a checkpoint of a different level.

**Addresses, with no boot (2026-11-09).** The registration table (`inventory/native_targets.csv`, `docs/NATIVE_VM.md`
*The registration table*) names both bodies: `GameInfo.SavePackage` is `0x00298bb0` and `GameInfo.LoadSaveGame` is
`0x00149370`. So the `0x0012xxxx` neighbourhood search is no longer the way in — these are the two functions to read,
and `0x001226a0` (the one body that references the wide string `LoadSaveGameInfo`) is where the save/load/check
module begins.

**Measured 2026-11-09, no boot: the load half is complete and the write half is a stub.** The registration table
(`inventory/native_targets.csv`) puts `GameInfo.LoadSaveGame` at `0x00149370`, a 237-byte exec that decodes its string
argument and calls the loader `0x00134d40` (2,472 bytes, the `[VL:LOADGAME]` log trail, returns 1 or 0).
`GameInfo.SavePackage` is `0x00298bb0`, 145 bytes: it decodes the same argument and stores **0** into the result
pointer — no call, no log, no writer — so `bool SavePackage(string)` answers false for both of its script callers
(`GameProfile.StartNewMatch`, `ContinueSinglePlayerGame`) and neither reads the result.

**A writer does exist, one address family over:** `FUN_00138680` (2,278 bytes) is the routine behind
`[VL:SAVEGAME]: Saving game to file: %s` (literal `0x00509b50`, site `0x00138c7b`) — the counterpart of the loader —
and it has no direct call site, so it is dispatched rather than called. The `SaveGame Summary Names=%d Import=%d
Export=%d` literal (`0x004df8e8`, site `0x00070938`) is inside `Core_UnObjBas`'s 5,552-byte body, i.e. the exporter
is the engine's own package serializer. Depth: `docs/formats/sgs-checkpoint.md`, *The two natives, measured*.

**Answered 2026-11-09: the dispatcher is the engine's exec body, and the writer is a vtable slot.** The three
`PlayerController` commands are UnrealScript only — `NewSaveGame`, `QuickSave`, `CheckpointSave`,
`MapChangeAutoSave` and `MatineeAutoSave` each end in `ConsoleCommand("SaveGame <letter> <text>")`, so the write path
begins as a *console string*, not as a call. `FUN_00139d50` (4,829 bytes) parses it: `SAVEGAME` takes the letter
`A`/`C`/`M`/`Q` into four booleans and dispatches through `call [eax+0xec]` on the `GameEngine` object (`0x0013a5bb`),
and slot 59 of the `GameEngine` vtable `0x00509438` is `0x00138680` — agreeing with the one dword in the image that
holds that address (`0x00509524`). The load commands (`LOADSAVEGAME`, `LOADCHECKPOINT`, `LOADQUICK`, `LOADTRAVEL`) go
to `0x00136a40`, which composes the name and compares the `SAVEGAME`/`CHECKPOINT`/`QUICKSAVE` literals; the
`.sgs` strings are name-table strings with no `push` xrefs, which is why neither side was findable that way.
**The writer's name, measured 2026-11-09.** `0x00138680` labels the save (`Map Change` / `QUICKSAVE` / `CHECKPOINT` /
`SAVEGAME`, logged with the loader's own prefix) and then builds the file from one of five formats: `TRAVEL_%s`,
`Q_A_%s`, `C_%s`, `MATINEE_%s`, all followed by `%s.sgs`, or `%s_%s.sgs` for a manual save. The checkpoint format
reproduces all nine shipped `System/Profiles/DefaultProfile/*.sgs` names exactly. It then logs
`[VL:SAVEGAME]: Saving game to file: %s`, sets a saving flag at `+0x708`, and calls the engine serializer.
**Answered 2026-11-09: the writer's "next" is two artefacts, and the index row has its own address.** The
`Engine` / `Saving` literals (`0x004c7948`, `0x004df84c`, read from the instruction operands with
`tools/ghidra/ghidra_scripts/ExportLiterals.java`) are the arguments of a config **read** (`FUN_000584a0` →
`FUN_00058010`, which formats `%s%ssystem%s%s.%s` and calls slot `+0x10` of `0x005fbed4` with a `0x400` buffer) —
not a profile write. The `+0x708` flag is set to **2** at `0x00138c96` and back to **0** at `0x00138ebc`, on the
first element of the level's actor array, spanning the serializer call at `0x00138cf2`. The `[Saved Games]` row is
written elsewhere: the section name literal at `0x00515664` has three xrefs, all inside `0x001be5xx`–`0x001bfax`,
and the writer reaches that code through `FUN_001be720` (`0x00138d9d`) → `FUN_001be520`, which walks the profile's
array at `+0xa8` (count `+0x38`, names `+0x28`, descriptions `+0x34`) and writes one row per element. The shipped
index has 142 rows in four key shapes and **none of its files is present, while none of the nine shipped `.sgs` is
indexed** — the index is an April 2005 mission-7 session, the files are the writer's `C_<description>.sgs`.
**Still open:** the serializer's exact arity (seven words are pushed, the caller cleans 16 — its epilogue is not
where its Ghidra body size puts it) and whether slot `+0x34` is a `SetString`.

**Corrected 2026-11-09: `0x001226a0` is not a reader, and the native half has no hole.** That 7,486-byte body is the
engine's **startup name table** — 290 wide literals, one `FName` construction each, each stored into its own global
across `0x007aae9c`–`0x007b5dbc`, in alphabetical order from `Accept` to `ZoneChange`. `LoadSaveGameInfo` is row 118
of 290 (`0x00505b14`, site `0x0012318a`), between `LIPSincAnimEnd` and `Login`. After the names the body resolves the
`Engine` and `Core` packages, walks the global class array running the registration step with flag `0x2002`, and logs
`Unreal engine initialized`. So the earlier "the save/load/check module begins here" was an artefact of the
literal-xref route: every engine name appears once in this table, so the body an xref finds is always the one that
looks like it starts that subsystem. The whole path is the loader `0x00134d40`, the writer `0x00138680` and the
profile writer `FUN_001be520`; what remains is the writer's serializer arity, not a missing reader. Depth:
`docs/formats/sgs-checkpoint.md`, *Not a reader*.

**Measured 2026-11-14: the loader composes a rootless path, and the device segment is empty.** The loader's three
joins are the device string, a profile and the name the console typed; the device goes through `FUN_001be350`, which
special-cases `L"defaultdevice"` and asks the file manager (singleton `[0x005bf514]`, vtable slot `+0x38`) for the
device path before appending `\`, `L"Profiles"` and `\`. That slot, `0x00034210`, is 33 bytes whose whole body
constructs the empty string, so the prefix is exactly `\Profiles\<profile>\` and the device name never reaches it;
26 sites across the save/profile module push the same literal, so this is the module's root rather than the loader's
quirk. The disc keeps all twelve save/profile files under `System/Profiles/<profile>/` -- the same tail, one `System\`
short -- and `DefaultProfile` appears in no wide literal while the only save-directory literal is `..\Saves\` and the
inis carry `SavePath=..\Save`, which exists nowhere in the dump. **Still open:** the file manager's base directory,
which decides between a resolved path that fails inside the level and the loader's own `FAILED` branch; the `System\`
literal at `0x004c9790` is stored at `0x00149603`, inside no committed function. Depth:
`docs/systems/CHECKPOINTS.md`, *Where the loader looks*; `tools/save-path-compose.py --check` is dump-free.

**Answered 2026-11-14: there is no base directory to read, and the object the chain asks is an error file manager.**
The `System\` literal at `0x004c9790` is pushed from eight sites and every one is inside a mapped function (`0x00149602`
is `execGetMapName`, `0x00149460` + 2,223 bytes -- the "inside no committed function" reading came from an address one
byte past the push), so it is a caller-side prefix, not a directory the loader inherits. The singleton the chain asks
(`[0x005bf514]` -> `0x005c1a0c`) carries vtable `0x004d2fd0`: 25 slots, `FFileManagerError_CreateFileReader` and
`_CreateFileWriter` at `+0x04`/`+0x08` (log and return 0), `-1` at `+0x0c`, sixteen bodies of eight bytes or less, and
no slot that answers a directory. The only other path-shaped slot, `+0x3c` (`0x00034240`), returns a drive-qualified
or separator-leading argument verbatim and asks the device getter only for a path that is not rooted, so the loader's
`\Profiles\<profile>\<name>` is passed through untouched to `LoadPackage` (`0x00073660` -> `Core_UnObj`, flags
`0x1088`). **Still open:** whether that package loader's own reader resolves a root-relative name. Depth:
`docs/systems/CHECKPOINTS.md`, *What the build's own path strings name*; `tools/path-literals.py --check` is dump-free.

**Sharper again 2026-11-14: 157 virtual calls reach that object and 120 of them land on a body that answers a constant.**
`tools/fmgr-dispatch.py` resolves every `call [reg+disp]` whose receiver is `ds:0x5bf514`: 17 of the 25 slots are
called, `+0x04` (`FFileManagerError_CreateFileReader`) has 20 sites and `+0x08` 10, and one of the twenty is
`0x0013499b` in `FUN_00134960`, so the save module asks for a reader and gets NULL. The resolver `+0x3c` has exactly
one caller (`0x002931dd`, `FUN_00293100`) and it is not the save chain. **Now open:** which object the global holds at
runtime -- its `.data` initializer is `0x005c1a0c` (the error table), and the only write to it in `.text` (`0x00169e3`)
stores a frame-local address from `0x00169d2` in the function that logs `Starting Engine`. Depth:
`docs/systems/CHECKPOINTS.md`, *What the file-manager table is asked for*.

**Answered 2026-11-14: the live value is a frame local of the initialiser, handed in as an argument.** `Core_UnMisc`
(`0x0005a990`) has exactly one caller, `0x00012b7d` in `Core_UnObjBas` (`tools/call-census.py --target 0x0005a990`), and
the value it stores into `[0x005bf514]` is that call's **seventh** argument: three SEH pushes precede
`mov eax,[esp+0x28]` at `0x5a99e`, and the site pushes `[ebp-0x954]` there (`0x12b4c`). The same call re-points three
neighbours -- `0x5bf510` from arg3 `0x0059ed68`, `0x5bf500` from arg4 `0x005bf3ec`, `0x5bf504` from arg5 `0x005bf3f0` --
so the initialiser installs a set of pointers, and only the file manager's comes from the frame. That slot is never
written inside `Core_UnObjBas` (all eight references are `lea`), so the object is built through calls that take its
address, and the `.data` initializer (the error table) describes the state *before* that call. **Still open:** which
class the frame object is -- answered in the note below.

**Answered 2026-11-14: the initialiser builds a file manager with a vtable no script class owns.** The first receiver of
the frame local is `0x128ff` (`lea ecx`, `call 0x1a3b0`), and that constructor stores its table at `0x1a3c2`:
`0x4c8db8`. The table is not in `inventory/xbe_class_tables.csv` -- its only dword reference in the whole image is that
store -- and against the static error object's `0x4d2fd0` it overrides **13 of 25** slots, including `+0x04`
(`CreateFileReader`, `0x0001d290`), `+0x08` (`CreateFileWriter`, `0x0001d6d0`) and the device resolver `+0x38`
(`0x0001c4f0`), and its constructor builds a `Z:\Cache` string beside the object at `+0x4`. So the "asks for a reader
and gets NULL" and the "device path is the empty string" findings describe the `.data` fallback, not the object the
initialiser installs. **Still open:** whether that frame object is alive when the loader runs, which is the
initialiser's exit path rather than another table. Depth: `docs/systems/CHECKPOINTS.md`, *Which class the initialiser
builds there*; `inventory/fmgr_live_vtable.csv` (`--live`, dump-free `--check`).

**Answered 2026-11-14: the file-manager global is installed twice, both times from `main`'s frame.** The containing
function of both stores is the process's own entry body -- one CRT call site at `0x33f2ff`, one `ret`, and the
`Starting Engine` / `Launching map` / `Entering Loop` literals -- so the frame outlives every game call, and the `.data`
object `0x005c1a0c` (table `0x004d2fd0`) describes only the image before it runs. The second store (`0x169e3`,
`[ebp-0xb0]`) comes after the `0x4c8db8` object and immediately before `Starting Engine`, so the engine phase runs with
an object nothing builds -- `0x6c750` writes nothing through `ecx` and only walks the object array at `0x006c2188`,
so the second install is a teardown of the file layer, not the engine's install. The loader `0x00134d40` and
`GameInfo.LoadSaveGame` `0x00149370` never read the global at all; the load path reaches it one call deeper (`0x001347d0`, `0x00134960`), and the save side reads it
directly (writer `0x00138680` once, serializer `0x0006f4b0` three times). Artifact `inventory/fmgr_installs.csv`.


**The purge over the array at `0x006c2188` cannot select a different object, and its `[+0x498]` byte is a class
flag.** `0x77210` answers its own argument on every path (one `ret`, `mov eax,edi` before it, `edi` written once from
`[esp+0xc]`), so the per-element call in `0x6c750` can only log the literal at `0x4d43dc` -- it cannot substitute one
element for another, and "which element does the file layer get" has no second answer. The array itself is one
`TArray` (data `0x006c2188`, count `0x006c218c`, capacity `0x006c2190`): two push sites behind a capacity check
(`0x0005283e`, `0x0005311e`) and one clear that zeroes count and capacity together (`0x00069c38`). Its elements are
containers with a filter field at `+0x28`, two record tables (`+0x7c`/`+0x80` by `0x1c`, `+0x88`/`+0x8c` by `0x24`),
an object at each `0x24` record's `+0x1c`, that object's class at `+0x24`, and one byte of the class at `+0x498`
tested for its sign -- import and export tables with a linker root, not a file-manager slot. **What would settle what
is left:** the class flag word at `+0x498` is named 93 times in the image and only the purge's access is byte-wide, so
the next read is `Core_UnClass`'s serialiser and which `CLASS_*` bit sits in the top bit of that byte; the save chain
does not depend on it, since the global is set from `main`'s frame slot at `0x169e3`, not from an element of this
array. Artifact: `inventory/linker_purge.csv`, tool `tools/linker-purge.py`.


## U6. Two shared helpers named from their own strings; the third is still `FUN_*`, and the four game bodies are identified

**Observed.** In the 43 functions of the `stargategame`/`stargatevehicles` modules, `FUN_00058a30` is
called by every one of them as `(expression, file, line)`, and `FUN_000340c0`/`FUN_0003af60` by 40 of
them, at the site that constructs a class from `(name, outer, object size, flags)` literals. Those three
unnamed functions were the reason the modules read as `FUN_*` soup. Three of the 43 do not follow the
registration pattern at all — the `StargateCar` physics body (`0x00311520`), two `SGCameraEffects`
bodies (`0x00316b10`, `0x00318060`). A fourth, `0x003107b0`, *does* follow it; what it lacks is a
literal the decompiler would print (below).

**Named 2026-10-24, from the strings each one logs.** Both names are recorded in
`symbols/xbe_overrides.csv` with their evidence; nothing was inferred from a guess.

| address | name | evidence |
|---|---|---|
| `0x00058a30` | `appAssertFailed` | 67 bytes. Wide literal `"Assertion failed: %s [File:%s] [Line: %i]"` (`0x004dafb0`) is the format it logs; **2,432 direct call sites**, every one passing `(expression, __FILE__, line)`. |
| `0x000340c0` | `appMallocBeforeInit` | 29 bytes. Logs the wide literal `"Called %s before memory init"` (`0x004d2ecc`) with `"appMalloc"` (`0x004d2f08`) and returns 0; **961 call sites, all `call [reg]`**, i.e. reached through a pointer, which is what an allocation entry point looks like before `GMalloc` exists. |

What the assert helper does is now measured rather than assumed: it converts the expression and the file
name to wide strings through `0x00058740` (a scratch converter with a `0x400` cap that bumps a 256-slot
block allocator at `0x00637e90`), passes both plus the line to `0x000574d0` (formats into a `0x1000`
stack buffer with `vsnprintf` at `0x003e5cc4`, checks the log-suppression globals at `0x006be0c8`/
`0x006be0dc`, then dispatches through the output device's vtable), and **returns** — there is no `int3`,
no abort and no debugger break in the body, so `check`/`verify` here logs and continues.

**`FUN_0003af60` is classified, and its name is a decision the image does not make (2026-11-10).**
Measured: 273 bytes, **493 direct call sites**, installs the **`Class`** class table (`0x004d4870`, object
size 1312), `or`s `0x12` into the flags at `+0x498`, clears `+0x49c`…`+0x51c`, stores 13 parameters at
`+0x4a0`…, returns `this` with `ret 0x34` (52 bytes of stack arguments), and starts by calling
`FUN_0003a5f0` (125 bytes, itself a `UnClass.cpp` body). `tools/class-ctors.py` puts it in the population it
belongs to (`inventory/class_constructors.csv`): `Vtbl_Class` is installed by **four** functions —
`0x0003ad20` (no stack arguments), `0x0003ae30` (a parent class, 4 bytes), `0x0003af60` (the registration
overload) and the deleting destructor `0x0003dbd0`. `ctor_Class` therefore goes to `0x0003ad20`, the
constructor the `Class` registration itself reaches, and `0x0003af60` is **a second constructor of the same
class**: it constructs a script class's `Class` object from the registration's own data, which is why all 493
registrations call it. An overload has no name in the image, and `docs/CONVENTIONS.md` forbids numbering one
into the map, so it stays `FUN_0003af60`. That answers U6's question — *what to call it* — as far as the
build can: the class is settled, the role is settled, the name is not the image's to give. (`docs/NATIVE_CLASSES.md`
counts the 915 constructors and destructors that do earn names.)

**Closed 2026-11-10: `0x003107b0` is `ucpp_Register_GoauldDrone`.** Its constructor call does carry the
class name -- the decompiler printed that argument as `&DAT_0053b42a`, and a reader that trusts the text
took the label for "no literal". One VA read settles it: `0x53b42a` holds the UTF-16 `L"GoauldDrone"`, the
dword pushed at `0x00310806`, and the body then asserts `ReturnClass` at
`...\stargatevehicles\src\GoauldDrone.cpp:5` exactly like the other stubs. Object size 2,532, the
fortieth row in `symbols/xbe_overrides.csv`. `tools/ucpp-registrations.py --xbe` reads the class name from
the call site's own pushed immediates whenever the decompiled text renders fewer than two literals, so no
body can be written off for what the decompiler declined to print.

**The three real game bodies are described the same way, by their assert seams.** Each calls the assert
helper at its own source line, and both the expression and the `\<Module>\Src\<File>.cpp` path are
immediates the same reader resolves: `0x00311520` asserts `Mesh` at `StargateCar.cpp:190` (three seams in
the body), `0x00316b10` `DOFResourceRefCount > 0` at `SGCameraEffects.cpp:140`, `0x00318060`
`SharedPixelShaderDOFMaterial == NULL && BlurTargets.Num() == 0` at line 119. A third `SGCameraEffects`
body outside the 43, `0x00318140`, asserts `DOFResourceRefCount == 0` at line 162 -- all three read the
counter at `0x007b93f8` -- so "not a registration stub" here means the depth-of-field resource counter and
its constructor/destructor, not unclassified code.

**One extractor bug came out of this and matters more than the row.** `appAssertFailed` was named from its
own literal (above), the export prints the name, and the extractor was still pinned to `FUN_00058a30`: it
matched no assert at all and reported **0 registration stubs named** against 40 bodies that each hold one,
silently, because its only other output is a list of "left unnamed" bodies. It now accepts both spellings,
and `--check` drives them both.

**Call-site census (measured).** `tools/call-census.py` over the Ghidra export: `appAssertFailed` has
**2,432 call sites** in `build/ghidra/evidence/calls.tsv` (2,245 of them from 1,270 distinct callers
after the census collapses repeated sites), spread across the engine's own modules (Core/UnTemplate
731, Core/UnMath 136, Core/UnObjBas 115, Core/UnClass 107, Engine/UnActor 100, Core/UnObj 76,
Engine/UnChan 71, unnamed 60, Gui/UnGUI 32, Engine/KFarfield 31). A helper called from a third of the
binary's functions is not script-specific glue.

## U7. How `GNatives` is populated, and how to read it

**Observed.** `GNatives` is at `0x006313e8` with `EX_Max` = 4,096 entries, in the part of `.data` the
image does not initialise — the file contains no slot bytes (`docs/NATIVE_VM.md`). Startup fills all
4,096 slots with one default stub (`0x000404a0`, the `rep stos` in one of the static initialisers) and
then patches individual slots: 233 `mov [slot], reg` write sites are visible statically, 776 places call
a fixed slot directly (`call [slot+N*4]`), and 1,428 places index it (`call [slot+reg*4]`), covering 256
distinct slots.

**Why it matters.** 4.4 — naming natives from the script's `native(index)` declarations — needs
`index -> address`. Without it, the 274 declared indices can be named but not located, and the 523
index-less declarations (U1) stay unexplained.

**Options, none tried yet.**

1. **Read it from a running build.** The prototype already boots in xemu to the main menu
   (`docs/XEMU.md`). With the symbol map loaded, dump the 16 KB array at `0x006313e8` from guest memory
   once the engine has started; that is the whole table, one command.
2. **Follow the write sites.** The 233 `mov [slot], reg` sites are inside initialisation code that knows
   both the slot number and the function it stores, so disassembling them yields a partial static map.
3. **Trace the interpreter.** Break at `0x0003a0a7` and record `(iNative, GNatives[iNative])` while the
   menu runs; script-visible behaviour then pairs an index with a body.

**Resolved 2026-10-23, partly.** Option 1 was taken: a 16 KB dump over the xemu GDB stub at the main
menu gives 356 registered slots (3,740 still hold the startup default `0x000404a0`), all 356 distinct
and inside `.text`, and **all 273 declared indices are present**. `inventory/gnatives_slots.csv` and
`symbols/xbe_dispatch.csv` carry the map; `docs/NATIVE_VM.md` has the validation and the procedure.

**Option 2 is measured too, and it is a validation rather than a substitute (2026-11-10).**
`tools/gnatives-writes.py` scans the image for the two store forms that name an absolute slot and resolves each
`mov [slot], reg` value through the pointer table the preceding instruction loads from: **233 store sites, 233
different slots, and all 233 store exactly the address the runtime dump holds there** (`0 slots the dump does not
have`). The **123** runtime slots with no static store are registered through `mov [ecx+eax*4], edx`, which encodes
no absolute address -- so a store scan can see two thirds of the table and can never see the rest
(`docs/NATIVE_VM.md`, `inventory/gnatives_write_sites.csv`). A partial static map used to be one of the three ways
this entry could have been closed; it is now a check on the way it was closed.

**The blind 123 are mostly records, not unknown (2026-11-10).** The store scan's blind spot is answered from
`.data`: **113 of the 123** storeless slots hold the body of a `{pool name, body}` record, so those slots are filled
by a walk over the record block rather than by a per-slot store (`tools/gnatives-records.py`, `make
gnatives-records`, `inventory/gnatives_records.csv`). Ten are left -- 302, 333, 384, 385, 501, 503, 509, 511, 528
and 561 -- each with its body present in `.data` but not as a `{name, body}` record. The rest of the entry stands:
83 registered slots have no declarer, and a full map still waits on a dump after a mission load.

**Closed 2026-11-10: the storeless 123 are per-entry stubs, and the ten are in them.** A store scan cannot see
them because the write is `mov [eax*4+0x6313e8], edx` inside the registrar at `0x46c60`, reached from a stub that
pushes `&body` and **the slot index as a literal**: 123 stubs, one per storeless slot, and all 123 store the value
the guest holds there (`tools/gnatives-stubs.py`, `make gnatives-stubs`, `inventory/gnatives_stubs.csv`). 233 + 123 =
**356 of 356** live slots, so every slot's index is in the image and the static map is complete up to a name. The ten
the record pass left -- 302, 333, 384, 385, 501, 503, 509, 511, 528, 561 -- are in the same set, with their stub
named in the artifact; what they lack is a script declarer, not an address. The mechanism above is corrected
accordingly: the 123 are stub-registered, not filled by a walk over the record block, though the record table does
carry most of the same bodies for its own purpose.

**The other half of the registration file is measured too (2026-11-10).** 467 further stubs push `-1` where the
indexed ones push a slot: 467 distinct bodies, 467 distinct `{name, body}` records (no name twice), **0** bodies
shared with the indexed set and **0** in the live table -- the registrar returns for `-1`, so these are the
name-resolved natives, and 467 of the 523 index-less declarations have one (`tools/gnatives-name-stubs.py`, `make
gnatives-name-stubs`, `inventory/gnatives_name_stubs.csv`). The 56 without a stub are Object 41, BrushBuilder 13,
MaterialFactory 1 and UPreviewSkinnedButton 1.

**The 56 are settled, and none of them is missing from the registration file (2026-11-10).** They use the
build's other two channels rather than a gap: **41 of them are `Object`** and carry a `{name, body}` record, which
is what the `int%sexec%s` lookup binds from (U1) -- `Object` is the one class registered through the store table, so
all 230 of its live slots are store-filled, 0 of its 160 indexed natives has a stub and just one of its 42
index-less declarations has a `-1` stub; no stub for the rest is the class's normal state. The other **15** are
`Editor` (13 `BrushBuilder` + 1 `MaterialFactory`) and `UPreview` (1 `UPreviewSkinnedButton`), and the image carries
**no** occurrence of those class names, their functions, or any `int<prefix><Class>exec<Function>` literal for them
-- `Editor` and `UPreview` are the only two packages with 0 of their declarations bound by name, and both sit in
`System/Default.ini`'s `EditPackages` and in no runtime package list, so the shipped build never loads them
(`tools/native-channels.py`, `make native-channels`, `inventory/native_channels.csv`). All 508 declarations that have
a binder literal have it in the image. The same run removed a fabrication in `tools/native-reconcile.py` that had
listed `UPreview`'s two records as `text-missing` against a literal it invented; they read `record-unbound` now.

**The slots no package declares are token bodies, and their registration was never in doubt (2026-11-10).** Slots 66,
67 and 68 are the only store-written `GNatives` entries below the native band, and every store site's setter spells its
index at `0x006353e8` (231 of the 233 as an immediate, the other two through a register), so the question this entry used to ask
about them -- how they were registered -- is answered. What is still missing is a *name*: 356 live slots split 56 / 16
/ 284 at `0x60` and `0x70` (tokens, extended-native trampolines, declared natives from 112 = `execConcat_StrStr`), and
three low-band bodies whose bytes fix the stock numbering (11 = bare `ret 8`, 29 = int const, 30 = float const) put
66-68 at tokens `0x42`-`0x44`. Their behaviour is recorded in `docs/NATIVE_VM.md`; a name needs a dump taken while a
mission's scripts run, because no shipped package references an index below 112.

**Closed 2026-11-10: a mission load registers nothing, so 356 slots is the whole table.** A second dump taken with
`M00` loaded (`tools/xemu/gnatives-mission.sh 10`, `make gnatives-mission`, `inventory/gnatives_mission.csv`) holds
**the same 356 slots with the same bodies as the menu dump — 0 gained, 0 lost, 0 replaced**. Registration finishes
at startup, before any map, so the map needs no further boot, and the 83 registered slots with no declarer are
engine-internal execs registered at the same time (named `Native_<index>`), not natives waiting for a mission.
Two instrument notes worth carrying to any later guest read: the table must be read in the context's **linear**
space (`tools/xemu/mem.py --virtual`; a physical read of `0x6313e8` lands on another frame whose tail page reads
zeros, which looks like a zeroed array), and a boot that never left the menu still serves its stub — the read is
gated on `tools/xemu/frame-diff.py`, because a failed boot would otherwise "confirm" the menu table.

**Closed 2026-11-11: the token is the slot, and the low band was arithmetic, not a table.** The bytecode census
read the opcodes above `0x5f` as `op - 0x70` and so reported 38,143 sites on "slots 0..95 that name nothing" -- an
operator band with no declarer, and a `0xe7` arity that disagreed with the declaration it resolved to. `GNatives`
settles it from both sides. First, the image's own `.data` record for each slot (`{slot, intUObjectexec<Function>}`,
the binder strings this entry already measured) spells the body's name: read by the **token as the index**, **111 of
the 120 direct opcodes' bodies are `exec<Function>` for the declaration at that exact index (38,950 sites)**, the
other nine differing only in spelling (`execConcat_StringString` against the declared `Concat_StrStr`); read by
`op - 0x70`, none of the 120 match. Second, the arity: `0xe7` is slot 231 = `Object.Log`, and 1,072 of its 1,186
sites pass one argument, while `0x77` is slot 119 = `Object.NotEqual_ObjectObject` with 5,368 sites of exactly two.
The one-byte form spans `112..255` -- the declared band -- and the two-byte form exists only beyond it, so nothing is
numbered from 0 and there is no second table. `tools/uscript-dis.py --native-calls` now scores `op`, `op - 0x70` and
`op - 0x60` side by side (42,056 / 5,864 / 5,213 sites resolving) and writes `inventory/native_opcodes.csv` with the
slot's record name and whether it matches; `docs/BYTECODE.md` carries the corrected numbers. What remains open in
this entry is unchanged and unrelated: a name for the 83 registered slots that no package declares.

## U8. Which slot dispatches `ProcessEvent`, and the assert sites with no function

**`ProcessEvent` found 2026-10-24, at `0x0004bf20`.** It was located by scanning the executable for
calls to `ProcessInternal` rather than by name: its two call sites (`0x4c10f`, `0x4c23e`) are the only
ones in the image and both are inside this function. Its shape confirms it — `Function->iNative` at
`[Function+0x84]` selects the native path (`call [Function+0x90]`), and the script path builds an
`FFrame` on the stack (vtable `0x4d9618`, `Object +0x04`, `Node +0x08`, `Code +0x0c`, `Locals +0x10`)
and calls `ProcessInternal` directly (`docs/NATIVE_VM.md`).

**Dispatch slot closed 2026-10-24: slot 21, byte offset `+0x54`.** The 492 pointers are 492 tables
of code pointers in `.rdata` (`0x004c78c0`–`0x0053b168`, 30–159 slots each, packed), each with exactly
21 code slots before `0x0004bf20` and no other address at that slot; every table start is named by an
immediate in `.text`. So the fork dispatches `ProcessEvent` as a virtual at a fixed slot through
`[object+0x00]`, which is why nothing calls it. Measurements and the tool:
`docs/NATIVE_VM.md`, `tools/class-tables.py`, `inventory/xbe_class_tables.csv`.

**Two families closed 2026-10-24, two residuals open.** The tables are named from the build's own
class registrations (`docs/NATIVE_CLASSES.md`, `inventory/xbe_class_table_names.csv`): 466 of 492.
Slots 19 and 20 hold `0x00020f00` — one `ret` — in 327 tables and real bodies in 165, and the 165 are
the actor-side engine hierarchy (`Actor`, `Pawn`, `Controller`, `Info`, `NavigationPoint`, …): the
split is which classes override those virtuals, not which are abstract.

Still open: **which virtuals slots 19 and 20 are** (the override reads `[this+0x2dc] & 1`, then
`[this+0xa4]` and `[+0xf4]`; nothing in the property track calls them), and **the 26 tables whose
registration cannot be followed** — their constructors install the table but the registration pushes
neither a literal nor a thunk the tool can follow. What would settle the first: a call site that
dispatches `+0x4c`/`+0x50` through a known object.

**Assert-site residuals: 6 sites, down from 199 (2026-10-24).** Four passes now run, in order: entry
candidates at the fill byte before a site (**76 seeds, 58 verified, 18 rejected**), entries named by
data tables (629), **entries found by decoding the gap** (`seedFromGaps`, 12 entries), and block
promotion for what is left (25 blocks). Measured on the same dump with the same command (`make ghidra`):

| measurement | before the gap pass | after |
|---|---|---|
| sites in no function, end of the seeder | 50 | **5** |
| sites in no function, against the committed map | 49 | **6** |
| the xref count `XbePost` reports | 50 | **5** |
| functions | 14,944 | **14,956** |
| assert-attributed functions | 1,288 | **1,300** |

The gap pass is the "second, independent decode" this section asked for, and it confirms what the
earlier note only suspected: the 49 were not undecodable. It takes the byte after a 0xCC fill run and
accepts an entry only on three measurements — the body created there contains the site, the
instruction covering the site is a `PUSH` whose immediate is the file string the asserts table names
for that row, and the body ends at a terminator. The 47 code sites had been rejected by
`looksLikePrologue`, an opening-byte list: none of the 14 entries they sit behind opens with a byte
the list contains (they open with an argument load — `mov eax,[esp+8]`, `mov eax,[0x7baaf8]`,
`movzx eax,[esp+4]`). The list is kept for the first pass so its measured results do not move; the
entries recovered without it are listed in `inventory/xbe_gap_seeds.csv`, like block seeds.

**What the last six are.** Two are not call sites at all: `0x00592cb6` and `0x00592cbe` are dwords in
a table of 8-byte records in `.rdata` whose value happens to equal the address of the file string
`0x00520040`. The xref scan looks for the address anywhere in an executable section and this build
marks `.rdata` executable, so an address alone cannot tell a dword from an operand;
`tools/assert-residual.py` separates them with the byte before the dword, which has to be `0x68`
(`push`) at a call site and is `0x87` there. The other four are `push` sites whose candidate entries
all belong to a function the analysis already has, and the three tests reject what is left; they are
listed, with the entry the decode found, in `inventory/xbe_assert_residual.csv`
(`make assert-residual`).

**The promoted blocks are not function entries.** `inventory/xbe_block_seeds.csv` lists the 25
blocks the last pass promoted, each with the evidence site it still contains and its body size, so a
decompiled body that starts at one is not mistaken for a function (`docs/NATIVE.md`). They exist so
assert-attributed code can be read and decompiled; they are not entries the build names.

**Status.** `ProcessEvent` closed. The assert-site thread is closed at 6 sites, two of which
are dwords in a data table rather than call sites. The cost of the gap pass is one body that moves:
one line of the map is **not reproducible**: `FUN_00256280`'s body measures **35,070 bytes in four runs
and 38,716 in two others** of identical code on the same dump, and the difference is exactly the 3,646
addresses (0.09% of the image) that fall out of a function in the smaller variant. That is the
analyser's order, not this pass — one run of the unmodified seeder reproduced the committed map byte
for byte and another produced the smaller body. Until that body is explained the map cannot be called
byte-for-byte reproducible, and the next run that moves it is not a regression. The slot-index question (which virtuals 19/20 are, the 26 unnamed tables) is
unchanged and is not a coverage problem. 


**Store evidence, the fourth seed source (2026-11-10).** U8's thread was assert sites; the class-table store
is an independent one, because its immediate names the class. `SeedStores.java` seeded **15 entries from the
31 stores that sat in no function**, taking that count to **8** and functions 14,957 → 14,973, and
`inventory/xbe_store_seed_residual.csv` records each of the 8 with the test it failed (seven: the body from
the nearest `0xCC` boundary ends before the store; two: the body does not end at a terminator). What would
settle the rest is a decode that does not assume the body starts at the nearest fill byte: the
`Struct`/`State`/`Function`/`Class` block at `0x3a180`–`0x3c494` is where the seven are, and the
registration calls one of them without the table store belonging to the same body.

**The tree half of U8 is measured and stops (2026-11-10).** The class constructors encode the subclass
relation: each installs its own table and calls its parent's before the store, and
`inventory/class_constructors.csv` records that call. Inverted, the edge reproduces the script tree for
**375 of 391** named tables that have a child (15 disagree, 76 leaves), so it is evidence -- and it names
`Property` (`0x004d1468`), `LevelBase` (`0x004fc4f0`) and `Player` (`0x004c9348`) among the 26 from their
partly-unnamed child sets. It cannot name the other 23: they have no callers. The site join is now
measured too, and it answers the question this section was waiting for: **25 of the 27 unnamed sites
push one shared pointer** (`0x00034650` -> thunk `0x00062ce0`), and the decompile of that body shows why --
`MOV EAX,ECX; MOV [EAX],0x004dd2a8; RET`, a nine-byte **default constructor** that stores one fixed dword
and names no class. That dword is the table the map calls `VertexStreamBase` (`0x004dd2a8`), and the
loaded level carries it on **51 classes / 550 objects**, so it is not an identity either. The other two
sites push the *neighbouring* `WaterTexture` constructor (`Texture`, `FractalTexture`) and the last
(`FireTexture`) one that installs `IceTexture`'s table, so **no one of the 27 pushes a constructor that
installs its own class's table**: the literal path cannot name any of the 26, measured rather than
assumed. What is left is an argument that is not the constructor -- the site's fourth and fifth pushes,
or the registration call read at runtime with a breakpoint. `tools/ctor-argument.py`,
`inventory/registration_ctor_arguments.csv`, `tools/class-tree-edges.py`, `docs/NATIVE_CLASS_TREE.md`.

**The runtime read is taken (2026-11-10), and it names one row and none of the 26.** A booted guest at the title
(`drive.py --gdb=12641 --linger=1800`, 42,152 objects) walked **42,135 of 42,152** object headers over 255 pages
and paired them with **179 class tables / 312 rows** (`inventory/live_class_tables.csv`,
`tools/xemu/class-tables-live.py`). `tools/class-table-reconcile.py` scores that against the literal artifact:
**139 agree**, **12 name an ancestor of the object's class** (both readings true -- the literal names the
constructor that installs the table, the read names the class of the instance, `Triggers`/`SGC_SecurityReader`),
**26 tables are carried by more than one class**, **one is inverted** (`0x004cfba0`: the literal says
`WaterTexture`, 454 objects of class `Texture` carry it) and **one is not a class at all** (`0x004e1ce8`: the
literal read `new`, the objects say `System`). **None of the 26 unnamed tables carries an object at the title**,
and 287 of the 466 named ones do not either, so absence there measures which packages the title loads, not the
walk. What would settle the 26 is the same read in a guest **with a level loaded** (M00 holds ~6,500 more
objects), or a package load that instantiates their classes.

**The loaded-level read is taken (2026-11-10) and the 26 are still not carried.** The same walk in a booted **M00** --
`drive.py --gdb=12391 --linger=1800`, `Continue` -> `QUICK SELECT MAP` -> ten `down` -> `A`, the tutorial modal up, so
the level's actors exist -- reads **48,646 objects / 48,631 headers (15 unreadable) / 346 pages** and pairs them with
**189 class tables / 376 rows** (`~/sg1/work/m00live/read.log`,
`inventory/live_class_tables_m00.csv`; 6,494 more objects than the title's 42,152). `class-table-reconcile.py --live`
scores it **agree 142, installer 13, shared 32, inverted 1, unknown-class 1**, i.e. the title's verdicts hold and one
more table agrees. **Not one of the 26 unnamed tables carries an object here either**, and **277 of the 466 named
tables are just as absent**, so the read names 189 of 492: what it witnesses is the package set a given point in the
game loads, and the 26 sit inside the 303 tables no loaded class instantiates. So the runtime route this section asked
for is measured and cannot settle them; what is left is the other route it already named -- a decompile of
`0x00062ce0` to see what the shared argument at `0x00034650` is.

**The installer's callers are the third edge, and they name five (2026-11-10).** A class table is
installed by a function (`inventory/class_constructors.csv` `function_va`), and every subclass's
constructor and destructor calls its parent's first, so the *callers* of an installer are the classes
one level below it. `tools/class-table-parents.py` (`inventory/class_table_parents.csv`, 492 rows,
1,978 caller sites) reads that edge from one `E8` pass plus `symbols/xbox_functions.csv`: it
reproduces **466 of 466** committed names (`agrees`, 0 adjacent, 0 differs), names **five** unnamed
tables -- `0x004c7958` `Material`, `0x004c7a10` `RenderedMaterial`, `0x004c7ac8` `BitmapMaterial`,
`0x004c7c78` `CameraEffect`, `0x005040d0` `Engine` -- and **clashes on two** (`0x004cfe68` ->
`WaterTexture`, `0x004d0228` -> `IceTexture`, each a name the registration gives another table; both
are the texture-family inversion the live read already settled in `0x004cfba0`'s favour). The other
**seventeen are constrained and not named**, for a measured reason: their installers' callers are
native engine classes (`Struct`, `IntProperty`, `TcpNetDriver`, `ActorChannel`, `VertexBuffer`, ...)
with no row in `inventory/classes.csv`, so there is no super to read; the caller set is kept in the
artifact as the constraint. Two (`0x004c9348`, `0x004cfd78`) have no named caller at all. What would
settle the seventeen is not another literal: it is the class objects' own **`ClassConstructor`**
field read at runtime (the registration builds one `UClass` per class even when no instance exists),
or an engine-side `Classes.csv` for `Core`/`IpDrv`/`Engine` natives.

**The class object's constructor field is not carried, and that is the last literal-free route closed
(2026-11-10).** `tools/xemu/class-ctor-field.py` walks the object array in a booted **M00**
(`drive.py --gdb=12441 --linger=1800`, 48,646 objects), takes the **1,059** objects whose `+0x00` is the
`Class` table `0x004d4870` as the registered classes, reads each one's own `FName` at `+0x20`, and scans
the whole object body -- window from the gap to the next object, clamped to `Class`'s 1,312 bytes -- for
every dword equal to a constructor or a destructor in `inventory/class_constructors.csv` (**1,011** VAs).
**Not one class object carries any of them**: 0 hits over 1,059 class objects (7 of 347 pages unreadable),
so the read reproduces **0 of the 466** names it already has. That failure is the measurement: this build
keeps a class's constructor in its registration code and does not store it in the `UClass` instance, which
is also why the registration literal and the installer's callers were the two edges that worked. The
seventeen stay constrained. What would settle them is an engine-side `Classes.csv` for the `Core`/`IpDrv`/
`Engine` natives, or a breakpoint on a registration call that reads the `UClass` as the registration fills
it -- not another field of the class object.

**The site's "fourth and fifth pushes" were already read, and the docs were wrong to call them
unread.** `0x004d0e90` is `L"System"` -- argument 9 in `docs/NATIVE_CLASSES.md`'s own table -- and the
per-site string VA is argument 7, the class literal; `tools/registration-sites.py` decodes both, which
is how 466 names came out. Reading the 13 pushes of a site in call order (the window is
`sub esp,0x10` for args 3-6, then `push` of arg 7 name, arg 8 the outer object, arg 9 `L"System"`,
arg 10 `0x4084004`, arg 11 the constructor, arg 12 `&DAT_000624d0`) leaves no unread literal: the
arguments that differ per site are the name (read), the size (read) and the constructor (measured as
one shared default). That is why this section's remaining route is an edge, not an argument.

**Measured 2026-11-11: the dispatch census, and one runtime zero.** `tools/vcall-dispatch.py`
(`make vcall-dispatch`, `inventory/vcall_dispatch_sites.csv`) decodes every `call [reg+disp]` form in the nine
executable sections and finds **106** sites in `.text` at the two displacements slots 19/20 occupy — 31 at
`+0x4c`, 75 at `+0x50`, through edx 37, eax 30, ecx 15, edi 12, ebx 8, esi 4 — plus 35 in the statically linked
library sections, 33 of those at `+0x50`, which is the control that marks `+0x50` as the noisy displacement.
Fourteen `.text` sites sit inside a named body and every one of them is the object layer (`Core_UnObjBas` 5,
`Core_UnTemplate` 2, `Core_UnClass`, `Core_UnObj`, `Native_65`, two `MasterServerUplink` destructors,
`IpDrv_InternetLink`, `Engine_UnActCol`), so the pair is dispatched by object/class machinery rather than by
gameplay. The runtime half is started and not finished: a breakpoint on both bodies armed `OK` at a loaded `M00`
and recorded **0 stops in 30 s**, and the arm that would sit *before* a level loads is refused — the front end
idles in the kernel (`EIP=0xfffffe5c`, `0x80065996`, `0x80024a97`). What would settle it: a forced arm plus a
read-back of the patched byte before the level loads, then `tools/vcall-stops.py` over the recorded stops.

**Measured 2026-11-11: the forced arm is taken, and the read-back is itself the negative.** `tools/xemu/bp-args.py
--force` armed both bodies at the title with the CPU in the kernel (`CPL 0`, `EIP=0x80071009`); the stub answered
`OK` for each, and the bytes it read back through the same context were `f6` at `0x000c4f60` and `56` at
`0x000c6d00` -- the XBE's own bytes there, so the `0xCC` went into a mapping the game never executes. That is the
read-back doing the work the `OK` cannot, and it closes the force-the-arm route as this instrument stands: the arm
has to be taken with `CPL 3`, and the front end's halt loop makes that a sampling problem, not an arming one.

**Measured 2026-11-11, the read-back is blind and `CPL 3` is unreachable: the arm does land, and the pair is a true
zero in every window covered so far.** Two boots, each arming through `tools/xemu/bp-args.py --wait-user
--control-self`, where the control is a `Z0` on the sampled `EIP` -- an address the CPU is executing at that moment,
so it has to fire at once if the arm landed anywhere:

- **The ring is not the discriminator.** 88 context samples over one boot all came back `CS=0x8` (`CPL 0`), 84 of
  them with `EIP` deep inside the image (`0x00454510`, `0x00463864`, `0x000c8010`). This build runs the front end,
  the menus and the game at ring 0, so the `CPL 3` the note above asks for can never arrive: that route was
  unreachable, not unsampled. `--wait-user` now gates on the image's `0x00010000..0x007d9000` range and both boots
  armed on their **first** sample (`EIP=0x000544e8`, `EIP=0x002bb740`, both `CPL 0`).
- **The armed byte is not a witness, in either direction.** A `Z0` on this stub leaves no `0xCC` in the bytes a
  memory read returns: the control's own byte read back `8b` -- its body's own byte -- and the control stopped on
  that address anyway, in both boots, while the two measured bodies read back `f6`/`56` and never fired. So "the
  patch never reached the game's mapping" was read off an instrument that cannot see a patch, and the forced-arm
  negative above is withdrawn on that ground.
- **With the arm proven, the pair still does not dispatch.** The first boot's window was 60 s (front end, map list).
  The second kept it open **420 s** across the map-list select, the M00 load (`02-mission` 20,414 px changed) and
  180 s of gameplay (`03-gameplay` 6,829 px), and recorded **0 stops at either body** (`0x000c4f60`, `0x000c6d00`)
  while the control fired at once. A window with a proven arm and a silent body is a measurement, not a stall.

What is left is a window rather than an instrument. The pair is dispatched by object/class machinery, so the places
it could still fire are the ones neither boot covered: a level *unload* or travel, a checkpoint load, and a
multiplayer/dedicated path. `--window` is the knob for that (the tool used to end its measurement at the first 60 s
of silence, which is why the first boot covered only the front end).

**Measured 2026-11-12: the checkpoint-load window, and why it is the last one a boot can reach.** With the same
proven arm and control, a loaded `M00` was sent `checkpointload c_m02a_drillsite.sgs` at the console -- typed key by
key, read back off the frame as `(> checkpointload c_m02a_drillsite.sgs_` (400 cyan px, `console-line.py`), and
submitted with `ret`. The command executes and the guest **resets to the BIOS prompt** within 50 s, so a checkpoint
of another level does not load (see U5). Both bodies stayed silent through the select, the load, gameplay and the
command; a `Z0` does not survive that reset, so the window ends there. A second boot walked the mid-game menu to try
a *graceful* unload instead: three downs reached the bottom of the list (two frames byte-identical) and the
confirmation left the screen unchanged, so the level was never unloaded and the pair recorded **0 stops** over a
420 s window (four 60 s timeouts, control fired at once). Windows still uncovered then: a completed unload and the
multiplayer/dedicated path (the first is covered below).
**Measured 2026-11-12: the unload window is covered, and it is a negative.** A graceful unload does exist and it is
one console command: `RestartLevel` (`Engine/PlayerController.uc:3820`, `ClientTravel("?restart", NM_ListenServer,
false)` in standalone play) typed into a loaded `M00`, read off the frame as `(> restartlevel_` (163 cyan px, `242x33`)
before `ret` submitted it. The level came down and back up with the guest alive -- the load screen's own `Loading...
M00` / `Starting Story Mode Game` at +5 s, the `O'NEILL` HUD at +20 s, M00's tutorial modal at +45 s (identical at
+85 s, which is that modal holding input). With the same proven arm and control over a 380 s window that spans the
select, the load, gameplay, the teardown and 200 s of the reloaded level, both bodies recorded **0 stops** and the
control fired at once (`EIP=0x003ed669`), so a full actor teardown is inside the pair's negative: neither body is a
per-actor `Destroy`-style virtual, which would have run thousands of times across it. That leaves the
multiplayer/dedicated path, which a stock disc boot cannot reach (there is no second client), so U8's slot question
has no window a boot can add -- what would settle it is a typed receiver at one of the 106 `call [reg+0x4c]`/
`[reg+0x50]` sites (`inventory/vcall_dispatch_sites.csv`), not another boot.


**2026-11-12, the base is a `.data` singleton, so the 38 candidates are the wrong field.** The callers were the
route this entry named, but the base they pass is not an argument at all: reading the instruction that writes it
(`base_origin` in `inventory/vcall_field_owners.csv`) shows all eight `+0x220` sites loading `eax` from the absolute
`.data:0x7b9ba0`, which has **one store and 177 `mov`-form loads** in the image. That global is a lazily created
singleton (`FUN_003319fc`: create once, `0x224` bytes, a counter at `+0x190`, the pointer returned through an
out-parameter), and its last dword `+0x220` is filled at `0x3316b7` with a 32-byte helper from `0x331381` whose
constructor installs vtable `0x0053f770` -- outside the 492 packed class tables, so the holder is a **native C++
interface, not a `UObject`**. The eight sites are therefore none of the 38 heap candidates: the `Class`->`Function`
rows (533 of 1,004) are a different field at the same displacement, which is the shared-displacement rule this entry
already stated, now with the exact site group that was supposed to break it. What stays open is the module's name,
and no instrument here has it -- every function in `0x334e00`-`0x337500` is unattributed and carries no `__FILE__`
assert, and the family's only outside edge is the XONLINE section's entry stubs (`0x42bf49` and neighbours, each
`mov ecx,[0x7ba330]` then a `jmp` back into `.text`). What would settle that is a name for the singleton's
constructor family -- a signature, or a caller that asserts with a source path -- not another boot.

**2026-11-12, the object pointer's origin: the pair is dispatched on an object another object holds.** The receiver
load names which object, not where the pointer came from, so the same oracle listing is walked one hop further
(`origin`, `origin_va`, `origin_chain` in `inventory/vcall_receiver_sites.csv`). Of the 55 real sites, **28 read the
object from a field of another object** (six different offsets, so not one object graph), **14** from a stack slot
or a `pop`, **4** from a call's return value, **3** from an absolute `.data` address (`0x5fbed4` twice, `0x5bf514`
once -- and that pointer is not a class table), **3 are a copy of `this`** (`esi<-ecx` with `ecx` never written),
**2** are the stack-slot receiver itself and **1** comes from an argument. So slot 19/20 is dispatched mostly on a
*held* object, and the question this entry carries narrows: the class of that object at run time, with the three
global-pointer sites the only ones where a single shared instance carries the pair.

**2026-11-12, the layout half is measured on a live heap, and `+0x220` is dominated by a class's function pointer.**
Of the two routes this entry named, the RAM-dump one is taken: a boot's `GObjObjects` names, for each of the six
offsets, the class that holds a live object there and the class it holds (`tools/xemu/field-objects.py`,
`inventory/field_object_owners.csv`, 779 rows over 42,128 objects at the title screen). The *displacement* question
is answered and the *property name* question is not: the six offsets resolve to **149 distinct owner classes**
(`+0x2c` 78, `+0x48` 58, `+0x310` 58, `+0x220` 38, `+0x4c` 37, `+0x1c` **0**), so one offset carries different
properties in different classes -- `Shader+0x2c` holds a `Texture` in 107 of 107 objects while `FloatProperty+0x2c`
holds another `FloatProperty` in 2,026 of 4,402. At `+0x220` one population dominates: 539 of 714 sites hold a
`Function`, and 533 of those are a `Class` object (533 of the 1,004 live class objects), with a complete GUI widget
chain behind it (`GUIButton` 10 of 10) and `AnimNotify_Script`→`LinkerLoad` 45 of 63 -- so the eight `[eax+0x220]`
sites' owner is one of **38 candidate classes**, headed by `Class` at eight times the next. Naming *which* of the 38
a given site gets needs the argument's class at that call site, i.e. the callers in `0x335007`-`0x337318` read for
what they pass, not another dump. `+0x1c` is a measured negative: no live object holds a live `UObject` there at the
title screen, so the two `+0x1c` sites read either a non-object dword or one a mission fills in.

**2026-11-12, the field offsets are measured and no table names their owner.** The 28 field origins decode to six
offsets -- `+0x4c` 12, `+0x220` 8, `+0x48` 3, `+0x1c` 2, `+0x2c` 2, `+0x310` 1 -- every one a plain
`mov reg,[base+disp]` with no indexed or absolute load in the set, and they split into two populations: 20 read the
function's own `this` and 8 read `[eax+0x220]` on an object that arrives as an argument, those 8 sitting in eight
small functions whose single direct callers all live in `0x335007`-`0x337318`
(`inventory/vcall_field_owners.csv`, `tools/vcall-field-owner.py`). **Both naming routes answer zero:** a class table
holds virtuals only, and not one of the 23 containing functions is a slot target of any of the 492 tables, nor is any
of their direct callers -- so the class of the held object cannot be read out of the tables at all, which is the
thing this entry's earlier text hoped for ("a call site that dispatches `+0x4c`/`+0x50` through a known object"). What
is left needs a **class layout**: the property stream carries names, kinds and types but no byte offsets, so it cannot
turn `+0x220` into a property name; a `Serialize` override (slot 11, `0x392b0`) that stores its fields at fixed
offsets, or a live object's vtable pointer from a RAM dump (the route that named `StargateHUD+0x398` as `Console`),
would settle it without another boot.

**2026-11-12, corrected by a second decoder: the 106 hits are 55 calls, not 48, and none is unread.** The
paragraph below counted with one narrow decoder, and that decoder was wrong in both directions. It *accepted* 8
displacement bytes, because a `lea`'s own bytes decode as an `add` that lands on the site behind a chain of
wrong-length decodes -- `0x002d113c` is the last byte of `lea ecx,[ebp-0x15c]` -- and it *rejected* 15 real calls,
because a byte it does not know broke the chain in front of them. `tools/vcall-receiver.py` now runs `objdump` over
each site's function start (`oracle`: `agree` / `spans`): **55 of the 106 hits are `call DWORD PTR [reg+0x4c|0x50]`**
and 51 are inside a `lea`'s displacement -- **all 51 in slot 20**, since every slot-19 hit is a call. On the 40 rows
the narrow sweep already read, the oracle picks the same defining instruction in **40 of 40**. All 55 now read from
the function's own start: **53 load the receiver from the object's own first dword** (`mov reg,[reg2]`, 24 of them
through `ecx`, 14 `esi`, 8 `eax`), **2 from a stack local**, **0 unread**, and no site reaches the pair through an
absolute global or a class table. The question this entry carries is unchanged -- which *class* an arbitrary `[obj]`
belongs to at run time, which a stock-disc boot cannot answer because the multiplayer/dedicated path never runs --
and the static half of it is now the object pointer's own provenance (`this`, an argument, a field, a local), which
the same oracle sweep can read next.

**2026-11-12, and the 106 hits are 48 calls: the rest are displacement bytes.** `tools/vcall-receiver.py`
(`inventory/vcall_receiver_sites.csv`) reads the instruction that loads the register each `.text` hit dispatches
through. **58 of the 106 are not instruction boundaries at all** -- at `0x002d0d6e` the `ff` is the last byte of
`lea edx,[ebp-0x418]`'s displacement, followed by `52 50` (`push edx` / `push eax`) -- so the census over-counted
the population by more than a factor of two, and `+0x50` is the worse half (46 of 75, against 12 of 31). Of the 48
real sites, **38 load the receiver with a two-byte `mov reg,[reg2]`** (the object's own first dword, its vtable),
2 from a stack local and 8 unread, so the pair is dispatched as `call [[obj]+0x4c]` on the object's own table and
never through an absolute class table or global. The remaining question is unchanged and is now the only one:
which *class* an arbitrary `[obj]` belongs to at run time -- which a stock-disc boot cannot answer, because the
multiplayer/dedicated path never runs.

**2026-11-12, the helper's table is 21 slots, not 26, and the boundary is a terminator.** `tools/native-vtable.py`
(`inventory/native_vtable_census.csv`) re-reads the table the `.data` singleton's `+0x220` helper installs and finds
**26 code pointers spanning two classes**: `0x53f770` with **21 slots** and `0x53f7c4` with **5**, split because
`0x53f7c4` carries its own install store (`mov [eax],0x53f7c4` at `0x3313ce`, inside slot 0's factory for a `0x14`-byte
adapter). The over-read was not an address error -- every one of the 26 is a valid function start -- it was a missing
rule: a table cannot contain another table's start. On the boundary rule the previous note asked for, a slot qualifies
as `map` (an exact start in `symbols/xbox_functions.csv`) 6 of 21, as `call` (an `E8` target) **0 of 21**, and as
`tail` (the byte before completes `ret`/`ret imm16`/`int3`/`jmp rel`/`jmp [reg]`) **21 of 21**; the sibling scores
2/0/5. So the cheapest naming-free rule is the terminator, and "requires a known function start" would have named under
a third of the table. The bodies also place the pair: slots 17/18 (`0x33122a`, `0x33124e`) call `[eax+0x4c]` on the
object held at `+0x1c`, and slot 20 (`0x33049a`) calls `[eax+0x44]`/`[eax+0x48]` on that same object -- two of the 55
`+0x4c` sites U8 counts are these calls (`0x331247`, `0x331279`) -- while **no body in the table dispatches `+0x50`**.
What stays open is unchanged and is not a coverage problem: no body references a string (0 of 26, so no `__FILE__`
assert), none is a direct call target, and the module's name still needs a signature or an asserting caller.


**2026-11-12, and the merge is general: 109 of the image's 1,007 code-pointer runs hold two tables.** `tools/native-vtable.py
--block` (`inventory/native_vtable_merges.csv`) splits every run of three or more code pointers in `.rdata` at its install
stores: **704 table starts** over **1,007 runs**, **468 runs begin with a table**, and **109 runs hold two or more** (re-measured after the store-form fix below: **724** starts, **476** begin with a table, **114** hold two or more)
(`0x4c7958` six, `0x4c9348` five, `0x4ca618` 797 dwords over six). So the `0x53f770` over-read that this entry
corrected is the normal shape of a packed block, not an anomaly: a slot count read from the next run boundary is wrong
for 109 runs, while the install store is exact for all 704. The UObject class tables' starts are unaffected (they were
named from install literals); their slot counts are read from the next *start*, which is the rule this census shows is
the safe one.


**2026-11-12, no `.text` site names a table, and the store rule was three addressing forms short.**
`tools/native-vtable.py --reach` (`inventory/native_vtable_reach.csv`) classifies every `.text` instruction that
mentions one of the install-started tables: **722 of 724 are mentioned by their install store and nothing else**, **0**
are dispatched through a register base (`mov reg,table` then `call [reg+disp]`), **1** is called absolutely
(`0x00564dbc`, slot 0, from `0x00385936` and `0x0038b29a`), and **1** is loaded into `ebx` as the start of a
`{start, end, constant}` data descriptor inside `Core_UnObjBas` (`0x4d0d80`). So a vtable in this image is only ever
reached through an object pointer, and no call site can supply a name to *any* table, mapped or not -- the "name from a
caller" route is closed for all 724, not only for the native ones. What remains open is the naming question itself, now
with one measured lever: **48 of the 232** unmapped tables are installed by a body that also installs a mapped class's
table (36 of those bodies named `ctor_*`/`dtor_*`, 32 with two slots or fewer, i.e. secondary base tables), while **184**
have no mapped neighbour. The same pass corrected the census it reads: `immediate_store` never accepted
`mov [ebp+disp8],imm32` or `mov [esp+disp8],imm32`, so the table count was 20 short -- **724 starts, 476 runs beginning
with a table, 114 runs holding two or more**, against 704 / 468 / 109 -- and the two independent scans now agree on all
1,503 stores. The tables are also **not related to the class tables at all**: `tools/native-vtable.py --share` compares every
unmapped table's slot bodies against all 492 class tables and finds **0 of 232** with three bodies at the same index
and **0** with a three-slot layout prefix once bodies under 16 bytes are masked -- unmasked, the only "matches" are
the one-byte `ret` stub 327 class tables hold at slots 19/20 -- so a native vtable carries no mapped class's virtual
function and the naming problem is not a lost hierarchy.

## U9. Phase 4.2's signature libraries cannot be carried by this repository

**Observed.** 12,347 of 14,280 functions are still `FUN_*`. 4.2 asks for MSVC 7.1 / Xbox SDK signature
libraries (xbox-includes, FLIRT-style signatures) to name the CRT, XAPI, D3D8, XNet, XOnline, zlib and
math routines. Those databases are built from Microsoft's SDK libraries and toolchain output, which
`AGENTS.md` forbids committing, and an ordinal-to-name table for `xboxkrnl.exe` is the same kind of
artefact.

**Consequence.** The part of 4.2 that can be done from the image alone is done: the XBE's own library
table names the statically linked libraries and their versions (XAPILIB 1.0.5849, D3D8, D3DX8,
XONLINE, XGRAPHC, DSOUND, XBOXKRNL, XVOICE, VOICEMAIL, HLIBCP, LIBC). What is missing is the
function-level signature data.

**What would settle it.** A signature database supplied locally by whoever runs the analysis (kept
outside the repository, like the 2006 source), with `tools/versions.lock` recording which database was
used and `docs/NATIVE.md` reporting the measured effect. Until then 4.2 stays open with the reason
written down rather than a guess at names.

**Measured 2026-11-10, the static half is closed.** `tools/library-bodies.py` reads every body in the
seven library sections in place (`docs/LIBRARIES.md`, `inventory/library_bodies.csv`): 3,109 bodies /
565,157 bytes, **0** of which reference a build-tree source path, 169 reference any string at all, and the
sections' entire vocabulary is 363 strings. RTTI carries four descriptors (all CRT exception classes), so
the class-level route is closed as well. The engine-module method -- name the translation unit from the
body's own assert -- has nothing to read here, and the sections' 21 `thunk_` names plus one mangled C++ name
are the only names in the population. The engine corpus does no better from its own text:
`tools/body-name.py` (`docs/NAMING.md`) names **27** of its 4,625 unnamed bodies from a `Class::Method`
literal and 0 from a byte-identical twin, so the string-based route is exhausted on both sides of the
image. That leaves this entry's unblock as written: a locally supplied signature database, scored against
the section table in `docs/LIBRARIES.md`.

**Measured 2026-11-11, both halves narrowed (`tools/class-table-residual.py`, `make class-table-residual`).**
The registration-push route is read for all 493 sites: every one calls `0x0003af60` and pushes the class's **name and
outer strings**, a constant flags word `0x04084004`, the constructor and a size -- and **none pushes a class table**,
so the table is installed by the constructor and the 26 names are not on the site at all. The string decode agrees
with `registration_sites.csv`'s independent `literals` column on 493 of 493 rows. Slots 19 and 20 are **one pair**,
not many: `0x00020f00` twice in 327 tables and `0x000c4f60`/`0x000c6d00` in 165, and all 165 are under `Actor` with
`Actor` itself carrying the pair, so those two virtuals are installed once at `AActor`; all 26 unnamed tables are in
the 327. The dispatch-site route is counted (82 `call [reg+0x4c]`/`[reg+0x50]` sites, mixed vtables) and needs a
typed receiver first, so the next instrument is a breakpoint on the two bodies in a booted guest, not another
`.text` scan. `docs/NATIVE_CLASS_TREE.md` carries the numbers.

**Status.** Open, blocked on data the project may not ship.

## U10. The tagged property record's field order — closed 2026-10-24

**Observed.** A `.sgs` checkpoint is a UE2 package whose exports are the level's actors
(`docs/formats/sgs-checkpoint.md`), which means its properties are serialised as tagged records —
a name, a type, a size and the value — not as a positional dump. The walk at `0x0003bdc0` selects
that stream with a flag (`[archive+0x3c] == 0` recurses into nested structs, the binary path only
does so for a property flagged `0x80000`), resolves the tag name against the struct
(`"Property %s of %s not found"`, `__wcsicmp`) and calls each property's serializer through vtable
`+0x94`. `0x0003ceb0` reads a tag name and `0x0003cd70` writes one (`docs/NATIVE_PROPERTIES.md`).

**Why it matters.** PLAN 6.2/6.4 rebuild packages and checkpoints; reproducing a tagged stream needs
the record's field order, and 5.4's schema (`inventory/sgs_schema.csv`, 1,739 tags) is only
describable at the level of names until then.

**What would settle it.** Read `0x0003bdc0`'s two branches to exhaustion and pair every store to the
stream with the read that consumes it, or dump a checkpoint's first 64 bytes at a known tag and map
the fields onto the reader's offsets. The type names are already indexed
(`inventory/property_types.csv`), so the type field can be checked by value, not by string.

**Resolved 2026-10-24.** `0x0003ceb0` is the tag reader and its body gives the order outright
(`docs/NATIVE_PROPERTIES.md` §6): int32 **Name** (index 0 ends the list), one byte **Type** whose low
nibble is the property type, bits 4–6 the size class and bit 7 "array index follows", an int32
**StructName** only for a struct property, a **Size** that is either implied by the size class
(1/2/4/12/16) or read as 1/2/4 bytes, then a compact **ArrayIndex** when bit 7 is set and the type is
not bool, then the value. The type nibble is the reserved name index of the property type (1..16), and
each property class's `vtable+0xcc` returns exactly that byte, so the tag and the class agree by
construction. UELib's `DeserializeTagUE1` — the parser that reads all 9 checkpoints and 19,080 tags
of this build with no failures — implements the same masks (`0x0F`/`0x70`/`0x80`) and the same order
independently.

**Writer measured 2026-10-24.** `0x0003cd70` fills the same record in the same order — name index at
`+4`, struct name at `+8` for a struct property, size at `+0xc`, array index at `+0x10`, type byte that
is OR-ed with the size class (1/2/4/12/16 implied, `<0x100` one byte, `<=0x10000` two, else four) and
with `0x80` when the array index is non-zero or a bool property's value is true. It also shows the
**size is a measured serialized length**, produced by running the value through a scratch archive —
which is why the reader can report a size mismatch. `docs/NATIVE_PROPERTIES.md` §6.

**One residual:** the routine that writes the assembled tag struct to the archive byte by byte was not
followed, so the on-disk *caller* of the writer is open for 6.2 even though field order, packing and
size thresholds are measured.

## U11. Which D3D8 device caps the renderer asks for

**Observed.** PLAN 2.6 also asks which D3D8 device caps this build uses. There is no import table to
read them from (`docs/IMPORTS.md`): the device is created inside the statically linked `D3D` section
(302 functions, none named), so the caps are arguments at run time, not values in a table.

**Why it matters.** The renderer-side natives (PLAN 4.8) behave the way they do because of the caps the
device was created with; knowing them also says which Xbox features the fork relies on.

**Measured 2026-11-10, static half.** The create path is now located in `D3DDrv`: `0x002b27f0` (2,513 bytes)
logs the three decisions it takes -- back-buffer format, depth-buffer format, and a vertex-processing mode
chosen among `hardware`/`software`/`mixed` after a `D3DVTXPCAPS_MATERIALSOURCE7` test -- and its siblings
enumerate display modes and check depth-stencil compatibility. So *which capability the code tests* is
readable (`docs/systems/RENDERER.md`); the cap bits it passes to `CreateDevice` are still arguments at run
time, and this entry stays open.

**Measured 2026-11-10, runtime half -- the entry is answered.** `tools/xemu/bp-args.py` (`Z0` breakpoints
over the GDB stub) stops at the log sites and at the create call itself. The call is `0x002b2dcd` into the
statically linked D3D8 at `0x44f040`, six pushes at `0x2b2dc5..0x2b2dcc`: adapter `[esi+0x46d8]`, device type
**1** (HAL, a literal push), the focus window from the render interface's slot `0xa8`, `BehaviorFlags` **0x40**
(hardware vertex processing, again a literal push), `pPresentationParameters` = `esi+0x4708` (`lea ebx` then
`push ebx`), and a sixth argument `[esi+0x4704]` that is zero at the call.

The block, read out of the guest at the `Creating device` log (`0x2b2da3`), is thirteen dwords in the SDK's
`D3DPRESENT_PARAMETERS` order: width **640**, height **480**, back-buffer format **30**, back-buffer count **1**,
multisample **0x11**, swap effect **1**, device window 0 (the handle is passed separately), windowed **0**,
auto depth-stencil **1**, depth format **42**, `Flags` **0x121**, refresh **0**, presentation interval **1**.
Three independent readings agree, because the same function logs each decision and the arguments are on the
stack at its `GLog` call: `Best-match display mode: 640x480x32@60` (`0x2b2b29`), `Using back-buffer format
30(32-bit)` (`0x2b2cda`), `Using depth-buffer format 42(32-bit)` (`0x2b2d48`) and `Enter SetRes: 640x480
Fullscreen 1` (`0x2b285b`). So the renderer asks for a **fullscreen 640x480 HAL device with hardware vertex
processing, a 32-bit back buffer and a 32-bit depth-stencil buffer**; 30 and 42 are this build's `D3DFORMAT`
values, not SDK names.

**Measured 2026-11-10, the reported half -- the entry is closed.** What the device *reports* is not a call to
stop on but a struct the render device keeps: a **53-dword `D3DCAPS8` at `this+0x419c`**, already populated when
the post-create capability test at `0x002b3008` (`test BYTE PTR [esi+0x4238],0x2`) reads
`VertexProcessingCaps` (`0xbb`: TEXGEN, MATERIALSOURCE7, DIRECTIONALLIGHTS, POSITIONALLIGHTS, LOCALVIEWER, one
unnamed `0x80` bit). Every field boundary lands on the SDK's, and the runtime writes its own driver name
`"Xbox driver"` at `+0xd4`. The numbers that matter downstream: 4 blend stages and 4 simultaneous textures, 8
active lights, 4096x4096 textures, `MaxVertexIndex` 0xffff (16-bit indices), and **96** vertex-shader constants
with `vs`/`ps` version words `0xffff0101`. Because MATERIALSOURCE7 is set, the `hardware` decision and the
requested `BehaviorFlags` 0x40 agree, and the `D3DVTXPCAPS_MATERIALSOURCE7` fall-through is dead code on this
hardware. The table is in `docs/systems/RENDERER.md` ("What the device reports"), and `tools/xemu/d3d-caps.py`
decodes the window out of a `bp-args.py` log (`--check` needs neither dump nor log).

**Measured 2026-11-10, the fill site.** The struct is filled by `GetDeviceCaps(Adapter=0, DeviceType=1 (HAL),
pCaps=this+0x419c)` at `0x002b1b8a`, into the statically linked D3D8 at `0x004523a0`; the `lea ecx,[ebx+0x419c]`
at `0x002b1b76` is the only instruction in an executable section naming that offset, and the same body stores
adapter 0 into `this+0x46d8`, the field the later `CreateDevice` passes as its adapter. The body then logs six of
the caps by name -- `MaxTextureBlendStages`, `MaxSimultaneousTextures`, `MaxActiveLights`, `MaxPrimitiveCount`,
`MaxVertexIndex`, `MaxStreams` -- from `this+0x4230/+0x4234/+0x423c/+0x4250/+0x4254/+0x4258`, which are
`caps+0x94/+0x98/+0xa0/+0xb4/+0xb8/+0xbc`. The field map therefore rests on the game's own names, not only on
the SDK's order. `tools/xemu/d3d-caps.py --fill-site` re-derives the call from the image.

**Still unnamed, and why.** The two `D3DFORMAT` enumerants (30 back buffer, 42 depth) and the `MultiSampleType`
value `0x11` stay numbers: naming them needs the Xbox D3D8 header, which neither the dump nor this repository
carries, so a name would be a guess. That is the only question left in this entry.

**Status.** Closed for both halves (request and report); `tools/xemu/bp-args.py` and
`tools/xemu/d3d-caps.py` are the instruments, and the three stub behaviours `bp-args.py` works around are in
`docs/XEMU.md`.

## U12. ~~The XBE entry point does not decode from the header~~ — resolved

**Was.** The header field at `0x128` reads `0x94B66E5C`. The documented retail key (`0xA8FC57AB`) decrypts it to
`0x3C4A39F7`, outside every section, so the entry looked unreadable. The earlier reader also mistook `0x118` (the
certificate address, `0x10184`) for the entry.

**Resolved.** The field is XOR-encrypted with the **debug-build** key `0x94859D4B`, giving **`0x0033F317`**, which
is inside `.text`. Three independent checks agree: the five bytes before the address are another function's
`xor eax,eax; ret 4` tail, so it is a function start; the body reads the XBE header back (`[0x10118]`,
`[0x10108]`), aligns the heap to 16 bytes and registers a static initialiser, which is what a CRT entry does; and
the retail key produces nothing mapped. `Xbe.entry`/`Xbe.entry_key` now carry it and `tools/xbe.py … sections`
prints it.

**Second half, also resolved.** Ghidra *was* told an entry, just the wrong one: `run-analysis.sh` passed
`0x10184` (the certificate) to both scripts. With the real entry passed, the analysis creates `XbeEntry` at
`0x0033F317` (114 bytes) and the function map goes 14,956 → 14,957 with nothing removed and no name changed.

## U13. Where a function's bytecode lives in its export (PLAN 3.7)

**Observed.** PLAN 3.7 needs a disassembler, and the reader is blocked on the *serialised* layout of a `UFunction`,
not on the opcode table: `tools/uelib` (pinned) maps opcode → token class in `src/Branch/DefaultEngineBranch.cs` and
gives every operand width in `src/Core/Tokens/*.cs`, so the decoding side is specified. What is missing is where the
script bytes start and how long they are inside the function's export payload.

**Two negatives, both measured, both worth keeping.**

1. **UELib's own reader does not transfer.** Its `UStruct.Deserialize` reads `Super`, `ScriptText`, `Children`, an
   optional `StructFlags`, `Line`, `TextPos`, `ByteScriptSize`, then the script — but parsing our 3,931 `Engine.u`
   function payloads that way validates for **1 of them** (identifier widths and the trailing function header were
   tried in both compact and `int32` forms; none fit).
2. **The content side anchors but does not locate.** Trivial bodies are unambiguous: `return true;` compiles to
   `EX_Return` + `EX_True` (`04 27`) and that pair sits exactly **12 bytes before the payload end in 424 functions**
   (`return false;` in 463). So the trailing 12 bytes are the function header and the script ends there — but no
   consistent size field precedes it: a dword at offset *k* with value `payload_end - 12 - (k+4)` exists in only
   **11 of 1,224** candidates.

**What is known.** The runtime side (`docs/NATIVE_VM.md`) reads `Function->Script` at object offset `+0x4c`, and the
interpreter treats `EX_Return` as the terminator, so the loader must fill that field from somewhere in the payload.

**What would settle it.** Decompile `UStruct::Serialize` in `default.xbe` and read the field order: it is the function
that writes `ScriptStorageSize` and the script bytes, and it is reachable through the class tables the project has
already named (`inventory/xbe_class_tables.csv`). Second route, cheaper if it works: find the *store* to `[reg+0x4c]`
in the loader with a Ghidra script and read backwards from it.

**Third session, 2026-10-26: the trailer is identified, the size field is not.** `docs/BYTECODE.md` lands the opcode
table, and its U13 probe (`python3 tools/uscript-dis.py --payloads System/Engine.u`, counts only, no payload bytes)
narrows this entry. Over `Engine.u`'s 3,842 `Function` exports with a payload of 16 bytes or more:

* **`FunctionFlags` is the dword at `payload_end-4`**: 439 of the 444 exports that join to `inventory/natives.csv` by
  (class, function) carry exactly that file's `function_flags`, so the last four payload bytes are flags and not
  script.
* **the script ends 7 bytes before the payload end** in most payloads: `04 0b 47` (`Return`, `Nothing`,
  `EndOfScript`) sits at `payload_end-10..end-8` in **3,197 of 3,842**. So the payload is
  `[header][script][3 bytes][flags]`, and only the header's field order and the script's length encoding are missing.
* **a size field in front of the script is not a plain int32 there**: a dword whose value equals the remaining length
  exists in **937** of the 3,842, and 518 of those are the 3-byte epilogue-only body; for a longer script no such
  dword is at that position.
* **and the walk cannot find the start instead**: requiring a token walk from a candidate offset that lands exactly on
  `payload_end-7` leaves **3,623 of 3,826** payloads with more than one candidate start (a scratch probe, not carried
  by the committed tool), so a self-delimiting walk is not a locator either.

**Fourth session, 2026-10-26: the tail is solved, the header's widths are not.** `UFunction::Serialize`
(`0x00039f10`) settles what the trailing bytes are, and its version gates explain why earlier readings of this
payload went wrong. After `UStruct::Serialize` it writes, in order, a `u16` (`+0x84`, the native token), a `u8`
(`+0x88`, operator precedence), the `u32` `FunctionFlags` (`+0x80`) and — **only when `FunctionFlags & 0x40` (Net)** —
a `u16` `RepOffset` (`+0x86`). The three fields a `UFunction` otherwise carries (`paramsSize`, `paramsCount`,
`returnValueOffset`) sit behind `if (Arch.Version < 0x40)` gates and this build is **version 130**, so they are
**not in the payload at all**; the body recomputes them at load by walking the `Children` chain. UELib's reader
applies the same gate (`PackageObjectLegacyVersion.Release64 = 64`), so for this build its tail order is
`NativeToken`, `OperPrec`, `FunctionFlags` — 7 bytes, or 9 with `RepOffset`.

Measured on `Engine.u`'s 3,842 `Function` payloads (`python3 tools/uscript-dis.py --payloads System/Engine.u`):

* the epilogue `04 0b 47` (Return, Nothing, and `47` = the `'G'` marker `UStruct::Serialize` appends to an
  uncompressed script) sits at `payload_end-10..end-8` in **3,197** payloads, and the `Net` flag is set in **0** of
  them;
* at `payload_end-12..end-10` in **201**, and in **all 201** the `Net` flag is set — the two extra bytes are the
  `RepOffset` the body writes. The trailer's length is therefore *predicted by a flag* with no exceptions in 3,398;
* **444** payloads carry it at neither position, which is the residue this entry now starts from;
* the script's size is a 4-byte field immediately in front of it in **804** payloads, and in exactly those the
  opcode table walks to the boundary. Those scripts are short (2 to ~30 bytes), so the evidence proves the layout
  rather than the table.

**Fifth session, 2026-10-28: the tail's fields are confirmed by content, and the header width is now the only
question left.** `tools/uscript-dis.py --payloads` takes the whole `System` directory (12,472 `Function` payloads,
797 joinable to `inventory/natives.csv`) and compares the `u16` at `payload_end-7` — `payload_end-9` when `Net` —
with the index the function's *script* declares in `native(1234)`:

* **791 of 797** payloads carry exactly the declared `NativeToken`; 5 carry a token where the script declares none
  and 1 the other way, **0 disagree in both**; `FunctionFlags` matches in **790 of 797**. The field order written
  down from `UFunction::Serialize` is therefore checked by a value that appears in two unrelated places, not by
  position alone.
* the epilogue counts over the corpus are **11,379** at 7 bytes, **337** at 9 (all Net) and **756** at neither —
  that last number is the scriptless native half, which is why it sits just under `natives.csv`'s 797.
* **the size field's position is still not derivable**, and two hypotheses have now been tested against each other
  over the 2,166 payloads where a *unique* dword equals the distance to the epilogue: `UStruct::Serialize`
  (`0x000392b0`) writes `Super`, a `Children` chain, `FriendlyName`, `StructFlags` (version > 119), `Line`,
  `TextPos`, `ScriptStorageSize`, then the script; a header of *N* compact indexes plus 16 fixed bytes fits 596
  payloads at two leading references, 22 at three and 618 at four — i.e. no fixed count, so the variable part is
  most likely the `Children` chain (one reference per parameter). **Next test, not tried:** take the parameter
  count from the export table (exports whose `outer` is the function and whose class name ends in `Property`),
  predict the start as `Super` + one reference per child + `FriendlyName` + 16, and require the walk from the
  predicted offset to end exactly on the epilogue.

  **That test has now been run, and the children-chain prediction does not hold.** Over the same 2,171 payloads, the
  prediction `Super` + one reference per child + `FriendlyName` + 16 is exact for **181**, and off by +1/+2/+3 for
  722/639/258 — adding one leading name index raises the exact count to **904** and leaves residuals spread either
  way, which is what a wrong rule looks like rather than a missing byte. Using the walker as the locator is no help
  either: walking from the predicted offset, and from ±3 around it, succeeds for 3,030 payloads at the predicted
  offset against 3,545 at **+2**, i.e. no peak where the rule says the start is — a wrong start still lands on a
  boundary in a quarter of payloads because most scripts are short. So the header width is neither a fixed count of
  references nor the `Children` chain alone, and `uscript-dis` keeps refusing rather than guessing. The two routes
  left are the ones this entry started with: `UStruct::Deserialize` in the image (the reader, not the writer), or a
  payload whose script's first token is unambiguous in the *source* — a function whose `.uc` body begins with a
  distinctive literal — used to pin one start and work outward.

**What is left is the header.** It serialises the struct's `Next`, `Super` and `Children` object references and its
`FriendlyName`, then a `u32` (written when `Version > 119`), then `Line` and `TextPos`, then the size — but the
four indices are archive-encoded and their widths vary per payload (the 804 size fields land at offsets 19..24).
For the other 3,038 payloads no dword at any of the first 48 offsets equals the script's length, so a size field
found by value is not the general rule. **Do not run the walk backwards from the tail to find the start**: a walk
started anywhere near the header walks hundreds of bytes before desynchronising, which is a property of a wrong
start rather than of the table (and 3,623 of 3,826 payloads have more than one start that reaches the boundary).

**Sixth session, 2026-10-29: the header's encoded part is pinned and the `Children`-chain rule is dead.**
`tools/uscript-dis.py --header` decodes the payload's leading values with the archive's own compact index and
requires the **sixth** to be the export's `FriendlyName` name index: that aligns **12,360** payloads, and it is
**11,687 of the 11,716** that carry the epilogue (99.75%) — so the header's last field is identified by content, the
same way the tail's was. The count of values in front of it is **constant at five** for every payload, whatever the
parameter count, which is what the previous session's test got wrong: `Super` + one reference per parameter is not
the rule, and the earlier +1/+2/+3 residuals were the encoded *widths*, not a missing reference. Resolved against the
package tables, the five are `v1` = `Super` (import 3,226, same-named function in a parent class 2,406, zero 6,728),
`v2` = `Next` (another function of the same class 10,529), `v4` = `Children` (a child of this function 9,223), and
`v0`/`v3` always zero.

**What that leaves is the fixed run after the name.** With the writer body's three `u32`s plus the size — 16 bytes
past `FriendlyName` — the size dword equals the remaining length in **672** payloads and the walk from there reaches
the epilogue in **1,975**; the rest fail at that start in the walker's bad-token case (3,824) or on an unterminated
parameter list (1,617), and the failures concentrate in the long scripts, so the 1,975 that land say nothing about
them. The two explanations still standing are therefore (a) a field
in the fixed run that the reading of `UStruct::Serialize` misses, and (b) the opcode table's 35 unnamed opcodes
being what stops the walk. **The test that separates them is a script whose first token the shipped `ScriptText`
fixes** — a body that begins with a literal the table classifies unambiguously — and it is now the cheapest route,
because everything needed is in the packages.

**Seventh session, 2026-10-29: the serializer's call sites are measured, and the loader is a vtable slot.**
`tools/ghidra/ghidra_scripts/Callers.java` exports every function that references an address, so the load side can be
looked for from the reader rather than guessed. `UStruct::Serialize` (`0x000392b0`) has **exactly two direct call
sites** — `0x000394c9` inside `0x000394c0`, and `0x00039f1a` inside `UFunction::Serialize` (`0x00039f10`), which is
the order `docs/BYTECODE.md` documents — and `UFunction::Serialize` itself has **none**, because it is dispatched
through a class table. So **the linker never calls the struct serializer directly**: the payload's header is read by
whatever `Object->Serialize(Ar)` virtual call the export loader makes, and a search for a call to `0x000392b0` from
linker code cannot succeed. The route that remains is the vtable slot, which the project already has named for 492
class tables (`inventory/xbe_class_tables.csv`).

**And the header decode does not line up with what that serializer writes.** Decoding the payload from offset 0 puts
`FriendlyName` sixth in **7,104 of 7,115** `Function` payloads in `Engine.u` and `StargateGame.u`, but decoding from
offset 2 puts it third in 4,629, fourth in 2,121 and fifth in 354 — a variable count that **does not track the export
table's child count** (three values with 0 children: 899 payloads, with 1: 1,472, with 4: 321). So the variable part of
the header is not the parameter list, and the leading values are not simply `Super` + `Children` as the body reads.
Two readings survive: a fixed prefix of linker bytes in front of the struct's own output, or a value whose decoded
width differs from its stored width. Neither is measured yet, and the fixed run after the name stays open on top of it.

**Eighth session, 2026-10-29: the header is a fixed four values for a class and five for a function, and classes are
the control.** A class runs the same `UStruct::Serialize` and carries no `UFunction` tail, so it separates "a field per
child" from "a fixed header" without any reading of the body: a class's children range from 1 (`Time`, `Locale`) to 618
(`Actor`) and **every one of the 2,316 aligned classes holds four values** in front of `FriendlyName`; every one of the
12,355 aligned functions holds five. The `Children`-chain model is therefore dead as a width rule, and the residuals
that killed the earlier per-parameter prediction were compact-encoding widths.

What the five values are, over the whole corpus: `v0` always zero, `v1` = `Super` (3,226 imports, 2,406 same-named
functions in a parent class), `v2` = `Next` (another function of the same outer, 10,529), `v3` always zero, `v4` =
`Children` (9,218). For a class the order shifts: `[Super][0][Children][…]` (2,317 of 2,318 resolve `v2` to a child).

The fixed run after the name is the last piece, and the class control cannot reach it: **no class payload carries the
`04 0b 47` epilogue at all (2,319 of 2,319)**, so a class's script is not stored the way a function's is and there is no
end to measure the run against. On functions the dword at `name_end+16` equals the distance to the epilogue in **2,141**
payloads and the walk from there reaches it in 1,975; the ones that miss fail on the walker's refusals spread across
the opcode range, so the start is probably still wrong for them and the run's length is not settled.

**Ninth session, 2026-10-29: the locator is solved — the script starts 16 bytes past `FriendlyName`.** Three
independent readings agree, and `python3 tools/uscript-dis.py --start System` reproduces all of them:

* the **floor**: an empty body still pays the epilogue and the trailer, so the smallest payload after `FriendlyName` is
  the run plus 3 plus 7 (9 when `Net`). Measured: **25** is the floor over 12,360 payloads, with spikes at **26**
  (1,380) and **28** (801) = `16+3+7` and `16+3+9`. A 12-byte run would floor at 22, where nothing appears.
* the **walk depth**: from `name_end + 16` the decoder gets 16–63 bytes in 3,494 payloads, 64–255 in 1,486, past 256
  in 86, and all the way to the epilogue in 3,328. A wrong start desynchronises within a token or two — that is 979.
* the **dword at +12** is not the script's length: larger than the physical script in 9,298 payloads, equal in 2,052,
  never smaller. So the fourth word of the run is another field, and no size field is required: `SerialSize` already
  fixes the script's end.

That closes 3.7's *locator*: the start is computed from the header, not searched for. What is left is the opcode
table's coverage (the 979 early refusals plus the ~5,000 later ones are unnamed or mis-patterned opcodes) and the
identity of the run's four words.

**Tenth session, 2026-10-29: the disassembler runs on shipped functions, and the walk refusals have no single cause.**
`--script` disassembles a function from `name_end + 16` to the epilogue, and `--usage` runs the corpus: **3,444 of
11,687** scripted payloads walk clean end to end, and their opcode census (`inventory/opcode_usage.csv`, 153 opcodes)
is now the order the per-opcode work should be done in. The refusals are spread across the table — `VirtualFunction`
708, `BadToken` 690, `FinalFunction` 424, `LabelTable` 415, `0x77` 271, `0x00` 186 — and **a single-opcode fix does not
work**: giving `0x03` each operand width from 0 to 6, or an expression, completes at most **75 of its 690** payloads.
That is the signature of a desynchronisation earlier in the stream, which makes the walk refusals a per-opcode
investigation with the usage census as its guide, not a bug to be patched in one place.

**Eleventh session, 2026-10-29: five operand widths were four fixed bytes and are a compact index — 99.3% of scripts
now walk.** The walker's handlers for groups 0, 13, 14, 15 and 19 read an `int`, and `tools/opcode-table.py` recorded
that as `bytes:4`. Held one group at a time against the corpus, with the start now known, the payloads say the operand
is the archive's **compact index** (bit 7 sign, bit 6 continuation, six bits then seven per byte):

| groups as `bytes:4` (shipped) | groups 0, 14, 15 as `index` | + group 13 | + group 19 |
|---:|---:|---:|---:|
| 3,328 of 11,350 walk | 8,869 | 10,040 | **11,269** |

`--usage` over the whole corpus agrees: **11,602 of 11,687** scripted payloads walk clean, against 3,444 before, and
`--start`'s depth histogram moves with it (11,269 to the epilogue, 81 refusing at all). The table now regenerates with
`index` for those groups and `inventory/opcode_usage.csv` is the census of the new set. This is the per-opcode work
3.7 was waiting on, and it was one rule — not the 690-payload `BadToken` case, which a width change still rescues at
most 75 of.

**Twelfth session, 2026-10-29: the index operands resolve, and the encoding indexes two tables.** With 99.3% of
scripts walking, `--resolve` reads every index operand and asks the package what it lands on. The unambiguous tokens
agree perfectly — `FinalFunction` **5,424 of 5,424** resolve to a `Function`, `DynamicCast` **4,153 of 4,153** and
`MetaClassCast` **129 of 129** to a `Class`, every variable token to a property — which is a semantic check on the
operand rules that no walk can give, because a wrong width can still tile. The virtual and global call tokens do not
name objects at all: **10,405 of 13,398** `VirtualFunction` operands and **109 of 109** `GlobalFunction` operands name
a function the package declares, i.e. they index the **name table**, and `NameConst` does the same. So the same compact
codec indexes exports/imports on one token and names on another, and only the opcode distinguishes them.

**Thirteenth session, 2026-10-29: the corpus's call sites are a table.** `--calls` records every call in the 11,602
scripts that walk: **5,424 static** (`FinalFunction`, an object reference — **all 5,424 resolve to a `Function`**) and
**13,507 dynamic** (`VirtualFunction`, `GlobalFunction`, a name), of which **10,514** name a function the package itself
declares. `inventory/script_calls.csv` carries package, class, function, token, dispatch, target and target class. The
2,993 dynamic sites whose name is not declared locally are not a defect to fix but a fact to carry: the name table is
per package, so a call into a native parent's function is indistinguishable from a call into a sibling package's until
the hierarchy is consulted.

**2026-11-11: the hierarchy is consulted, and a quarter of the dynamic calls land above the caller.**
`--calls` now resolves a by-name target along the caller's super chain, in the package that declares each ancestor
(`inventory/classes.csv`'s `package_of_super`), and writes the declaring class to a `resolved_in` column: of the
13,507 dynamic sites, **9,628 (71%) name a function declared on the chain -- 5,136 on the caller's own class and
2,246 on an ancestor**, 305 `Engine.Actor`, 244 `Engine.Pawn`, 187 `Editor.BrushBuilder`, 135 `Engine.Interaction`.
So the "2,993 not declared locally" are two different things: 2,246 are a parent's function, which the hierarchy
does place, and **6,125 remain unresolvable from the site by construction** -- a virtual call resolves on the
receiver's class and the site names no receiver (`SetText` 156, `SetFont` 139, `ShouldCrouch` 63). A rebuild has to
carry the ancestor's declaration for the first group; the second needs a receiver, not a better name table.

**Later the same day: the second group splits, and the first group's number was wrong.** Counting that 9,628 against
its own components gives 5,136 + 2,246 = 7,382, and 13,507 - 6,125 is 7,382 as well, so the chain places **7,382
(55%)**, not 9,628 -- a correction to the entry above. The 6,125 the chain cannot place are not names the build does
not have: `--calls` now also indexes every `Function` export of the 18 packages by name (a state's functions counted
against the class that owns the state), and **2,712 site(s) across 1,080 names reach a class that is the only one in
the corpus declaring that name** (`ShouldCrouch` -> `Engine.Pawn` 63, `AddMenuItem` -> `UWindow.UWindowPulldownMenu`
58, `SetPosImmediate` -> `StargateGame.UIWidget` 51), so the call can reach exactly one implementation whatever the
receiver is. The remaining **3,413 sites over 736 names** are declared by two or more classes -- `SetText` 156 over
5, `SetFont` 139 over 4, `GetValue` 56 over 10, `WhatToDoNext` 52 over 16 -- and placing those needs the receiver's
**static type**, which the bytecode does not carry beside the call. So U13's call table is now resolved to 74% of
its dynamic sites (7,382 + 2,712 of 13,507) rather than 55%, and the last quarter is a receiver-typing problem, not
a name-table one.

**2026-11-11, the receiver measurement: the site does carry its receiver, and it is the expression in front of the
call.** The counts in the three paragraphs above are superseded -- a site inside a `State` was attributed to the
state rather than to the class that owns it, which alone placed **869 more sites on the chain** (7,382 -> **8,251**)
and cut the sites with nothing at all in front of them from 145 to **17**; a self-call always has a declarer on its
chain, so that collapse is the check on the attribution. A by-name call carries no receiver operand because the
compiler has already emitted the object: the **expression immediately in front of the call token** is what the VM
dispatches on. `--calls` now reads it, types it where its token can name a class (a property's declared type from
`inventory/class_properties.csv`, a cast's target, a `Context`'s inner expression) and places the call on the
**receiver's** chain, `receiver:Pkg.Class` in `resolved_in`, with the token and the type in two new columns. **1,228
sites (9%) over 419 names** are placed that way, 198 distinct receiver classes (`Pawn` 80, `UIWidget` 76,
`SquadAIBasic` 65, `Controller` 51, `UWindowComboControl` 50): `Engine.Touch ClientMessage()` has a `Pawn` in front
and lands on `Engine.Pawn`. The corpus-only lookup consequently shrinks to **2,332 (945 names)** and the residue to
**1,696 sites over 412 names**, which is now a *typing* gap: **1,207** have an object expression whose type the corpus
does not record (**628 a local variable** -- a local's type is not a class property, so it is not in
`class_properties.csv` -- and 579 a property the script does not type, arrays/delegates/pointer natives), and **489**
have a completed value in front (a self-call whose declarer the corpus does not carry, or a receiver the walk did not
reach). So **11,811 of the 13,507 (87%)** by-name sites name a declaring class. One caveat, measured: **820** of the
8,251 chain-resolved sites would *also* be placed by the expression in front (a statement ending in an object leaves
one beside the next call), so the pass prefers the chain reading and writes the receiver class into every row it does
place. The obvious next instrument is the local-variable declarations, and it is worth 628 sites.

**2026-11-11, the local-variable declarations: the operand is a record the function owns, the type is in the
source the class ships.** A `LocalVariable` operand is not an index into a locals array -- it resolves to a
property export whose **outer is the calling function** in 540 of 540 local receiver sites in `Engine.u` (389
`ObjectProperty`, 62 `IntProperty`, 37 `StrProperty`, 23 `ClassProperty`, 9 `StructProperty`, 7
`ArrayProperty`), so the record names the variable and nothing else. Its payload does **not** carry the type
where a reader can reach it: of the 281 `ObjectProperty` locals whose source type is known, no payload offset
names that class for more than 30 (offset 4: 30, offset 5: 16, offset 3: 8), so `[ArrayDim][class reference]`
is not what this cook writes. The class ships its own `ScriptText`, and `local <Type> <Name>;` plus the
parameter list bind exactly the name the export table gives, so `--calls` parses each class's source once per
package and reads the type from there. **490 of the 628 local sites are placed that way**, which takes the
untyped locals to **138** and the by-name sites that name a declaring class from 11,811 to **12,301 of 13,507
(91%)**; the residue is **1,206 sites**, 717 with an untyped object expression (138 locals, 579 properties the
script does not type) and 489 with a completed value in front. The control is a shuffle, and it is a flag now
(`--calls --shuffle`): the same walk with each class's declared types rotated onto a neighbouring name leaves
**12,753 of 13,507 (94.4%)** by-name sites naming a declaring class against **13,258 (98.2%)** as shipped, and the
typed-local receiver placements fall **582 -> 80** (whole receiver group **2,641 -> 2,123**) -- so about six in
seven of the local placements are the declaration deciding, and the survivors are names that land on any chain.
What is left of this route
is a local whose source binds no class -- no `ScriptText` for the class, no declaration for the name, two
different declarations of it, or a declared type that is not a class. The 579 untyped properties are the next
instrument, and their declarations come from the tagged property stream that `class_properties.csv` is read
from.

**2026-11-11, later the same day: the numbers above are superseded, and the residue has names.** `--calls` now
counts every site that stays unplaced by *why* its expression typed to nothing, and two labels that used to
overlap are separate: **656** unplaced sites with an object expression that names no class at all, **47** that
typed to a class whose chain declares no function of that name (`Pawn` 14, `BotBase` 14, `GameHUD` 4, fifteen
more over other classes), and **489** with a completed value in front -- **1,192**, so by-name sites naming a
declaring class are **12,315 of 13,507 (91%)**. Of the 656: **474 reference a property no package in the corpus
declares** (`UIWidget` 80, `HaakenMK2Weapon` 59, `Instigator` 56, `Controller` 48, `Pawn` 41, `Outer` 36),
**81 a local whose class's source has no declaration for the name**, **71 a literal or cast that names no
class**, **30 a token that is not a variable**. The largest bucket is therefore **not a script-typing gap a
better walk could close**: an import names an object in another package, so these are properties declared
outside `System` or declared natively, and the only reading that would type them is the native property table
(`docs/NATIVE_PROPERTIES.md`), not the packages. The split also found a real bug: the type-syntax reader knew
`Class<>`/`array<>` and not the lowercase `class<>` the scripts themselves write, which cost 14 sites; with
both spellings accepted the receiver group is 1,732 sites over 546 names and the local share 504.

**2026-11-11, the last of the call residue: the index was keyed on the state, the class was matched on its case.**
The residue above was announced as "a dispatch question", and two thirds of it was not: it was the same class of bug as
`class<LocalMessage>` -- a lookup keyed on a spelling nothing else uses. **(a)** A function declared inside a `State` is
owned by the class that compiles the state, but the by-name index was keyed on the function's *outer* export, which is
the `State`: `IsUnconscious` (declared in `state BotUnconscious`) sat under a name no chain walks. Keying it by the
owning class places **223** chain sites (own class +197, ancestor -127 -- the nearer declarer is the state the caller's
class owns) and 8 receiver sites, and the corpus-alone group falls 2,332 -> 2,290, for **8,321 on the chain**.
**(b)** UnrealScript matches a class name case-insensitively while the source spells `pawn`, `inventory`, `GameHUD`:
folding the case places **26** more receiver sites (`pawn` 14, `GameHUD` 4, `inventory` 2, `pickup` 2, `controller`,
`vehicle`) and types 25 more locals (**1,757** on the receiver's chain, 529 with a local in front). **(c)** The typed
residue -- 47 sites whose expression names a class whose chain lacks the call name -- is then **16**: a base-typed
receiver can hold any subclass, so `--calls` looks *below* the receiver as its last step and **6** place on the one
descendant that declares the name (`IsUnconscious`, `GetAggroLevel` -> `StargateGame.BotEnemy`, 3 each; the first is a
state function, which is why (a) and (c) meet on the same name). The other **10** are the dispatch the entry predicted:
`CallToArms` 3, `UnPossess`, `Possess` over several descendants, `GetMoveTarget` 2, `GetMyPlayer`, `TakeOver`,
`SetPawnClass` only outside the subtree -- and **0 names absent from the corpus**, so what remains is which object, not
which build. Total: **12,374 of 13,507 by-name sites (91.6%)** name a declaring class; the 1,133 that do not are 654
untyped expressions (473 an import no package declares, 81 a local its source does not declare, 70 a literal or cast,
30 not a variable), 10 typed-and-unplaceable, and 469 a completed value in front. `inventory/script_calls.csv` moved
**400 rows**, 327 of them in `resolved_in`.

**2026-11-11, same day: the import branch was dead code, and the reason was a `U`.** The 473 "a property no package
declares" were not absent from the packages: the tagged property stream names the property class `UObjectProperty`
and an import names it `ObjectProperty`, so `c[1] == kind` never matched, and the branch below it (`_class_of_type(c[3])`
on a three-field tuple) could never have run either. Comparing the names without the prefix types **233 more imported
receivers** -- the receiver's chain 1,757 -> **1,954**, the untyped-object residue 654 -> **421** -- and takes by-name
placement to **12,571 of 13,507 (93.1%)**. The 240 imports left in the residue name a property that two or more
packages declare with *different* types (`UIWidget` 61, `HaakenMK2Weapon` 50, `Controller` 48), and the import's outer
already names the owning class (`Pawn` 33, `GUI` 30, `GUIListBox` 16), so the next instrument is to prefer the owner's
declaration instead of requiring the corpus to agree.

**2026-11-11, and the residue above is closed by that instrument: the import's own outer settles the vote.** The 240
sites named a property that two or more packages declare with *different* types, and requiring the whole corpus to
agree was the wrong rule -- `UIWidget.Type` and `GUIText.Type` are different variables, not a tie to break by count.
An import's `OuterIndex` names the class that declares the property, and that is the declaration the compiler resolved
against, so `_receiver_reading` now prefers the owner's own record. **423 receiver readings** come from a disagreeing
import and type that way (`Pawn` 177, `Actor` 57, `GUI` 57, `LevelInfo` 46, `GUIListBox` 37, `BotBase` 19; 15 owner
classes), and **all 240 sites that were unplaced** place: the receiver's chain 1,954 -> **2,194**, by-name placement
**12,811 of 13,507 (94.8%)**, the unplaced residue **936 -> 696**. What is left is 181 expressions that name no class
at all (81 a local its source does not declare, 70 a literal or cast, 30 not a variable -- **no import**), **46**
typed to a class whose chain and subtree lack the name (`Object` 36, `BotBase` 12, `UnrealScriptedSequence` 3, `Pawn`
1; `ClientMessage` 25 of the names, a dispatch over every class in the build) and **469** a completed value in front.
The rule's control: where every declaring package agrees (700 sites) the owner's own class agrees 700 of 700 and
differs 0, so it supplies the vote a tie was missing rather than outvoting a consensus. The remaining gap is which
*object* a completed-value or `Object`-typed site calls on -- not something another reading of these packages can
settle.

**2026-11-11, second half of the same residue: an array element is an object expression and the caller had never asked
for one.** 430 of the 469 "completed value in front" sites were `A[i].Foo()`: the token in front of the call is
`ArrayElement` 349 or `DynamicArrayElement` 81, its two children are the index and the array (in that order, measured),
and the element's class is the array's declared element type (`array<Pawn>` -> `Pawn`). The reading branch alone moved
**nothing** -- `--calls` only asked `_receiver_reading` about a token in `OBJECT_TOKENS` -- so the fix is the set plus
the branch: **501 sites type over 49 (token, class) pairs** and **390 place** on the receiver's chain (2,194 ->
**2,584**), for **13,201 of 13,507 by-name sites placed (97.7%)** and a residue of **696 -> 306**. It also exposed
that a tagged type carrying a struct's inlining flags (`export editinline Order`, `editinlinenotify GUIComponent`) had
been read as a class name: a class is one identifier, so those 21 sites are now "declared, but not as a class" and no
placement moves. What is left of the 306: 221 object expressions that name no class (81 a local its source does not
declare, 70 a literal or cast, 40 an array element of a non-class array, 30 not a variable), 46 typed to a class whose
chain and subtree lack the name (`Object` 36 -- a dispatch over every class in the build) and 39 a completed value in
front. The next reading worth trying is the callee's **declared return type** (12 `FinalFunction` and 5
`VirtualFunction` receivers), which needs a function's return type off the same `ScriptText`; not attempted.

**2026-11-11, and the return-type reading is attempted: the residue it was pointed at is not there, but a spelling bug
beside it placed 57 sites.** `source_returns` reads a function's declared return type off the same `ScriptText` (the
first group of `_SRC_FUNC`), and `_receiver_reading` answers a call-token receiver from it: a `FinalFunction` names its
Function export, so the callee's class is that export's outer (a `State`'s function belongs to the class that owns the
state), and a `VirtualFunction`/`GlobalFunction` callee is resolved on the caller's chain. `OBJECT_TOKENS` had to gain
the three call tokens first, or the branch is unreachable -- the same gate that cost the array round a session.
**822 sites** front a by-name call with another call; **72 type** from the callee's declaration (`sound` 33,
`PlayerController` 22, `SGFormation` 6), **534 name a callee that returns nothing**, **131 an imported `Function`**
(declared in another package -- the route for anyone who wants it, though only 12 of those sites are unplaced), 72 a
return value that is not a class, 11 a callee whose class declares no return type for it and 2 outside the caller's
chain. Only **4** placements come from it, because most of those 822 were already placed on the caller's chain, and
the 39 "completed value in front" turned out to be mostly statement tails (`JumpIfNot` 1,250 sites asked, `Let` 520,
`Jump` 318), not receivers. Two readings beside it did the work instead: the **ancestor's** `ScriptText` for a local
its own class does not declare (**7**, `Engine.Vehicle` 3, `Engine.Actor` 2, `StargateGame.DeathMatch` 2), and
**matching a declared name without case** the way the compiler does (**149 sites**: `Msg` 30, `t` 13, `i` 12, `Dir` 8
-- the same defect as `pawn` and `class<LocalMessage>`). By-name placement **13,201 -> 13,258 of 13,507 (98.2%)**,
residue **306 -> 249**, the receiver's chain 2,584 -> **2,641**, untyped locals **81 -> 28**. What is left is 181
expressions that name no class, 46 a base- or `Object`-typed dispatch and 22 statement tails, and none of those is a
question another reading of these eighteen packages can answer -- which is the negative this entry closes on.

**Fourteenth session, 2026-10-29: two negatives worth keeping.** The 85 scripts that still refuse after the operand
correction are a per-opcode tail, not a rule: `LabelTable` non-termination 16, then `0x52` 6, `0x54` 5, `0x49` 5,
`0x03` 4, `0x4c` 3, `0x2b` 3 and four parameter lists ending on `0xff`. And **class scripts are not compressed**: all
2,319 `Class` payloads were tried at five offsets past `FriendlyName`, with four skips and both zlib framings, and none
inflates to something that starts with a `Return` or ends on the `'G'` marker. Since no class carries the `04 0b 47`
epilogue either, a class's script is stored some third way — a fact to carry, not a lead.

**The image route was attempted and its address is wrong.** `LinkerLoad::Serialize` at `0x000515d0`, named in this
entry as the definitive route, is **not a function start**: `ExportAt.java` reports "no function at this address"
after `SeedGaps` has run, and the cached decompilations of the neighbourhood (`0x00038dc0` = `UObject::Serialize`,
`0x00037110` = the 4-byte field writer, `0x00036740` = a byte writer) contain no reader for this header. So the
route needs the real address first, from a call site into the export loader, rather than the number recorded here.

**What would settle it, now that the tail is known.** The remaining question is only how the script's length is
encoded in front of it, so read the *load* side rather than the payload bytes: `LinkerLoad`'s own `Serialize`
(`0x000515d0`, slot 11 of the `LinkerLoad` table in `inventory/class_serialize_slots.csv`) is the code that fills
`Function->Script` and its size, and the store to `[reg+0x4c]` is the anchor to read backwards from.

**Why it has not been attempted from those two routes yet.** Two sessions tried the payload-bytes route first; this
entry exists so the third starts from the reader, not from another alignment guess.

**Sixth session, 2026-10-29: the header's encoded part is pinned and the `Children`-chain rule is dead.**
`tools/uscript-dis.py --header` decodes the payload's leading values with the archive's own compact index and
requires the **sixth** to be the export's `FriendlyName` name index: that aligns **12,360** payloads, and it is
**11,687 of the 11,716** that carry the epilogue (99.75%) — so the header's last field is identified by content, the
same way the tail's was. The count of values in front of it is **constant at five** for every payload, whatever the
parameter count, which is what the previous session's test got wrong: `Super` + one reference per parameter is not
the rule, and the earlier +1/+2/+3 residuals were the encoded *widths*, not a missing reference. Resolved against the
package tables, the five are `v1` = `Super` (import 3,226, same-named function in a parent class 2,406, zero 6,728),
`v2` = `Next` (another function of the same class 10,529), `v4` = `Children` (a child of this function 9,223), and
`v0`/`v3` always zero.

**What that leaves is the fixed run after the name.** With the writer body's three `u32`s plus the size — 16 bytes
past `FriendlyName` — the size dword equals the remaining length in **672** payloads and the walk from there reaches
the epilogue in **1,975**; the rest fail at that start in the walker's bad-token case (3,824) or on an unterminated
parameter list (1,617), and the failures concentrate in the long scripts, so the 1,975 that land say nothing about
them. The two explanations still standing are therefore (a) a field
in the fixed run that the reading of `UStruct::Serialize` misses, and (b) the opcode table's 35 unnamed opcodes
being what stops the walk. **The test that separates them is a script whose first token the shipped `ScriptText`
fixes** — a body that begins with a literal the table classifies unambiguously — and it is now the cheapest route,
because everything needed is in the packages.

**Seventh session, 2026-10-29: the serializer's call sites are measured, and the loader is a vtable slot.**
`tools/ghidra/ghidra_scripts/Callers.java` exports every function that references an address, so the load side can be
looked for from the reader rather than guessed. `UStruct::Serialize` (`0x000392b0`) has **exactly two direct call
sites** — `0x000394c9` inside `0x000394c0`, and `0x00039f1a` inside `UFunction::Serialize` (`0x00039f10`), which is
the order `docs/BYTECODE.md` documents — and `UFunction::Serialize` itself has **none**, because it is dispatched
through a class table. So **the linker never calls the struct serializer directly**: the payload's header is read by
whatever `Object->Serialize(Ar)` virtual call the export loader makes, and a search for a call to `0x000392b0` from
linker code cannot succeed. The route that remains is the vtable slot, which the project already has named for 492
class tables (`inventory/xbe_class_tables.csv`).

**And the header decode does not line up with what that serializer writes.** Decoding the payload from offset 0 puts
`FriendlyName` sixth in **7,104 of 7,115** `Function` payloads in `Engine.u` and `StargateGame.u`, but decoding from
offset 2 puts it third in 4,629, fourth in 2,121 and fifth in 354 — a variable count that **does not track the export
table's child count** (three values with 0 children: 899 payloads, with 1: 1,472, with 4: 321). So the variable part of
the header is not the parameter list, and the leading values are not simply `Super` + `Children` as the body reads.
Two readings survive: a fixed prefix of linker bytes in front of the struct's own output, or a value whose decoded
width differs from its stored width. Neither is measured yet, and the fixed run after the name stays open on top of it.

**Eighth session, 2026-10-29: the header is a fixed four values for a class and five for a function, and classes are
the control.** A class runs the same `UStruct::Serialize` and carries no `UFunction` tail, so it separates "a field per
child" from "a fixed header" without any reading of the body: a class's children range from 1 (`Time`, `Locale`) to 618
(`Actor`) and **every one of the 2,316 aligned classes holds four values** in front of `FriendlyName`; every one of the
12,355 aligned functions holds five. The `Children`-chain model is therefore dead as a width rule, and the residuals
that killed the earlier per-parameter prediction were compact-encoding widths.

What the five values are, over the whole corpus: `v0` always zero, `v1` = `Super` (3,226 imports, 2,406 same-named
functions in a parent class), `v2` = `Next` (another function of the same outer, 10,529), `v3` always zero, `v4` =
`Children` (9,218). For a class the order shifts: `[Super][0][Children][…]` (2,317 of 2,318 resolve `v2` to a child).

The fixed run after the name is the last piece, and the class control cannot reach it: **no class payload carries the
`04 0b 47` epilogue at all (2,319 of 2,319)**, so a class's script is not stored the way a function's is and there is no
end to measure the run against. On functions the dword at `name_end+16` equals the distance to the epilogue in **2,141**
payloads and the walk from there reaches it in 1,975; the ones that miss fail on the walker's refusals spread across
the opcode range, so the start is probably still wrong for them and the run's length is not settled.

**Ninth session, 2026-10-29: the locator is solved — the script starts 16 bytes past `FriendlyName`.** Three
independent readings agree, and `python3 tools/uscript-dis.py --start System` reproduces all of them:

* the **floor**: an empty body still pays the epilogue and the trailer, so the smallest payload after `FriendlyName` is
  the run plus 3 plus 7 (9 when `Net`). Measured: **25** is the floor over 12,360 payloads, with spikes at **26**
  (1,380) and **28** (801) = `16+3+7` and `16+3+9`. A 12-byte run would floor at 22, where nothing appears.
* the **walk depth**: from `name_end + 16` the decoder gets 16–63 bytes in 3,494 payloads, 64–255 in 1,486, past 256
  in 86, and all the way to the epilogue in 3,328. A wrong start desynchronises within a token or two — that is 979.
* the **dword at +12** is not the script's length: larger than the physical script in 9,298 payloads, equal in 2,052,
  never smaller. So the fourth word of the run is another field, and no size field is required: `SerialSize` already
  fixes the script's end.

That closes 3.7's *locator*: the start is computed from the header, not searched for. What is left is the opcode
table's coverage (the 979 early refusals plus the ~5,000 later ones are unnamed or mis-patterned opcodes) and the
identity of the run's four words.

**Tenth session, 2026-10-29: the disassembler runs on shipped functions, and the walk refusals have no single cause.**
`--script` disassembles a function from `name_end + 16` to the epilogue, and `--usage` runs the corpus: **3,444 of
11,687** scripted payloads walk clean end to end, and their opcode census (`inventory/opcode_usage.csv`, 153 opcodes)
is now the order the per-opcode work should be done in. The refusals are spread across the table — `VirtualFunction`
708, `BadToken` 690, `FinalFunction` 424, `LabelTable` 415, `0x77` 271, `0x00` 186 — and **a single-opcode fix does not
work**: giving `0x03` each operand width from 0 to 6, or an expression, completes at most **75 of its 690** payloads.
That is the signature of a desynchronisation earlier in the stream, which makes the walk refusals a per-opcode
investigation with the usage census as its guide, not a bug to be patched in one place.

**Eleventh session, 2026-10-29: five operand widths were four fixed bytes and are a compact index — 99.3% of scripts
now walk.** The walker's handlers for groups 0, 13, 14, 15 and 19 read an `int`, and `tools/opcode-table.py` recorded
that as `bytes:4`. Held one group at a time against the corpus, with the start now known, the payloads say the operand
is the archive's **compact index** (bit 7 sign, bit 6 continuation, six bits then seven per byte):

| groups as `bytes:4` (shipped) | groups 0, 14, 15 as `index` | + group 13 | + group 19 |
|---:|---:|---:|---:|
| 3,328 of 11,350 walk | 8,869 | 10,040 | **11,269** |

`--usage` over the whole corpus agrees: **11,602 of 11,687** scripted payloads walk clean, against 3,444 before, and
`--start`'s depth histogram moves with it (11,269 to the epilogue, 81 refusing at all). The table now regenerates with
`index` for those groups and `inventory/opcode_usage.csv` is the census of the new set. This is the per-opcode work
3.7 was waiting on, and it was one rule — not the 690-payload `BadToken` case, which a width change still rescues at
most 75 of.

**2026-11-12, the start is anchored on the shipped source, and the walk that used to prove it proves nothing.**
`tools/uscript-dis.py --start-scan` walks from every delta in `name_end+8..+40` and counts the ones that land on the
epilogue: over `Engine.u` that share is **99.3% at +14, 99.2% at +16, 87.1% at +11, 79.3% at +13** and 73% at +30,
because most opcodes are one byte and a garbage stream consumes the payload exactly. So "the walk from +16 reaches the
epilogue" is a coverage figure for the opcode table, not a locator, and the same is true of the payload-floor reading
(a floor of 25 bytes fits any 16-byte run that keeps `3 + 7` behind it). The anchor that discriminates is content:
a `Function` export's class ships its `ScriptText`, so a body opening with `return true;`/`return false;`/`return;`/
`return 0;`/`return 1;`/`if (`/`assert (` fixes its first opcode bytes (`04 27`, `04 28`, `04 0b`, `04 25`, `04 26`,
`07`, `09`), and `--start-anchor` asks which `name_end + delta` carries them. An `if (` or `assert (` body is **not** an anchor: this compiler evaluates the condition before it emits the
conditional jump, so `07`/`09` is not the body's first byte — with those two in the list, 597 of 716 payloads that
matched at no delta carried the expected byte nowhere in the payload at all, which is what a wrong anchor looks
like. On the `return`-only population (`04 27`, `04 28`, `04 0b`, `04 25`, `04 26`) the 18 packages give **658 anchored
payloads, 225 with the expected bytes exactly at +16 and all 225 walking from there to the epilogue**, against at
most **4** at any other delta in `+4..+24` and **0** at payload offset 0. Two residues stay open and are counted:
**422** anchored payloads carry the expected bytes at no delta in `+4..+24` (216 `0428`, 153 `0427`), a
header-alignment question now that the anchor is valid; and the dword at `name_end+12` equals the script's length in
**223 of 236** rows the anchor matched, while corpus-wide it is larger far more often — so it is a size whose
relation to the script is not constant, and it is still unidentified.

**2026-11-12, later: the 422 were a mispairing, and two rules close the locator.** `--start-anchor` had keyed its
declaration by the function *name* alone, so a single class's `return false;` anchored every same-named `Function`
export in the package (`Destroyed` is exported from dozens of classes in `Engine.u` alone); those payloads were then
searched for bytes their own source never contained, which is why 422 of 658 matched at no delta. Keyed by
(class, function) — the export's own `Outer` — and with the literal's opcode chosen by the declaration's **declared
return type** (no return type writes `return <expr>;` as a bare `Return Nothing`, `04 0b`; `float` writes `04 1e`
plus the float; `byte` writes `04 24` plus the byte; only `bool`/`int` reach `04 25`/`04 26`/`04 27`/`04 28`), the 18
packages give **441 anchored payloads, of which 422 carry the expected bytes exactly at `name_end+16` and all 422
walk from there to the epilogue**, against at most **9** at any other delta in `+4..+40` and **0** at payload offset
0. What is left is **12** payloads (3 compile to an empty body, 8 open with `07`, 1 with `04 82`), 31 declarations
made twice in one class, 10 whose return type has no measured rule, and the 2,137 `Function` exports owned by a
`State` object, which this key does not reach. The dword at `name_end+12` is the script's length in **420 of the 422**
rows whose start is +16 — true of this trivial-`return` population and not of the corpus, where the same dword is
larger far more often: the field is equal here and still unidentified.

**2026-11-12, later still: a state's body is reachable from the class's own ScriptText.** The 2,137 `Function`
exports owned by a `State` object are not class declarations, but the class's `ScriptText` does carry its
`state <name> { ... }` blocks, so keying those payloads by (class, state, function) applies the same three rules —
and they hold there even more sharply: **93 anchored, 92 at `name_end+16`, all 92 walking**, 1 with no match (its
body compiles empty). With the 441 class-owned payloads that makes **534 anchored, 514 at +16 and all 514 walking**,
at most 10 at any other delta in `+4..+40`, 0 at payload offset 0, and the dword at `name_end+12` equal to the
script's length in **512 of the 514** rows whose start is +16. What remains unanchored is 31 declarations made twice
under one key and 10 whose return type has no measured rule; what remains unmatched is 13 payloads (4 empty bodies,
8 opening with `07`, 1 with `04 82`). A trap worth recording: offsets inside a spliced state body are already
relative to it, so `first_statement_kind(body, m.end())` — not `m.end() - block_offset` — is the call, and the wrong
one turns the 92-of-93 peak into a flat histogram over `+16..+39`.

**2026-11-12, later again: the residue was the anchor reading a state's body as the class's.** `--start-anchor
--lists N` now names each unmatched payload instead of only counting it (`--lists` prints the expected bytes, what
the payload carries at +16, whether those walk, and the dword at `name_end+12`, plus the two off-population groups),
and the 12 of the 13 above came back as one cause: the class-level pass ran the declaration regex over the whole
`ScriptText`, which also matches every function declared inside a `state { … }` block. Where the class's own
declaration is not an anchor — `Engine.u`'s `Weapon.IsFiring` is `return ((Instigator != None) && …)`, while a state's
`IsFiring` is `return true;` — the state's literal became the class-level expectation, and the class-owned export was
searched for bytes only the state's body has. Blanking those spans before the class-level pass (same length, so every
offset stays valid) takes the corpus to **540 anchored, 536 at `name_end+16` and all 536 walking**, at most 10 at any
other delta (`+18` only, its `+21`/`+22`/`+28` tails gone), **2** declarations dropped as doubled, **10** with no
measured rule, and **4** unmatched. All four are one shape and are not a header misread: they carry `04 0b 47` with a
length field of 3, so the compiled body is empty — `Pawn.CanMultiJump`, `Pawn.CanDoubleJump`,
`ScriptedController.SetRouteToGoal`, `BotEnemy[BotUnconscious].WeaponFireAgain`. **A literal `return` can compile to
nothing, but it is not the rule:** the other 422 bool-literal declarations emit `04 27`/`04 28`, and nothing in the
payload separates the four (same `0x00020000` flags), so what would settle it is their own declaration text and one
`--script` read per case.

**2026-11-12, closing it: their declaration text was the answer, and it has no body.** `function bool CanMultiJump();`
— a declaration-only stub, implemented in a state or in native code. The anchor's body finder takes the first `{`
after the parameter list, so for every such stub it paired the *next* function's body: 82 declarations in the corpus
are like this (all the `VoiceChatReplicationInfo` accessors among them), each searched for a literal its own export
never contained. Checking `;` before `{` and treating the payload as a prediction instead gives
**466 anchored, 466 at `name_end+16`, all 466 walking, 0 unmatched**, with **82 of 82** bodiless payloads compiled to
the empty script and none carrying one — so the four "empty bodies" above were the compiler being right and the parser
being wrong, and the 70 `04 0b` payloads that had been counted as anchored were anchored on a borrowed `return;`.
The two remaining exclusions are `Weapon.GetErrorVec` and `Weapon.GetFireStart`, each declared both `native` and with a
body; they are filed under the bodied declaration (and that body's first statement is not a literal, so they are not
anchors either).

**Sixth session, 2026-11-12, and this closes it: the field is the script's size in memory, and there is no length
to find.** The dword at `name_end+12` is **the script with every compact index operand widened to four bytes** —
`len(script) + sum(4 - width)` over its index operands. `python3 tools/uscript-dis.py --sizefield System` measures
it exactly in **11,485 of 11,602** walked payloads (98.99%), against **2,141** (18.45%) that carry the stored
length; the 464 of 466 above equal it only because those `return` bodies have no operand to widen. That agrees
with the loader, which widens those operands on the way into memory (`StargateHUD.DrawOrderTarget`'s log call is
`b8 2a` in the package and `a0 1e 26 01` in the guest), and it explains why every search for a *length* in front
of a script failed: the loader reads the payload's `SerialSize`, and this field is a check on an extent instead
— a candidate end must satisfy it, which is the first test here that does not depend on the opcode table.
The residue is **117 payloads** (97 above the prediction, 20 below, four of them with a dword in the millions, so
their header decode is ambiguous); `--sizefield --lists N` names each row with its index widths, and **no single
operand class explains them** — every pattern item's count per residue row was compared against the difference and
nothing fits — so they are individual token encodings, not a second rule.

**Seventh session, 2026-11-12: the residue is asked per row, and 99 of the 117 have no first operand to blame.**
`--diverge` tries every span of every residue row as *the* explanation, in the two shapes a single operand can take:
an `index` the compiler did not widen (moving the prediction by `-(4 - width)`) or a `fixed` operand whose bytes decode
as a compact index of exactly its own width (moving it by `+(4 - width)` while the walk stays put). **9 rows have
exactly one** such operand -- 81 `index-not-widened` sites at op `01`, 31 at `00`, 7 at `1b`, and 19 `fixed-is-index`
sites at op `39`, 10 at `2c` -- 9 have several, and **99 have none**, so most of the set is not a mis-widthed operand
but an accumulated difference. The corpus-wide rule test agrees: 2,376 single-feature rules (`dword - widened ==
k * count(feature)` over 297 features of the rows, k 1..8) were tried against all 11,602 walked rows and **none is
exact**; the closest leaves 70 rows. One arithmetic reading is dead rather than merely unproven: the largest
difference is **+4 in 40 rows over at least eight classes** (`GUI.GUIController` 5, `GUI.GUIMessageBox` 4,
`StargateGame.GameMenu` 4), and a `case` value widened from u16 to u32 would have fitted 2 of the 117 while 58 *exact*
rows carry a case. **What would settle the +4 mode is a guest read**: the same instrument that proved the widening
(`StargateHUD.DrawOrderTarget`, `b8 2a` in the package against `a0 1e 26 01` in RAM) applied to one +4 row
(`Engine.StreamPlaylist.AddStream`, dword 200 against a predicted 196) shows which bytes the loader added where the
files cannot speak. The four rows carried as "header decode ambiguous" are one smaller step further in: `--diverge`
searches the 44 header bytes for a dword equal to the prediction at every offset, and **none of the four carries one**
(11,665,408; 3,932,095; 7,798,660; 261,119 differences), so their dword is not this field read a few bytes off -- it is
a payload whose header this reader misaligns, and only a live loader read names what it actually is.

**And the anchor now accounts for every declaration in the corpus**: 466 anchored, 2 declared twice under one
key, 82 with no body, and **0 unanchored payloads carrying a script** — a line `--start-anchor` prints for this
claim, since a row there would be a declaration with an unmeasured return type *and* a script to measure it from.
So the `vector`/`rotator`/`struct`/`array` literal encodings an earlier note named as the next measurement are
not exercised by any shipped body: every declaration that named one of those types is a bodiless stub
(`Weapon.UpdateLeadLag` `rotator/zero`, `VoiceChatReplicationInfo`'s three `array<voicechatroom>/false` accessors
and two `int/false` counters, `BotEnemy.SquadCoverRequest` `esquadcover/false`), and the `vector/zero` row appears
in neither residue.

**Eleventh session, 2026-11-12: the `+4` mode is one opcode's operand -- measured in a guest, then confirmed on
the files.** The previous sessions' feature census counted *tokens*, and the missing quantity lives inside one: the
`0x43` `DelegateFunction` reference is **two compact indices**, not the four fixed bytes the table recorded.
`tools/xemu/script-ram.py` searches a `pmemsave` image for a script's memory model (opcodes and fixed operands exact,
every index operand four wildcard bytes) and measures where the copy's epilogue actually sits. On `Engine.u`'s
`StreamPlaylist.AddStream` (script 146, dword 200, predicted 196) the copy at `0x00cd84c0` agrees with the model for
**176 bytes**, disagrees first at the `0x43` reference -- **8 bytes in RAM** against 4 in the file -- and then agrees
with the model's whole tail **shifted by exactly +4**, ending on the same epilogue: the copy is **200 bytes**, the
field's own number, and `45 1b` / `7b 06` are two two-byte indices, which is exactly the +4 the field counts.
With the row corrected to `index,index,expr`: **11,608 of 11,687 payloads walk (was 11,602), 11,565 of 11,608 satisfy
the identity (99.63%, was 11,485 of 11,602), and the residue falls from 117 rows to 43** -- six previously-refused
payloads now walk and none regressed. **What is left** is 43 rows: `-3` is now the largest shape (14, an index the
loader does not widen, i.e. this question in the other direction), with small `+-1..+-10` differences and the three
million-scale rows this entry already carries as a misaligned header. Each of those needs the same guest instrument
rather than another file-side census.

**Twelfth session, 2026-11-12: the `-3` and `-6` rows read in a guest, and the chosen slot is not the token class.**
The mirror residue is the model over-widening, not the loader mis-reading: `StreamBase.ConvertToFileExtension`
(field 207, predicted 210) has a copy at `0x00cd8b00` that is **207 bytes -- the field's own number** and `205 + 2`,
with 208 of 210 model bytes agreeing; `GameMessage.GetString` (499 against 505) and `TexOscillatorTriggered.Trigger`
(174 against 180) land on their fields the same way. `Trigger`'s last six bytes name the mechanism: the model has four
wildcards, `86 00`, four more, `39 3a`, while the copy carries `07 86 00 9b 39 3a` -- **two slots of one byte**, one
per -3. And the opcode does not decide it: one `LocalVariable` operand in `ConvertToFileExtension` is four bytes in
the copy and the script's last one stays a single byte, so the residual question is not "which opcode" but "which
slot", and each of the 43 residue rows now has a location this instrument can name. The listing that made these rows
findable is fixed in the same change (`--sizefield --lists` printed `Class`, the metaclass, for every row).

**Twelfth session, 2026-11-12: the residue is a slot width, and 10 of the 43 rows are answered.** The dword is not one
field with an exception list; it is `len(script) + sum(4 - width)` over the slots the *loader* widened, and a guest
copy is the only witness to which those are. `tools/xemu/script-ram.py --residue CSV` aligns the model span by span
(an index operand free to be 1, 2 or 4 bytes) over six existing `pmemsave` images: **13 rows align completely, 10 of
those copies are the field's own number**, and the difference is exactly the 1-2 slots left at file width -- all of
them one byte wide, on `op00`/`op01`/`op02` (9/3/1). Neither the opcode nor the operand's value selects them.
`InitializeParsers`, `EnableShadows` and `CalcPathPoint` (the three million-scale fields) have complete copies of the
*model's* length, so those payloads simply do not carry this field at `name_end+12`. The 30 rows still without a copy
split into "not resident in these six images" (`GamePlay.u`, `IpDrv.u`, `StargateContent.u`: 0 spans) and a real
divergence (`Actor.DisplayDebug` at 212 of 1,605 spans, where three slots the model counts as one byte hold four-byte
pointers -- the reverse of the `-3` rows). **What would settle the rest:** name the token at `DisplayDebug`'s stop
from the copy (the bytes are in the model at file offset `0x165`), which is the last row whose difference is not a
slot this reading can already name; and re-read the 8 `StargateMenus.u` and 7 `UWindow.u` rows against a dump taken
with a menu open, since no body of those packages is resident in an in-game image.

**2026-11-12, the stop is a four-byte *opcode* prefix that only some literals get, and a global rule does not fit.**
`tools/xemu/script-ram.py` carries a second model (`op1f`: an `op 1f` opcode is four bytes, the literal after it
unchanged) and reports which rule read each copy. On `Actor.DisplayDebug` the eight `StringConst`s inside the 455
bytes the strict model reproduces are all copied verbatim, and only the two at file 0x167/0x16a are expanded -- 2 of
that script's 83. So widening every `op 1f` desynchronises at the first unexpanded literal (16 spans, 32 bytes), and
keying on the preceding `0f 00` pair reaches 214 spans before the second expanded literal: the deciding property is
per literal, not per opcode and not the preceding token. The four bytes are the same value `0x00fe4980` for both
literals, in all six dumps, 125 times in one 128 MiB image; the target reads as zeros and pointers rather than text,
and `default.xbe` does not contain that dword at all (0 matches), so it is a load-time heap address, not something the
image names -- "a resolved pointer to this literal" is what the bytes disprove. Corpus-wide the rule closes **0 of the
30 unaligned rows**. It also corrects the literal above: it is ` REMOTE ROLE ` (`54 45`), not ` REMOVE ROLE `. **What
would settle it** is the handler rather than the payload: the body behind `GNatives[0x1f]` (`docs/NATIVE_VM.md`) says
whether the four-byte slot is read at all, or a byte-diff over many more copies correlates which literals expand
inside one script.
**2026-11-12, the handler is read and it reads no four-byte slot.** `GNatives[0x1f]` is
`intUObjectexecStringConst` at `0x00047bf0`, 69 bytes (`tools/gnatives-body.py --slot 31`, listed in
`docs/NATIVE_VM.md`): it copies the literal at `FFrame::Code` through a fixed `0x400`-cap helper into the result
`FString` and advances `Code` **one byte at a time** to the NUL and one past it. So the token has no count and no
operand, and the executor's own opcode read is a single byte (`0x48ce5`), which makes the copy's `80 49 fe 00` at both
stops opcode `0x80` (`GNatives[128]`, `execLeft`) plus three operand bytes -- the two literals are not read through
slot 31 from those offsets. The `op1f` model in `script-ram.py` keeps the right byte count and loses the wrong claim:
what the loader writes there is a **replaced opcode**, not a widened `StringConst`. **What is left** is who writes it
-- read the 16 bytes at `0x00fe4980` and the object they point into (125 such dwords in one 128 MiB image, all in a
`0xfe4xxx` heap page of the same shape), or break at `0x48cf5` with `Actor.DisplayDebug` running and read the opcode
that actually arrives at the stop -- and re-read the 8 `StargateMenus.u` / 7 `UWindow.u` rows against a dump taken
with a menu open, which no run has done yet.
**2026-11-12, a menu-open dump does not place the 15 menu rows, and "not resident" is disproved.** A seventh image taken
with the front end's menus open (`$WORK/menu1/ram.bin`, `frame-diff` passed) reads the 8 `StargateMenus.u` rows at
9/9/0/5/5/1/1/5 spans and the 7 `UWindow.u` rows at 0/10/0/5/0/0/0 -- no complete copy, the same 5-10 span peak, so
the menu packages' bodies are not laid out at the modelled offsets even while a menu is running. The image does answer
whether they are there: `tools/xemu/resident-names.py --residue` counts each row's class name in the `FNameEntry` pool
(wide name at `+0x0c`), and **41 of the 43 residue rows' class names are resident in it**, the two exceptions being
`UDebugMenu.u`'s `UGraphParticlePage` and `UDebugMenuBar` -- the package `docs/NATIVE_VM.md` already measured as never
loaded. Two of the six in-game images read the same way (`msg1/try1`, `xemu/findtext4`): `GamePlay.u`,
`StargateContent.u` and `StargateVehicles.u`'s class names are resident in both, while `findtext4` lacks `ExtrasPage`,
a menu class that image never loads -- so the counter separates images, and the earlier "no body of theirs in any of
the six images, not resident" line was an inference from a failed anchor, not a measurement. **What is left** here is
one instrument, not more images: locate a copy by a *content* anchor (a literal the function carries) instead of a
prefix, and read the true script start back from the guest -- the same start problem U13's first rows were about.

**2026-11-12, a content anchor places none of the residue rows, and it clears the *start* of the ones it reaches.**
`tools/xemu/script-ram.py --anchor-literal` derives a copy's start from a literal the script carries
(`hit - model offset`) rather than from the model's prefix, which is the route this entry's last line asked for. Over
the 43 `--sizefield` residue rows against the four 128 MiB images still on disk it closes **0 of the 30 unaligned
rows** -- 13 complete copies and 10 the field's own number, unchanged -- and the population says why: **21 of the 30
carry no literal of 6+ bytes at all**, and **6 of the 9 that do** (three of them `StargateMenus.u` buttons) carry one
that occurs nowhere in any of the four images, so those scripts are absent from the guest rather than hard to anchor.
The three rows that do hit align exactly as far as the prefix search did (`Actor.DisplayDebug` 212 of 1,605 spans from
45 candidate starts, `LadderInfo.UpdateLadders` 0 of 415, `CommandInterface.executeScriptCommand` 0 of 286), so a
wrong start was never what they were missing; **14 of the 30 align nothing at all** (0 spans, e.g. `GameMessage.GetString`
0 of 285, `UWindowWindow.WindowEvent` 0 of 323, `UWindowListBox.DrawItem` 0 of 193). Where a copy is placed the anchor
also answers the start from the guest side: on `PlayerController.ServerJoinVoiceChannel` (327 bytes at `0x00d72000`)
and `StargateWeapon.PlayFiring` (464 at `0x012dc700`) the 16 bytes in front of the derived start match the payload's
header tail in 9 and 10 of 16 positions, never more than 2 from the right, so the export's header does not precede the
script in memory and `name_end + 16` is confirmed from the guest. **What would settle the rest** is an image taken
while one of those functions is running rather than a better anchor: a break (or a read of the `Script` store) at
`0x48cf5` with the menu's own class live, and -- for the `op 1f` wedge inside `Actor.DisplayDebug` -- the writer of the
`0x00fe4980` dword, which no image names and `default.xbe` does not contain.



**2026-11-13: the call residue is named, and the two readings it lost were the tool's.** `--calls --lists N` prints
every site of the untyped-object residue with its package, class, function, call name, the token in front and that
token's operand name; the 181 sites were five shapes -- 70 casts, 28 locals, 40 array elements, 25 struct members, 18
others -- and two of them were reading gaps. A cast names its class through the package's **import** table whenever the
class is declared elsewhere, and the cast branch consulted only the export table; and the shipped source writes
`protected` (and `exec`) before a declaration, which the modifier list did not accept, so a class's own
`protected final event PushMenu` was never keyed and its `local GUIPage NewMenu` read as undeclared. Reading the import
and accepting the modifiers takes the residue **181 -> 83**, by-name placement to **13,356 of 13,507 (98.9%)** and the
receiver's chain to **2,738 sites** (was 2,641). **What is left** is 40 array elements of *struct* arrays (an element
of a struct array is not an object: a negative, not a gap), 25 struct members whose declared type is a class
(`StargateGame.SquadMenu.PostRender`'s `FaceWidget`, `GoauldConsoleHud.DrawHUD`'s `Message`), 17 call results whose
callee is an import with no return type in this corpus, and one local of `Engine.InteractionMaster.AddInteraction`.
**What would settle the 25** is the struct's own member declarations, which the class `ScriptText` carries -- the same
file-level parse as the local pass, with no boot and no Ghidra round.


**2026-11-13, the struct members: the declared class is a `var` line in the class text, and it names 24 of 25.**
The residue after the cast and modifier fixes was 83 sites over five reasons, and 25 of them were a `StructMember`
whose record names the member (`FaceWidget`, `Message`) and, through its Outer, the struct (`SquadItemStruct`,
`HUDLocalizedMessage`) but not the member's class -- a property record carries the kind `ObjectProperty` and no
target class, the same limitation the local records have. The class text does carry it: a struct's members are
`var` lines inside the `struct` block of the declaring class (`SquadItemStruct` in `SquadMenu`, `BackupData` in
`MindControlCrystal`), which `source_structs` parses into `{struct: {member: type}}` and the tool indexes over the
whole corpus. Six of the 25 are *imports* (a class that uses another class's struct imports its members, and the
import's OuterIndex is the struct), so the reading follows that field too. **24 of 25 place on the receiver's
chain** and the twenty-fifth types to `BotBase`, where `Possess` remains a dispatch; by-name placement goes
**13,356 -> 13,385 of 13,507 (99.1%)** and the untyped-object residue **83 -> 53**. The count of "array element:
the array's type names no class" falls 40 -> 35 for the same reason -- five `Engine.MaplistManager` arrays are
struct members declared `array<...>`, so their element type was behind the same block.

**What the 53 are, and none of them is a struct any more.** 35 arrays whose element type is a struct (an element
of a struct array is not an object: a negative), 12 call results whose `FinalFunction` operand names no `Function`
of the package, 5 receivers typed to a class whose subtree does not declare the name, and 1 local the class text
does not declare. The next reading is one of those four, not another pass over the declarations: a `FinalFunction`
operand that lands outside the export table is either an import or a mis-read width, and the import branch that
named the struct members is the same test.


**2026-11-13, the twelfth shape is an import as well, and eleven of the twelve place.** The twelve residue sites
whose receiver expression is a call result read "the operand names no Function of this package", and the operand
was `PlayerOwner` -- not a variable but a function `UWindow` declares, imported into the menu packages. The
`FinalFunction` reading consulted only the export table, so a static call to another package's function looked
like a gap; the import's own OuterIndex names the class that declares it, its return type is a `function` line in
that class's text, and `class_pairs` supplies the package to open. **The call-result reading goes 75 -> 131 sites
typed** (77 `playercontroller`, spelled lowercase in the UWindow source), **eleven of the twelve place** on the
receiver's chain, by-name placement **13,385 -> 13,396 of 13,507 (99.2%)**, the receiver's chain **2,767 ->
2,778**, and the untyped-object residue **53 -> 42**.

**What is left is four shapes, and only one of them is still a question about these packages.** 35 arrays whose
element type is a struct (a negative -- the element is not an object), 5 receivers whose name the caller's chain
does not declare (`GetMoveTarget`, `GetMyPlayer`, `TakeOver`, `SetPawnClass` -- classes outside the receiver's
subtree), 1 local `Engine.InteractionMaster.AddInteraction` declares and the class text does not, and 1 call
result whose callee is declared with no return type. Seven imported functions name a class the eighteen packages
do not declare, and every one of those sites is placed on the caller's chain anyway. So the call table's untyped
residue is 42 of 13,507 by-name sites, and the next pass over it would be a reading of the four shapes above, not
of another declaration.

**2026-11-13, the last local is a parse gap and not a missing declaration: a preprocessor directive sits between
the declaration and its body.** `Engine.InteractionMaster.AddInteraction` is declared twice in its class text, once
under `#ifdefXBOX` and once under `#ifndefXBOX`, and the body both share begins after the closing `#endif`, so the
brace scan stepped onto `#` and dropped it; the archive's own local records name it, the text does declare it, and
the two statements were one line apart. Stripping directive lines before the declaration parse types the local
(`Interaction`), takes the untyped-object residue **42 -> 41**, by-name placement to **13,397 of 13,507 (99.2%)**
and the receiver's chain to **2,779**, and empties the local shape entirely. The population is measured by the same
report: **4 declarations over 173 class texts** are split by a directive, and they are two functions declared twice
under a guard (`AddInteraction`, `StargatePawn.Talk`) -- so an `#ifdef`-guarded signature is now a parsed shape, not
a class-specific exception. **What is left in the residue is three shapes and no declaration**: 35 struct-array
elements (a negative), 5 receivers whose name the class subtree does not declare, and 1 call result whose callee has
no declared return type. **What would settle the 5** is whether their receiver's declared type is too narrow -- each
name is declared by a class outside the receiver's subtree, so the question is which class the site's object
expression really holds, and the receiver chain the tool already writes is the list to read them from.

**2026-11-13, a chained callee is resolved by the corpus too, and the four left are a dispatch.** A call in front
of a call is typed from its callee's declared return type, and the callee was looked up on the caller's super chain
only; the by-name call table already has a second, receiver-free lookup (a name exactly one class in the corpus
declares), so the chained callee now takes it as well. **One site places** (`StargatePawn.CreateShield`'s
`AskForShieldClass` -> `receiver:Engine.GameInfo`), three rows gain the `receiver_type` that says why, by-name
placement goes **13,397 -> 13,398 of 13,507**, the receiver's chain **2,779 -> 2,780**, and the untyped-object
residue **41 -> 40**. The four that stay untyped are named *with their declarer count*: `GetInstigator` 2 classes,
`SetSprinting` 2, `TeamIDLookup` 5, and one callee (`UnrealScriptedSequence`'s `Trigger`) with no declared return
type. **What would settle them is not another parse of this corpus:** several classes declare each name, so the
callee is a virtual dispatch whose receiver the site does not carry -- a runtime question, and the residue's first
shape of that kind.

**2026-11-13, most of the residue was two words inside the array's brackets.** The reason `--calls` gave for 35 sites
was "array element: the array's type names no class", and the pages had been reading it as "the array holds a struct"
for two rounds. The array's element type is `array<editconst editinline Stream>`, `array<export editinline
ScriptEvent>`, `array<editconst editinline direction>`: the tagged stream carries the property's modifiers *inside* the
angle brackets, `_class_of_type` unwrapped the brackets, and the rest -- `editconst editinline Stream` -- was handed on
as a class name, so a class named right there read as absent. Stripping the words `var` already accepts before the
unwrap, and letting a nested `>` follow a space (`array<class<Info> >` was captured one character short), **places all
35**: by-name placement **13,398 -> 13,433 of 13,507 (99.5%)**, the receiver's chain **2,780 -> 2,815**, the
untyped-object residue **40 -> 5**. So the "35 struct arrays" were class arrays -- `StreamPlaylist`, `direction`,
`ScriptEvent` -- and the residue that is left is a dispatch, not a declaration: **5 chained callees**, `GetInstigator`
(2 classes declare it) 2 sites, `TeamIDLookup` (5) 1, `SetSprinting` (2) 1, and one callee with no declared return type.
**A tag that names a struct is now named as one** rather than as an expression that names nothing (`array<PlaylistParser>`
after the strip is one identifier, and `source_structs` decides it), which moves no placement and takes the 157 rows
whose `receiver_type` spelled `vector`/`Rotator`/`IpAddr` down to 3 -- a class column no longer claims a class called
`vector`. **What would settle the 5** is not another parse: each is a virtual dispatch whose receiver the site does not
carry, the same runtime shape the chained-callee round named, so the residue now holds one kind of question and the
next reading of it needs a runtime receiver, not a better walk of these packages.

**2026-11-13, the last 54 receivers are a dispatch, and 36 of them are the root's own property.** The typed residue
(a receiver whose static type's chain declares no such name) is printed with names now: 54 sites, of which 7 place on
the one descendant that declares the name (`IsUnconscious` 4, `GetAggroLevel` 3), 42 stay a dispatch over several
descendants, 5 name a function only classes outside the subtree declare, and **0 name a function no class in the
corpus declares**. **36 of the 54 are `InstanceVariable Outer`** -- `Core.Object`'s `var native const object Outer`,
declared type the universal base -- so the descendant set is every declaring class in the corpus and the residue's
size is each name's declarer count (`ClientMessage` 3: `Pawn`, `PlayerController`, `Security`; `ConsoleCommand` 4;
`Possess` 5; `Save` 5; `ValidIndex` 2). The source those sites ship spells the call with no receiver and declares no
such function (`Engine.CheatManager` calls `ClientMessage` bare and neither it nor its chain declares one), so the
bytecode's `Outer` is the only reading, and for a call site the bytecode outranks the extracted text. **What would
settle the 42** is a receiver from a running image -- the static type is the root, so no walk of these packages can
narrow it -- while the five names outside the subtree (`GetMoveTarget`, `GetMyPlayer`, `TakeOver`, `SetPawnClass`)
are a different question the corpus can still be asked: what the field or local in front is *assigned*, which types
it far more sharply than its declaration does.

**2026-11-13, the five names outside the subtree were a package spelling, and the assignment walk is not needed.**
The question the entry above left open was answered by a cheaper measurement than the one it named: those five names
are not declared outside the receiver's subtree, their receivers' chains were **cut**. `classes.csv` spells a
package two ways -- its own `package` column carries the file's spelling (`GamePlay`) while `package_of_super` carries
the import's (`Gameplay`), 70 rows, all pointing into `GamePlay.u` -- and the super-chain index is keyed by
`(package, class)`, so every step into that one package landed on a key nothing holds and the walk stopped silently.
Twelve chain targets were unreachable that way, `ScriptedAction` alone the super of 46 classes. Folding the package to
the file's spelling (and printing the fold's own audit beside the chain line) takes the typed residue **54 -> 36** and
places **all eight** sites of those five names: `GetMoveTarget` on `receiver:Engine.AIScript` (2), `TakeOver` on
`receiver:GamePlay.ScriptedSequence` and `GamePlay.ScriptedSequence`'s chain (2), `SetPawnClass` on
`receiver:Engine.Controller`, and `GetMyPlayer` (3, one more than the count above) on
`GamePlay.ScriptedController`'s chain -- `UnrealScriptedSequence extends ScriptedSequence extends AIScript` and
`BotBase extends ScriptedController`, so both paths were inside the subtree of a chain that exists. By-name placement
**13,433 -> 13,461 of 13,507 (99.7%)**, the caller's chain **8,321 -> 8,413**, the corpus-alone lookup **2,290 ->
2,234**, `inventory/script_calls.csv` moves **109 rows and loses none** (92 onto the caller's chain, 28 onto a
resolution they lacked, 7 off a descendant guess). **What is left in the typed residue is 36 sites and one reading**:
`Outer` is the root's own `object` property, so an `Object`-typed receiver is a dispatch over every declaring class --
the runtime question above, unchanged and now the only one. A lesson worth carrying past this entry: a chain that
ends early and a class with nothing above it look identical from the outside, so a walk that folds case for *class*
names has to fold it for *package* names too, and should print how many steps it repaired.

**2026-11-14: the header's fourth word is exact in every payload, and the last four residue rows were a reader
defect.** `--sizefield` left four rows, all of them dwords that are multiples of 65,536 (262,144 / 3,932,160 /
7,798,784 / 11,665,408), which is a dword read two bytes late: the low u16 zero and the high u16 the size (4, 60, 119
and 178). The four payloads carry the export's name index as both the fifth and the sixth front value, and
`--sizefield` was still decoding the front for itself -- a loop that stopped on the first match -- while `locate()`,
which the disassembly half uses, reads six values and therefore already had the right name end. Reading the header
through `locate()` (which now returns `name_end` and the six front values) makes all four exact, so
`dword@name_end+12 == len(script) + sum(4 - width)` holds on **11,687 of 11,687 payloads** and the residue is empty;
`inventory/matching_baseline.csv` regenerates byte-identical. Nothing about where a script lives is open now: the start
is `name_end + 16`, the dword at `name_end + 12` is the script's size in memory, and the walk and the field check each
other on every payload the corpus has.

## U14. Scripted input reaches the guest; one list step, and a mission load, do not (PLAN 7.2, 7.3)

The title boots to its real five-item menu and the frame is capturable, but nothing sent at it from the harness has
changed what it shows. Measured on 2026-10-30, guest executing throughout (`EIP` in user space at every check):
`xdotool key Down` targeted at the window (`XSendEvent`, which SDL ignores), the same through XTEST, XTEST after
`xdotool windowfocus --sync` (the X input focus *is* the xemu window), and XTEST with
`[input] background_input_capture = true` in the run's TOML — in every case the highlight stayed on `Continue` and the
frame moved by no more than the animated background's noise floor (591 pixels in a 640x480 crop at `-fuzz 15%`,
measured with no input sent at all). XTEST `a`, the pad's A button, produced no transition either.

**The input path is found (2026-10-30, later the same day): QMP `sendkey`.** With a `usb-kbd` attached to player 1's
hub (`device_add usb-kbd,port=1.3.2,id=kbd` — the documented `port=1.3` is the hub itself and is refused), `sendkey`
injects into the guest's own keyboard and needs no window focus, no SDL and no X. Measured: `ret` took the title to
`SELECT PROFILE` (18,790 pixels changed), `y` (the pad's Y) to `QUICK SELECT MAP` (2,867–9,753), `esc` (the pad's B) back
out (13,872), while arrows moved nothing (0–246, the noise floor) and `tab` opened no console. Without the keyboard
attached the same `sendkey` changed 0 pixels and still reported success, which is the trap to remember. What remains is
narrower than before: get the map list's highlight onto a campaign map (`M20A`…`M21D` are listed in-game) and select it —
arrows and `tab` do not move it — and then a mission load is demonstrated.

**The list moves by page, and the step it cannot take is one entry (2026-10-31).** `home` → index 0, one `pgdn` →
index 10, `end` → the last entry (16,760 / 14,803 / 14,134 pixels in the list's own crop), while `down`/`up`,
`left`/`right`, `kp_8`, `kp_2`, `ins`, `del`, `spc` and `tab` changed nothing (0–351). The list is **41 entries, ten per
page**, campaign maps inside it — and `M20A` is **entry 31**, so `home` + 3 `pgdn` and `end` + 1 `pgup` both land on
30. The missing step is the d-pad (`GUIVertList.InternalOnXControllerEvent` maps `XC_PadUp`/`XC_PadDown` to
`Up()`/`Down()`), i.e. the virtual pad below, and that is the whole of what U14 still needs. `A` (`ret`) on the page
does not load the highlighted map (13 pixels over 65 s, guest executing, double-tap included): the page's
`LoadMap()` is behind `XC_A` with `bButtonA=True`, but the focused list consumes that event first
(`GUIVertList.InternalOnXControllerEvent` returns `true` after `OnClick`) — or `Travel` refuses the
`?Game=StargateGame.StoryModeGame` URL. Those are the two candidates, and a pad on port 1 (or an assert dialog) tells
them apart. The harness bug behind two sessions of this: an `import` grab taken right after `sendkey` returns the
*pre-key* frame (0 pixels, where the next grab differs by 18,790), so `drive.py` now grabs twice per asserting shot
(`--regrab`).

**The earlier routes, kept because they say why `xdotool` cannot be the answer.** (1) A virtual pad: `/dev/uinput` is writable by the desktop user and
`python-evdev` is installed, so a `UInput` device can be created (`sg1-vpad` was created on `/dev/input/event27`) —
SDL reads an evdev gamepad with no window focus, so this is the one path that does not depend on the compositor handing
focus to an ssh-started process. It needs an SDL mapping so xemu treats the device as a controller, plus
`[input] gamepad_mappings` and `[input.bindings] port1` in the run's TOML. (2) If the virtual pad also shows nothing,
the question moves to the guest: whether this build reads the pad at all before the first frame, which the same
harness answers by sampling what the guest does after a click that must have an effect (a mission load changes the
screen by whole frames, not by the noise floor).

The distinction to keep: what is missing is **input injection**, not a boot or a display. Pixels were the weak channel
until the noise floor was measured; `cpu=` (QMP `EIP` sampling) is what says the guest is alive in the same run.

**Closed 2026-11-03: the virtual pad, and a map that loads.** `tools/xemu/vpad.py` puts a uinput gamepad on port 1
before xemu starts and `drive.py --step=pad=` presses it; `pad=down` steps the list one entry and `pad=a` **loads the
highlighted map** (in-game HUD, no assert dialog, `docs/XEMU.md`). Three traps sat between "pad created" and "the guest
moves": the first identity reused the *physical* pad's SDL GUID, `BTN_DPAD_*` made SDL map the d-pad to buttons instead
of the hats, and xemu merges its base-path settings over `-config_path` so auto-bind handed port 1 to the physical pad and
pushed the virtual one to port 2. The arithmetic is settled too: the list advances exactly one entry per press, so `M00` is index 10 and
31 presses really are `M12F` -- the older page table had been read off scrolled windows, not indices. `M00` now loads
from `Continue` (a profile-carrying EEPROM, `drive.py --eeprom`), with the tutorial overlay and no assert dialog.

## U15. ~~The console's `Tab=Type` binding does not open a console~~ -- corrected twice: it opens, draws nothing, and
keeps no output

`System/DefUser.ini:50` binds `Tab=Type`, `Engine/Console.uc:205` is `exec function Type()`, and `Default.ini` sets
`Console=Engine.Console` -- so the build intends a console on Tab. Measured 2026-11-03: `tab`, `grave_accent` and `kp_7`
each leave the screen unchanged in live gameplay with a guest keyboard attached, while typed letters do reach a
chat-shaped line (frames in `~/sg1/work/xemu/seq8`, `seq10`, `seq12`; tables in `docs/XEMU.md`).

**What would settle it:** attach the USB keyboard before the guest enumerates `XID` (the current `device_add` lands ~75 s
into the boot), or trace the scancode the Xbox kernel delivers for `sendkey tab`. If the kernel never produces `IK_Tab`,
the console is unreachable from the emulator's keyboard by construction and 7.3 needs the scripted-action route instead
(`GamePlay/ACTION_ConsoleCommand.uc` exists and runs console commands from level script).

**Corrected 2026-11-03, after the measurement in `docs/XEMU.md`.** The console does open on `Tab` and it does run what is
typed: `stat fps` produced a visible `60 cur FPS` / `59 avg FPS` overlay in two runs, one with the keyboard attached
during the boot and one after it. What is missing is the *window* -- the console draws nothing, and `bIgnoreKeys` swallows
the first keypress, so a command sent cold loses its first character. The open question is now output: a text-only
command leaves no on-screen trace.

**There is no scrollback to read, and the field this entry named does not exist (measured 2026-11-05).** All 18 packages
ship their `ScriptText`, so the classes are readable without a boot (`tools/pkg-script-text.py <dump>/System/<pkg>.u
--all --write $WORK/scripts`; 2,318 classes over the 18 packages):

| class | what it declares | configured? |
|---|---|---|
| `Engine.Console` (`Console.uc`) | `var string TypedStr, History[16];` -- no output field at all | **yes**: `Default.ini` sets `Console=Engine.Console` |
| `Engine.WindowConsole` | `var int Scrollback, NumLines, TopLine, TextLines;` -- its `Scrollback` is a **count**, not text | no |
| `GUI.ExtendedConsole` | `var array<string> Scrollback;` -- the only real scrollback buffer in the tree | no; it needs the disc rebuilt |

So "the console's scrollback is in guest memory (`Console.Scrollback`)" was wrong twice: `Console` has no such field, and
the only `array<string> Scrollback` belongs to a class this build never instantiates. **What is readable is the command
line**: `History[16]` holds the last sixteen commands, ring-buffered on `Enter` in `KeyType` via `HistoryTop`/`HistoryBot`,
so a memory channel can prove *a command was delivered* and never what it printed. For output, the usable assertions stay
the ones already measured: `stat fps`'s overlay (`docs/XEMU.md`) and `GamePlay/ACTION_ConsoleCommand.uc`, which runs
console commands from level script.

**The instrument that did not finish the read, and the route that would.** Scanning guest RAM for the `Console` class
table (`0x0050cb80`, 1088 bytes, `inventory/xbe_class_table_names.csv`) as a dword finds the constructor's own immediate
twice, at `0x00153bbc` and `0x00153c5f` -- both inside `.text`, because the store that installs the table is
`mov [ecx],0x50cb80`. Instances live **above the loaded image end** (`0x7d95e0` for this XBE), so the scan has to start
there; a 1 MiB-chunk sweep that begins below it aborts on an unmapped page (`read 0x7cc000+0x4000 failed`). The cheaper
route is `GObjObjects`: UE2 keeps every `UObject*` in one global array, and walking it would name the `Console` instance
in a few hundred reads instead of a 120 MiB sweep -- nothing in this tree has located that array yet.

**The object array is located, so the read has an instrument (measured 2026-11-06).** `UObject::GObjObjects` is at
`0x006c21b0` — 42,152 objects at the title — and `tools/xemu/objects.py` walks it into a class histogram
(`docs/XEMU.md`). That settles the route this entry called "the cheaper": objects are addressable by class without
a 120 MiB sweep, and a regression run can ask for a `PlayerController` or a `Level` by name. It does **not** settle
the `History[16]` read: at the title the only object on the `Console` class table holds a `defaultproperties` block,
which reads as the class default object, so the live console appears with a level. The remaining measurement is one
in-game run — `Tab`, a command, `Enter` — then `--class Console --strings` on the instance that run creates.

**The census names the master's slots, and the frame source failed the run (measured 2026-11-09).** `hudsay6` left one
question -- the client route reaches a `Console` that is the class default, so is there a *live* one anywhere? -- and
the answer needs a boot whose receipt runs. The instrument for it is now a census rather than a first-match screen:
`tools/xemu/hud-text.py`'s `master_slots()` reads every object pointer in the `InteractionMaster`'s block and prints
`offset -> target, class, name`, marking a target whose `Outer` is a `Package` (a class default) and one that sits on
the `Console` table; `consoles_from_client()` offers only non-default targets as a console, so a block holding the
default *and* a live console answers with the live one, and a block holding only the default says what it holds and
falls back to the array walk. Both shapes are in `--selftest`. The boot that would ask it did not get there: in
`hudsay7` the level loaded and the reader held the `GameHud` and its dialogue, while every frame from the map select
on was a uniform `(13,13,13)` (`02-mission.png` black 0.9965) and `05-precommands` could not be captured at all, so
`drive.py` exited 3 and killed the guest before the first keystroke; `hudsay8` reproduced the blank frames with one
visible xemu window throughout, so the X window is not the fix. **What would settle U15 now:** a run whose frames come
from QMP `screendump` (the emulated framebuffer, not `import -window`) -- or one with no post-load shots at all -- so
the census is reached and `History` is read.

**The read instrument exists and the three input traps are measured (2026-11-06).** `objects.py --fstrings` resolves
a UE2 `string` as the `(Data, Count)` pair it is, so a console's `History[16]` is readable once an instance exists
(validated on a live `PlayerProfile`: 21 pairs, 6 with text). Three negatives stand between that and the read, and
none of them is the console: a keyboard attached *after* the level loads is never read (0 px on `Tab`, `space`,
`stat fps`, `Return`); attached at 75 s the keys do arrive but a select press can be swallowed, and `drive.py` has no
per-press retry; and a killed `drive.py` leaves its `SG1 virtual pad` uinput device behind, which the next xemu binds
while every `pad=` step silently does nothing. What remains is one clean run with a single pad and the keyboard early,
then `--class Console --fstrings` on the instance — not another measurement of the console.

**The output channel is chosen by the HUD class, not by the console (measured 2026-11-06).** Six commands in one boot
(`god`, `say hello`, `enablecheats`, `god`, `addnotification hello`, `stat fps`) all reached `ConsoleCommand` —
`console-state.py` reads six populated `History[16]` slots — and only `stat fps` drew (`64 cur FPS`/`60 avg FPS` in a
2x crop). The reason is in the script text, not in the console: `StargateHUD` overrides both `Engine.HUD.DrawHUD` and
`Engine.HUD.DisplayMessages`, its `Message` has the engine's `AddTextMessage` call **commented out**, and it routes
`Say`/`TeamSay` to `SetSubtitle(Msg, 2.0, ...)` and every other type to `AddNotification` ->
`StargatePlayer.NotificationManager` -> `GameHud.SetNotifications`, a no-op unless the manager and a `GameHUD` both
exist. `Engine.HUD.TextMessages[8]` — the one message buffer an `Engine.Console` build would fill — is therefore never
filled on this disc, and the drawn channel is the game's own two-second subtitle. A frame 3 s after the submit cannot
see it, and the run's dump holds no stored copy of any of the three message strings. **What would settle it now:**
one boot whose per-command frame is taken *one second* after the submit (the tool does that since this run), plus a
read of `StargatePlayer.NotificationManager` and `GameHUDPtr` — no new external data, one game run.

**A submit delivers, and the receipt is `History[0]` plus an empty `TypedStr` (measured 2026-11-06).** `ret` reads
`Console.TypedStr` (`0x02661b00 +0x44`) at the `FString` assignment `EIP=0x1b300`, whose destination is a fresh heap
buffer, and the state it leaves is the receipt: three seconds later `TypedStr` is `0 0 0` and `History[0]` at `+0x50`
holds `0x07fde61c 6/6 ' hist'`. `History` is `var string History[16]` (`Console.uc:10`) — a static array of 16 inline
12-byte `FString` structs ending at `+0x110`, where `TypedStrPos` reads 5 -> 0 and `bTyping` 1 -> 0 — so
`tools/xemu/console-state.py --cr3 0xf000 --receipt TEXT` reads the slots by offset and exits 0 only when the text is
in a slot *and* the line is empty; the same dump taken before the submit exits 1. The earlier reading, "the trap is
the write", was the pre-execution interpretation of the same register file, and a dump taken at a watch is a
pre-submit snapshot because the guest resumes inside the same second. **What remains:** the console's output has no
sink at all (`Scrollback` belongs to `ExtendedConsole`, which this build never instantiates), and
`kp_enter`/`pad=start` were sent after a submit had already closed the console (`bTyping` 0) and remain untested.
`pad=a` is measured: with the line re-typed and dumped before and after the press, the A button changed neither
`TypedStr` nor `History`, so the pad has no console submit and a scripted console needs the keyboard. **`kp_enter` is measured now, from an open console (2026-11-06).** `submit7` types the line, dumps,
sends `kp_enter`, waits three seconds and dumps again: `TypedStr` and its `Data` pointer, the `History` slots and the
three counters are unchanged across the key, so the keypad Enter reaches no branch of `Console` (— it is not the
`IK_Enter` submit, and not even the `Escape` clear). **What remains** is the output question, which is structural:
`Scrollback` belongs to `ExtendedConsole`, which this build never instantiates.

 **`pad=start` is measured, and the console answers to the keyboard alone (2026-11-06).** `submit8` runs the
two-round shape with the `wait=3` before the Start dump: round 1 `ret` submits and moves `HistoryTop` 0 -> 1,
`HistoryBot` -1 -> 0 and `HistoryCur` 0 -> 1 (the counter map's first measured increment), and round 2 types the line
afresh into the console one `Tab` re-opens and taps Start. Every word of the object (DASH `TypedStr` and its `Data`,
`History[0]`, the three counters, `TypedStrPos`, `bTyping`) — is identical afterwards while the frame moved 813 px.
Start reaches no branch of `Console`, so together with `pad=a` no button on the pad drives it: a scripted console has to
be typed into from the guest keyboard attached before the title.

**A loaded level still has no live `Console`, and `Tab` was the variable, not the level (measured 2026-11-06).** With
the keyboard attached at 75 s and a campaign level up (`48,647 objects over 295 pages`, `StaticMeshActor` 927, the M00
census), `objects.py --walk 0x6c21b0 --class Console --fstrings --object-size 0x800` resolves **one** object on the
`Console` class table, `0x02344b00`, and its window is the class's `defaultproperties` block with **1 FString pair,
0 carrying text**. In the same session `Tab` drew **0 px** and `console-line.py` counted **0 cyan pixels**: no console
was open, so the walk measured the class default object again and the level was never the missing piece. The read is
now conditional on a positive prompt frame, which costs one screenshot instead of a 2m21s walk. What is still not
measured is a `History` entry — and since the console only exists while a `Tab` lands, the scripted route
(`GamePlay/ACTION_ConsoleCommand.uc`) remains the way to get a command in without one.

**Corrected 2026-11-06: the "cyan HUD glyph" was an empty prompt, and `ret` submits.** The frame this entry called a
false positive (`submit1/05-typed`, 56 cyan px in a 64x32 box) is `(>_` -- the console's empty input line with its
caret, read at 4x -- not a HUD glyph; the console was open and empty, which is a *positive* read. `console-line.py` now
separates `absent` / `prompt` (50--64 px wide) / `typed` (176 px stock, 210 px for `GUI.ExtendedConsole`) by the
trimmed extent, because the count alone also called the title screen (22 px over 554x246) and a menu button glyph
(87 px in 18x8) `present`. And the submit is not missing: in `conhist9` the frame after `ret` holds **no input line**
where `kp_enter` left `(> stat fps_` drawn, which is `TypingClose()` from the `IK_Enter` branch; `submit1`'s `ret`
frame is byte-identical to the one before it, so the same key did not arrive there. What remains unmeasured is the
command's *effect* -- no FPS counter draws in `conhist9/07-ret` -- and the console object's `History`, which the class
filter has never resolved (only the class default object, in every session). `docs/XEMU.md` "The console's submit is
`ret`" carries the frames, the extents and the shot-by-shot diff counts.

**Corrected 2026-11-06: the console is not in the object array once it closes.** With `M00` loaded and one `ret`
sent, the walk finds 45,802 live objects over 295 pages, exactly one `PlayerController` (`0x01c31000`) and **no
pointer to a `Console` anywhere in its first 0x800 bytes** -- although `Engine.Player` declares
`Console,UObjectProperty,0x2000:Transient;` (`inventory/class_properties.csv`) and `--class Console` returns only the
class default at `0x02344b00`. The frame after `ret` is `absent` (0 cyan px against `05-typed`'s 109), so the console
had closed: `TypingClose()` takes the object with it, and every walk that reported "only the class default object"
was read minutes after the prompt had gone. **What would settle the `History` read now is a sequence that ends with
the console open** (a non-character submit key, or no submit), then `objects.py --object <controller> --refs` to get
the live `Console` and `--object <console> --fstrings` for its `TypedStr`/`History` pairs -- both read in seconds,
where the array walk under them costs 3 m 29 s. Keys cannot be injected after the driver exits (a QMP client on the
run's `qmp.sock` times out waiting for the greeting), so the sequence itself has to end in that state.

**Corrected again 2026-11-06, with the prompt open: the drawn line is not a `Console` instance, and the boot is
not the swap.** The disc was identified from its own `System/Default.ini` (`tools/xemu/xiso-cat.py`): the booted
`$WORK/xemu/sg1.iso` is stock (`Console=Engine.Console`, md5 `109fff2a...`), and the swap image
`$WORK/console-iso/game.iso` is the `GUI.ExtendedConsole` one -- a `grep` for that string hits the stock image too
(a stray copy at offset 1,881,354,812), so only reading the file settles it. On that stock disc, with `hist` typed and
the crop reading `(> hist_` (82 cyan px, 128x32) and `stat fps`'s counter above it, a walk of `0x6c21b0` still finds
45,802 objects and exactly **one** `Console`, the class default at `0x02344b00`, with no FString carrying text, and no
`ExtendedConsole` at all. **What would settle the object question now** is the console default's interaction block:
`0x02344b00` points at `InteractionMaster` `0x02406720`, whose `+0x028`..`+0x034` name `XboxClient`, `GUIController`,
`Console` (the class default) and `UIXInteraction` -- one `objects.py --object 0x2406720 --refs --object-size 0x200`
turns that block from four addresses into the list the drawing object belongs to. The `Player` arm is closed for the
controller: its first 0xc00 bytes hold no `Console` field, in either prompt state.

**Measured 2026-11-06: the interaction block is four pointers, and none of them carries the text.** With the prompt
open on the stock disc (`hist` typed, 60 cyan px in a 110x32 band, no submit), `objects.py --object 0x2406720 --refs
--object-size 0x200` names the master's block in one read -- `XboxClient` `0x01fb9bc0`, `GUIController` `0x02345500`,
`Console` `0x02344b00`, `UIXInteraction` `0x05b10a80` -- and `--fstrings` over each window finds **0, 0, 0 and 0**
pairs carrying text (1, 4, 8 and 11 slots). The `Console` slot is the class default's `defaultproperties` window, so
the master holds no live console; and since the typed string is in none of its children, live (`XboxClient`,
`UIXInteraction`) or default (`Console`, `GUIController`), this block cannot yield `History` -- and it is not the
object that draws the line either, so "in that list" is now a negative rather than a lead. The console default's own
fields are the stable part and cost seconds: `XboxViewport` `0x022fe200` at `+0x02c` (itself a defaults window -- two
pointers in 0x400, no console field) and `InteractionMaster` `0x02406720` at `+0x030`. **What would settle where the
text lives** is a bounded scan for its payload: the heap the walk covers is `0x970000..0x7eb0000`, 295 pages of
64 KiB, so a two-byte `hist` search over just those pages is ~470 reads (2--3 min, no new external data), and the
buffer it finds is the FString `Data` pointer whose owner is then one window lookup away. One caveat measured the
same session: fifteen minutes into a linger, two of these addresses answered all zeros while the emulator still ran
-- a failed stub reply padded to zero, not a freed object, which is why `--object` now warns on an all-zero window.

**The `Player` arm is closed too: the live player is `XboxClient : Client`, and that chain declares no `Console`
(measured 2026-11-08).** With `M00` loaded and the prompt open (`console-line.py`: `prompt`, 67 cyan px, `95x32` at
`+34+209`), `fields.py --object 0x01fb9bc0 --prop-name Console` prints the chain `XboxClient` `0x00dcea00` ->
`Client` `0x00d40600` -> `Object` `0x00bcf000` and answers *0 declared `Console` properties ... no property named
`Console` in the chain*. `fields.py --object 0x00d40600 --children` shows what `Client` does declare: 32 objects, array
indices 308--339, `ETextureDetail` through `DrawDistanceLOD` -- viewport and renderer settings only, with no `Console`,
`GUIController`, `InteractionMaster` or `LocalInteractions` among them. The row this entry leaned on,
`inventory/class_properties.csv:2907`, is a *script* class whose transient fields all carry `0x2000:Transient`, which is
why no offset is serialized for any of them; the native `Client`'s constructor wires them instead, and the live window
does hold `InteractionMaster` `0x02406720` at `+0x0c0`. So `--prop-name` cannot reach the console from any object on
this build. **What would settle where the live console is** is a diff against the class default
(`fields.py --object 0x01fb9bc0 --compare`): the transient slot is null in the default and set in the instance, so the
differing dword names it without a property chain -- then `--fstrings` on that pointer, in a sequence that types after
one spent key (`bIgnoreKeys` swallowed this run's four characters, which is why the prompt read empty and the `FString`
pairs carried nothing).

**The object pages do not hold it (measured 2026-11-06).** The bounded scan proposed above was run with the
prompt open and `hist` typed and unsubmitted: the 295 64 KiB pages `GObjObjects`' pointers cover (18,880 KiB) were
searched in 2.5 min for the UTF-16LE payload *with* its NUL terminator and returned **0 hits** over 18,624 KiB read,
with four pages (256 KiB) unreadable after four attempts each (`docs/XEMU.md`, "The typed command is not in the
pages the object array covers"). The read rate is what decides the shape of the next attempt: the stub answers
130 KiB/s at one 64 KiB page per request and refuses a `0x20000` request outright, so the 123 MB span is ~16 min of
stub time, and a whole-RAM `pmemsave` has to happen *inside* a boot -- a second client on the run's `qmp.sock`
connects and never gets a greeting. What is still open is where the buffer is allocated: outside the pages the
objects live in, in a form this scan did not search (an 8-bit `char*` copy), or on one of the four unread pages.

**The whole-RAM dump finds the text, and nothing in the image points at it (measured 2026-11-06).** The bounded
scan above was replaced by the cheap route: `pmemsave 0 0x8000000` as a `drive.py` step writes all 128 MiB inside
the boot, and `tools/xemu/ramscan.py` searches it in 0.1 s instead of 17 minutes. With the console drawn
(`console-line.py` reads `typed`, 95 cyan pixels) there is **exactly one** copy of UTF-16 `hist` plus its
terminator, at `0x06823b7a`, inside the string `" hist"` that starts at `0x06823b78`; the control boot, whose pad
press never took and whose frame reads `absent`, has **0** hits, and the 8-bit form has 0 hits in the positive
image, so this is the console's own UTF-16 buffer and not a `char*` echo. What the image does **not** contain is a
pointer to it: no 4-byte-aligned dword names the match or any even address in the 0x40 before it, and no class
table sits within 0x8000 before the bytes, so the buffer is neither reachable by a stored pointer nor a field of
any object the vtable walk can name. **What would settle the owner:** the register file at the same step
(`--step=monitor=info registers`, or a GDB stack read there), because a `Data` pointer absent from 128 MiB of RAM
can only be live in a register or in the stack frame of whatever draws the line -- and `pmemsave` covers neither. **A whole-image census says the same about the object**: the `Console` class table occurs exactly twice in all 128 MiB -- a `mov` that mentions it at `0x00291bbc` and the object at `0x02661b00` whose fields run into the class-defaults text -- so there is no live console anywhere in the image, only its default (`docs/XEMU.md`, "two mentions of `Console`").

**Resolved 2026-11-06: the buffer has an owner, and the earlier search had the wrong address space.** `pmemsave`
writes *physical* memory while every address in this entry is a *linear* one, and the page tables travel in the dump
(`CR3=0x0000f000`), so `tools/xemu/vamap.py` translates between the two. The typed line at physical `0x06bfcb78` is
linear **`0x0802db78`**, and that value occurs exactly once in 131,072 KiB: at `+0x44` of the object at `0x02661b00`,
whose first word is the `Console` class table — an `FString` of `Count=6`, `Max=6` (`" hist"`), with `History[16]`
behind it still all zero. The earlier dump reproduces it at the same object offset, with its heap `0x3d9000` away, so
the owner is not an artefact of one boot. "Nothing in 128 MiB names it" was true of the physical value and false of
the string's address; the read that is still missing is not a pointer question: `Engine.Console.KeyType` fills
`History` only on a submit, so one run that submits (`ret` is the key that closes the console and runs the command,
measured) and one `objects.py --object 0x2661b00 --fstrings` would read it. The register file at the dump step was
measured too and names nothing: `CPL=0`, `EIP=0x00020669`, `ESP=0xd0169d38` — a ring-0 frame on a kernel stack, and
a `pmemsave` covers that stack anyway.

**The line's owner is measurable in one boot; the reader is not (measured 2026-11-06).** `drive.py --step=watch=hist
--watch-key=x` resolves every copy of the text to its `FString` field (`ramscan.string_copies`), translates the winner
with `vamap.py`, arms `Z3` on its *linear* address and sends one key so that something reads it. On `findtext8` the line
is `Console.TypedStr` at `+0x44` of `0x02661b00`, `Count/Max 6/6`, and the watchpoint's three stops are one instruction,
`FUN_0001b2c0+0x40`, the `rep movsl` of an `FString` assignment copying `12` bytes *into* the line from `line+0xC`.
Settled: the console keeps the typed command where its class says it does, and a keystroke rewrites it in place
(`Data` unchanged, `Max` 6, no realloc). Open: the instruction that *reads* it — this stub accepts `Z3` but traps the
store, so **what would settle it** is the same run with `--watch-key=ret`, whose submit copies `TypedStr` into
`History[HistoryTop]` before it clears the line; if that stops only on stores too, the read channel is a breakpoint in
the interpreter's dispatch rather than a data watchpoint.

**The command list is settled, so "the console did not run it" is no longer a guess (measured 2026-11-06).**
`tools/console-commands.py` joins the `exec function` declarations in the shipped text to the binary's `Exec`
FunctionFlag: **336 declarations over 37 class/state scopes, 292 names, and all 336 also carry the flag** -- plus 125
`Exec` functions with no declaration, 122 of them state-scoped (the binary names the *state* in its `class` column).
`stat`, `open` and `cheats` appear in neither source, so they are engine handlers: `stat fps` drawing and `stat game`
not drawing is the handler's business, not delivery. What remains for output is the route this entry's own table left
out -- `PlayerController.ClientMessage` and `Say`/`TeamSay` reach `HUD.Message`, which draws, and `CheatManager`'s
refusal messages go through it -- so **a command that messages the client draws a readable line even though
`Engine.Console` has no `PostRender`**. `tools/xemu/console-commands.sh` types a list of commands in one boot and
photographs each; it has not yet produced a reading (two boots lost to input on a busy desktop, `docs/XEMU.md`).

**The player object was there and the census called it code (measured 2026-11-06).** A mission boot that submitted
`say hello`, `addnotification hello` and `stat fps` drew nothing for the first two even with the frame one second after
the submit (`docs/XEMU.md`, `~/sg1/work/msg1`), and the dump could not be asked why: `ramscan --objects --class
StargatePlayer` answered six hits and read every one as `not-object`, although the sixth is the player --
`0x4d0b68 0xbb0d 0x7e57640 0x9335d60 0 0xffffffff` at physical `0x05aaf800`. The screen had two guessed bounds and
both fail on a loaded level: the `ObjectInternalInteger` cap (`0x10000`, measured `0xbb0d`) and an "in-image"
range test on the two words after it (measured `0x09335d60`, above the 128 MiB dump). With the screen on the measured
pair (`+0x10` zero then `0xffffffff`) the object is named, its pointer fields decode to `Pawn`,
`PlayerReplicationInfo`, `AIMsgManager`, `StargateHUD` and `XboxViewport`, and `--refs --cr3` names the `History`
slots that own the typed line. **What still blocks the drawing half**: `SetSubtitle` and `AddNotification` both end in
a `GameHud` actor (`StargatePlayer.GameHudPtr`, `NotificationManager`), and `GameHud` is a script class with **no XBE
class table**, so the 492-table census cannot see one. **What would settle it**: a package-class-table resolver (the
tables a `.u` package's classes get at load, which no artifact enumerates today), plus a `pmemsave` taken at the
`ret` that submits `say hello` rather than ten seconds later -- `M00`'s own dialogue rewrites `SubTitleText`, so the
absence of `O'Neill: hello` from a late dump is not evidence either way.

**The class-table half is answered, and the answer is that no such table exists (measured 2026-11-07).** The
resolver this entry asked for would have enumerated "the tables a `.u` package's classes get at load"; there are
none. `tools/xemu/ramscan.py --headers` groups every object-shaped header in a 128 MiB dump by its first word:
3,820 of 11,275 sit on one of the inventory's 492 tables (127 distinct), and of the 5,485 distinct words it does not
know **none** names a block with six or more of its first eight slots inside `.text` -- so `GameHud extends
Interaction` (a plain script class, no `native`) carries `Interaction`'s table, and a class is simply not a thing an
object carries. `ramscan.py --fields` then reads the object instead, translating each field through the page tables,
and on `msg1`'s dump the player names ten objects over seven classes (`Package`, `Pawn` x4, `ScriptedController`,
`PlayerReplicationInfo`, `AIMsgManager`, `XboxViewport`, `StargateHUD` at `+0x748`) with **no `Interaction` among
them**: `GameHudPtr` and `NotificationManager` are `none`, so `Say` and `AddNotification` had no sink to reach and
the frame's timing was never the question. The 19 objects on the `Interaction` table are 52 bytes each (the class's
`object_size`) against `GameHud`'s 106 properties, so none of them is the HUD actor either. **What remains** is
where a `GameHud` is supposed to come from: repeat `--fields` after a checkpoint load rather than a
`QUICK SELECT MAP` entry, since `areCheatsEnabled()` already gates on `Level.Game.CurrentGameProfile`, and if the
pointer stays `none` the question moves to `InteractionMaster`, which the player's first `Interaction` does name.
**The route is read, and the profile is not in it (measured 2026-11-07).** `make interaction-setup`
(`inventory/interaction_setup.csv`) lists every `AddInteraction` call site in the shipped scripts: **7 sites over 5
interaction classes, all in `StargateGame`**. `GameHudPtr` is written in two of them, both a routine named
`SetupInteractionsFor` (`StoryModeGame`, `StargateDeathMatch`) — and each of those also installs `CommandInterface`,
creates the `NotificationManager` and calls `UpdateWeaponMenu`/`UpdateSquadMenu`, which install `WeaponMenu` and
`SquadMenu`. The five unset player fields are therefore **one unit**: `none` for one predicts `none` for the other
four.

The chain has exactly one engine-side entry: `StargateHUD.PostNetHUDSetup` resolves the game class from
`GameReplicationInfo.GameClass` and calls `SetupHUDFor` -> `SetupInteractionsFor`; its only caller is
`StargateHUD.Timer`, armed by `SetTimer(1.0, True)` in `StargateHUD.PostNetBeginPlay`. Nothing in that chain reads
`CurrentGameProfile` — the profile is read by `GameInfo.InitGame` (from the travel URL's option, and dropped unless
the profile is a ladder game) and by `areCheatsEnabled` in `CheatManager`, which only decides whether a refusal is
messaged to the client. **So the checkpoint load this entry asked for would not have discriminated**, and the
measurement it named is retired.

What remains is whether the HUD's `PostNetBeginPlay` route ran at all. The `--fields` walk on the HUD (`0x05bc6a00`)
names 8 fields over 7 classes and neither object the two routines spawn (`MyFont`, `SGInsetCamera`), which is
consistent with "it did not" but is not proof: a pointer is named only when its target carries an inventory table.
**What would settle it** is one breakpoint: `UInteractionMaster::AddInteraction` is reached only from these seven
sites, so a `drive.py --watch` on it during an `M00` load either fires (the setup ran and the class string did not
resolve) or never does (the route is dead in this boot, and no map will show those five fields). Reading
`bPostHudSetupCompleted` off the HUD instead needs a property offset the dump does not carry.

**The breakpoint this entry asks for has to be a widened pattern (measured 2026-11-07).** The first attempt armed the
package's own bytes and found **0 copies** of all five functions in a 128 MiB dump — and a second dump, from a boot
that *did* load `M00`, also holds no 8-byte prefix of any of them, so that zero was never the failed boot it was
blamed on. The guest's copy widens **every compact index operand to a four-byte slot** (`b8 2a` in
`StargateHUD.DrawOrderTarget` is `a0 1e 26 01` in the guest; its `07 cd 00` jump is unchanged), so
`tools/uscript-dis.py --hex-bytes` emits `??` where an index was and `drive.py --step=brk=` matches it as any byte.
On that model `Timer`, `PostNetBeginPlay` and `DrawOrderTarget` each occur exactly once in the loaded dump
(`0x01573140`, `0x017d8dc0`, `0x019bf600`). What would still settle the route: the same boot with those patterns and
`drive.py --brk-freeze` (the VM stopped across the dump and the arm), then `Timer` firing or not.

**That breakpoint would read the loader, not the route (measured 2026-11-07), so it cannot settle this entry.** With
the parser fixed, one `M00` boot armed the five HUD-setup functions plus a `DrawHUD` control and every arm fired --
**all six from one reader**, `eip 0x0003990f` (`+0x2f` into the slot-11 walker `0x000398e0`), identical `esp`, same
return address `0x00039420`, inside 6.2 s. That is a loop over a class's functions during the load (the serialiser
validating scripts), not the interpreter reaching them in call order; `DrawHUD` is read from `0x00040900` instead,
which is what makes the two readers distinguishable. **What would settle it** is an effect of the route rather than a
byte the loader also reads: a breakpoint on `UInteractionMaster::AddInteraction` (only the seven `AddInteraction`
sites reach it), an exec breakpoint filtered on the reader's `eip`, or the HUD-side property the setup writes, whose
offset the package does not carry.

**The first half of that is now measured: both readers of the route are load passes (2026-11-07).** Keeping the
read watchpoints armed shows each of the five functions plus the second `PostNetBeginPlay` copy read exactly twice,
0.1 s apart, by `0x0003990f` and `0x00058ad4`, in the walker's order -- `AddInteraction` before `Timer` and before
`SetupInteractionsFor`, the reverse of the call chain. The wait was cut at the stop cap, so whether the route runs
*after* the load is still open: the same arms with `--brk-stops=40` either show a third read (the interpreter's
`0x48ce0`/`0x48ce8` pair, which `DrawHUD` hits about five times a second) or show none through gameplay, and the
latter is the cause of the five `none` fields this entry is about.

**Answered, and the route runs: five of the six armed copies are read a third time by the interpreter (2026-11-07).**
With `--brk-stops=40` the two load passes repeat and then, 2-3 s after them, **five of the six copies** are read from
`0x00048ce0`/`0x00048ce8` -- inside `UObject::ProcessInternal` (`0x00048bf0`), the same pair that reads `DrawHUD`
every frame -- so the five HUD-setup functions do execute (`SetupInteractionsFor` t+17.8 s, `AddInteraction` t+18.0 s
and twenty-two more reads, eleven alternating pairs, by t+20.4 s, `PostNetBeginPlay` second copy t+18.2 s, `Timer`
t+18.9 s, `PostNetHUDSetup` t+19.0 s). The sixth, the first `PostNetBeginPlay` copy, spent its reads on three load
passes and is a fact about that copy rather than a miss. Two corrections follow. **A `Z3` read watchpoint on a script
function *can* witness execution**, contrary to the previous paragraph, as long as the reader `eip` is checked:
`ProcessInternal` is distinguishable from the loader's walker.
And **the documented chain is still not the order that runs** (`SetupInteractionsFor` and `AddInteraction` execute
before `Timer` and `PostNetHUDSetup`), so the armed copies are not all the HUD's own functions -- the same five names
exist in more than one class. The five `none` player fields are therefore an *effect* question, not a dead-route one:
whether `SetupInteractionsFor`'s write reached `GameHudPtr` when it executed. **What would settle it** is (a) which
export owns each armed copy -- a dump-side walk of the object array for a `UFunction` whose script pointer is the
copy's linear address, no boot needed -- and (b) a `--fields` read of `GameHudPtr` from a dump taken *after* t+19 s of
an `M00` load, where the route has demonstrably run. **The declaration is an object outered to the class, so the field read has a route (measured 2026-11-08).** The class
object's `+0x24` is the *metaclass*, so `fields.py --object <class object> --prop-name X` was reading `Class ->
State -> Struct -> Field -> Object`; a class object is now read as its own struct and prints the real chain
(`StargatePlayer` -> `UnrealPlayer` -> `PlayerController` -> `Controller` -> `Actor` -> `Object`). That chain holds
**no** `GameHudPtr` dword in the class object's bytes even though `inventory/class_properties.csv` says the class
declares one, so a declaration is a `UProperty` object in `GObjObjects` whose `Outer` is the class -- `--prop-name`
now answers from one `Outer` pass and takes a comma-separated list. **What would settle it:** one gameplay boot with
`--object 0x00e47800 --instances --prop-name GameHudPtr,commandInterfacePtr,NotificationManager,Pawn`, one array pass
for the four slots on the row whose own `Class` is `StargatePlayer`. Detail: `docs/XEMU.md`.


**The copy's owner is a loader record, not an object field (measured 2026-11-07).** `tools/xemu/owner.py` walks
`GObjObjects` in a dump and looks for the dword that names an armed copy: 9,403 objects reached in `brk6`'s 128 MiB
(27,444 slots hold pointers that do not translate), `Outer` measured at header `+0x18` (it names a `Class` for 300 of
300 sampled `Function` objects), and **none of the six copies is a field of any reached object** -- each is the third
word of a 24-byte loader record, with a kind, an index, two lengths and an offset around it (`docs/XEMU.md`). So
"which export owns this copy" is not answered by finding the owning object, and the copy is not addressable as a
function's own code pointer from the array either. A `UObject`'s `Name` is an `FName` index, and this dump has no
name table to resolve it with: no `(Data, Num, Max)` triple over the whole 128 MiB parses as a name-pointer array,
and no dword names the UTF-16 class-name strings in it. **What would settle it:** locate `GNames` in a live guest --
a `.data` shape scan like `objects.py --find`, validated by resolving the `Name` of an object whose class the class
tables already name -- then a record's index names the function and an object's `Name` names its class. Until then
U15's five `none` player fields stay unexplained at the class level.

**`GNames` is located, and a record's index names its function (measured 2026-11-07).** The name array is
`0x006be0d8` in a live guest -- `data=0x01f30000 num=34,800 max=37,732`, positional (slot 0 `None`, 1
`ByteProperty`, 2 `IntProperty`, 100 `Begin`), immediately after the `Initialized` dword at `0x006be0c8` -- and
`tools/xemu/names.py` reads it. So the loader record's `(kind, index)` pair resolves: the seven measured copies come
back as `Timer` (308), `PostNetBeginPlay` (2014), `PostNetHUDSetup` (17040), `SetupInteractionsFor` (2757),
`AddInteraction` (6823) and `DrawHUD` (2427), plus the instructive one -- index 357 names **`PostBeginPlay`**, not
`PostNetBeginPlay`, so the second copy of that pattern is a different function with the same bytecode shape. A
copy's identity is measured now, not inferred. The shape scan itself is a **negative**: 4,496 `(Data, Num, Max)`
triples in `.data` pass the shape and only `GObjObjects` resolves to a real pointer array, because 21 of the name
array's first 64 slot pointers fail to read back (`odd hex length`) and a pointer-array prefilter counts them as
nulls. **The object side is measured too (2026-11-07): `UObject::Name` is `+0x20` and `UObject::Class` `+0x24`.**
`names.py --at 0x006be0d8 --verify` finds the class pointer without assuming a layout -- a `UClass` object's own
vtable is the `Class` table, so the header dword pointing at one *is* `Class`, and `+0x24` does that for 400 of 400
sampled objects -- then reads `Name` as the class object's dword that resolves, through `GNames`, to the class name
the instance's vtable already carries (`+0x20`, 260 of 400 objects with a named vtable, no disagreement; `Outer` stays
`+0x18`). What that buys U15 is the class-level read the entry asked for and the vtable route cannot give:
`GameHud extends Interaction` carries `Interaction`'s table, so no table names a *script* class, while every object's
`Class` pointer reaches a `UClass` object whose `Name` is that script class. `names.py --census` is that walk
(class histogram, `--grep` for one name); it has not been run over a full array yet, so the five `none` player fields
still need one census against a boot that has run the HUD route, and the effect question in the paragraph above is
unchanged.

**The census says the channel is set: `GameHud` and `NotificationManager` exist, above 128 MiB (measured 2026-11-07).**
One loaded-`M00` boot (`census1`) walked with `names.py --census` against a live stub reads `48,646` array entries into
**371** named classes -- and 2,835 of those entries are at or above `0x08000000`, the physical bound every dump-based
read in this project uses. The player's missing objects are all in that top slice: `GameHud` **`0x09332100`** (its class
default is `0x0682ab00`), `NotificationManager` **`0x096dc340`**, `PlayerProfile` `0x08a75040`, `PlayerStats`
`0x0977b900`, `SGPlayerInput` `0x09675500`, `StargateTeamHUD` `0x08af0a00`, and the 10 `TeamPlayerReplicationInfo`.

So the earlier reading -- "the player names no `GameHud`, so `GameHudPtr` is `none` in this state" -- is **the bound, not
the state**: that `--fields` walk ran over the 128 MiB `pmemsave`, and these objects are outside it. `AddNotification`
and `SetSubtitle` had a sink on this level. What remains open is the draw path (`GameHud.SetNotifications`) and the
frame, not an absent object.

Two smaller results: `SGInteraction` has **32** objects here, against the 19 an `Interaction` vtable count reports
(a `Class`-pointer read is the only one that can tell them apart), and the live HUD object is `StargateTeamHUD`, not the
`StargateHUD` an earlier dump-based read named.

Five classes stayed unlabeled for a mechanical reason, and the fix names them (measured 2026-11-07): their
`FNameEntry` blocks are the last allocation on a mapped page (`0x95ffc0`, `0x9affc0`), so 64 bytes read back and the
fixed 206-byte request ran into an **unmapped** page and failed whole. `names.py` now reads the entry one page at a
time and parses the prefix; index 5 is **`ObjectProperty`** (5,867 objects) and index 103 is **`Function`** (8,623),
and a re-run census names 376 classes with no unreadable row.

**The HUD's text windows are live, and the empty-argument guard is in the shipped script (measured 2026-11-07).** A
live field read (`tools/xemu/fields.py`) over the loaded-`M00` boot puts `GameHud` at `0x09352100` (class object
`0x01886c00`) and finds **36** pointer/array fields in its first 288 bytes, among them three `UITextBox` objects at
`+0x050`, `+0x054` and `+0x058` -- the order `StargateGame/GameHud.uc` declares `DialogueWindow`,
`NotificationWindow` and `InteractionWindow` -- plus the `StaticMesh` `HUD_Notifywindow` at `+0xd4`. So the draw
object the notification would use exists on this level; the question is what it holds. `GameHud.uc:1034`
(`SetNotifications`) replaces an **empty** argument with a fixed 27-character placeholder and then calls
`NotificationWindow.DisplayTextBox`, so the two candidate causes are now separable by one read: the placeholder means
the caller passed `""`, a message length means the text arrives and the *draw* drops it. **What would settle it** is
that read -- `NotificationWindow`'s `Text` `FString` length over the same boot, no new external data and no new
instrument.

Tooling: `names.py --census` now takes `--csv PATH` (every row, so one 4-minute walk answers any class question),
a comma list for `--grep`, and `--addresses`; and the census uses `OBJECT_HI = 0x10000000` for the object array, with
`objects.py`'s 128 MiB bound left alone for the dump readers.

**The notification's text is in memory and on the frame, so that half of the question is closed (measured
2026-11-08).** `UProperty::Offset` is calibrated (the dword at property `+0x48`; `docs/NATIVE_PROPERTIES.md` §10), so
the windows can be named by *property* instead of by slot order: on the live row `0x09362100`, `NotificationWindow`'s
`Offset 0x54` is the `UITextBox` `0x097c6e00`, and `UIWidget.DisplayText` (`StrProperty` `0x019c2450`, `Offset 0x394`)
holds `'New Objective : Open the door for the doctor'` — 45 characters, not the 27-character
`NO NOTIFICATION TEXT SET!!!!` that `SetNotifications` substitutes for an empty argument. The same boot's frame
(`02-mission.png`) draws the identical line at the bottom of the instruction panel, so the text arrives, the field
holds it, and the draw path renders it. `DialogueWindow` (`'General, would you mind getting the door for me sir?'`,
53) and `InteractionWindow` (`'default'`, 8) read at the same offset. What stays open in this entry is the
`History[16]` command-line read and the effect question above; "the HUD window draws nothing" is retired.

**The typed line lands in the console's `TypedStr`, and the frame is not a witness for it (measured 2026-11-08).** One
`Tab`, one `spc` and `h i s t` on a stock-disc `M00` boot (`plr3`, stub `12370`) leave `' hist'` in the console object
`0x02344b00` at `+0x044` (an `FString` pair with `count=6`, `objects.py --fstrings`), while `05-typed.png` is
**byte-identical** to `04-tab.png` (PIL `ImageChops` bbox `None`). The band `console-line.py` reports as `prompt`
(56 cyan px, 64x32 at `+34+209`) is already there before a character is sent, and the image's own
`System/Default.ini` says `Console=Engine.Console`, which declares no `PostRender` -- so on a loaded `M00` that verdict
is a false positive and the input question is a memory question. The console is one hop from the player
(`InteractionMaster` `0x02406720` at player `+0x0c0`, the console pointer nowhere in the player's first `0x1400` bytes),
and the diff-against-default route is closed for this class: `--instances` on `0x00dcea00` finds exactly one object
whose class is `XboxClient` -- the live row -- so there is no second row to compare. **Measured 2026-11-08: the field block answers it, and the frame is a witness after all.** `vp1` typed ` hist` on a
stock-disc `M00`, dumped, sent `ret` and dumped again: the console object (`0x02641b00` in this boot -- `plr3`'s
`0x02344b00` holds unrelated heap here, so the address is per-boot) carries `History[0] ' hist'` with `TypedStr` empty and
`HistoryTop/Bot/Cur` `0 -> 1` / `-1 -> 0` / `0 -> 1`, `bTyping 0`, against a pre-dump that holds the line and an empty
`History` (`console-state.py --receipt hist` exits 0 and calls the pre-dump `open for typing, line kept`); both dumps
hold exactly one object on the `Console` table, so the control and the receipt are one object 20 s apart. And the drawn
band does track the console -- 0 cyan px at `04-tab`, 52 px reading `(> hist_` at `05-typed`, 0 px one second after
`ret` -- so "the frame is a false positive" holds only for `plr3`'s pair, where the band was already 56 px
before a character was sent. `Engine.Console` carries no `PostRender` row in `inventory/function_flags.csv`
either, so the "no `PostRender`, therefore no picture" inference is retired: the engine draws it and the
swap's tell is the pre-filled `Say `. **Measured 2026-11-08: the draw is `Engine.HUD.PostRender`, not the console.**
`Engine/HUD.uc:299` declares `simulated event PostRender( canvas Canvas )` and one branch of it (lines 424-425) reads
`if ((PlayerConsole != None) && PlayerConsole.bTyping) DrawTypingPrompt(Canvas, PlayerConsole.TypedStr, PlayerConsole.TypedStrPos);`.
`DrawTypingPrompt` (line 629) sets `C.DrawColor = ConsoleColor`, positions by `ConsoleMessagePosX/Y` and draws
`"(>"@Left(Text, Pos)$chr(4)$Eval(Pos < Len(Text), Mid(Text, Pos), "_")` -- so the prompt is the two-byte literal `"(>"`
joined by `@` (which is why the earlier scan for the byte pair `(> ` found nothing in any package or in the image), the
caret is `chr(4)`, and the trailing `_` appears only when the cursor sits at the end of the line. `PlayerConsole` is
`HUD`'s own `var() Console` (line 27), linked in `LinkActors` from `PlayerOwner.Player.Console` (line 683), and
`StargateHUD`, `GoauldConsoleHud` and `ReplicatorHUD` all extend `HUD` and none of them overrides `PostRender`, so every
HUD class in this build reaches that one draw. **The console class cannot be the source**: a class table carries native
dispatch only (`inventory/class_virtual_overrides.csv`). `Console` (`0x0050cb80`) and `Interaction` (`0x0050c880`) are
both 36 slots and agree on every index except slot 3 -- the per-class slot that is a different function in all 492
tables -- although `Console.uc` re-declares five of Interaction's functions (`Initialized`, `KeyType`, `KeyEvent`,
`Message`, `NotifyLevelChange`, three of them `event`s), and `Interaction`'s `PreRender`/`PostRender` slots are two-instruction stubs (`c2 04 00`). **`vamap.py`
is not needed for this read**: the tables are image `.rdata`, so `tools/class-overrides.py --class Console` reads both
from `default.xbe` with no boot; the earlier attempt translated nothing because it read a *physical* RAM dump at a
virtual address. Reproduce the boot half with `tools/xemu/console-receipt.sh OUTDIR [COMMAND]`.

**The font and the clip are measured, so the draw path has no unknown left (2026-11-08).** `GetConsoleFont` is
script, not native: `Engine/HUD.uc:847` reads `OverrideConsoleFontName`/`ConsoleFontSize` and `StargateHUD` re-declares
it to return `GetSmallFontFor(C)`, which on this disc ends on `UWindowFontsTex.TahomaB30` (override branch) or
`Engine.DefaultFont` (base branch) -- both shipped, so the canvas font is never `None`. The position is
`ConsoleMessagePosX/Y * HudCanvasScale * Size + (1-HudCanvasScale)/2 * Size`, which at 640x480 is X 16 and Y 468 - YL:
inside the frame for any glyph shorter than 468 px. The body passes no clip rectangle at all -- `DrawTextClipped` clips
to the canvas' own `ClipX/ClipY`. `tools/hud-console-font.py` (`make hud-console-font`) is the whole chain and
`inventory/hud_console_font.csv` its artifact: no boot and no breakpoint needed.

**The sink read is gated on the player object's class and on possession, both measured without a boot (2026-11-08).**
`findcls.py --csv`'s class column is each object's own `Class` pointer, so the two level-shaped censuses
(`ghud1`/`ghud2`: 41,265 names, 48,646 array entries) answer what #244 could only caveat: the guest's player object,
`PlayerController0` (index 35,783, `0x01c31000`), is a **stock `PlayerController`** (`0x00d9ae00`), and neither census
holds a `StargatePlayer` or a `Pawn` row. The three slots the console's sink was expected to live in are declared on
`StargatePlayer`, so on such a row they are not fields -- `tools/xemu/sink-slots.py` now checks each slot's declaring
class against the row's `UStruct::Super` chain and prints `n/a` instead of a neighbouring allocation's dword, and
`--class-from PlayerController0` reads the row class off the guest rather than assuming it. **What would settle the
sink:** a boot whose `PlayerController0` carries a `Pawn` (`--expect Pawn=live`) -- the state in which a
`StargatePlayer` controller, and therefore a live `GameHud` / `NotificationManager` / command interface, exists at all.

**The message channels are live, so the remaining cause is a guard, not a null (`sink13`, measured 2026-11-08).** On a
loaded `M00` the player row `0x08842800` holds `NotificationManager` `0x0968c340`, `GameHudPtr` `0x09322100`
(`GameHud`), `commandInterfacePtr` `0x096ff230` (`CommandInterface`) and `Pawn` `0x089e0000` -- four non-null objects,
three named by their own `UObject::Name` (`docs/XEMU.md`). So `StargateHUD`'s `Say`/`AddNotification` path has a sink
to send to, and the `NotificationWindow` on that HUD is measurably drawing text it is given; what is left is which
guard on the console's own path refuses the message (`DisplayMessages`' commented-out `AddTextMessage`, or the
`SetNotifications` condition), not a missing object.

**The blocker was the instrument twice, and the boot that was left never started the game (measured 2026-11-08).**
`hud-text.py` bounded its `--watch` by its *process* clock, so a boot that resolves its `GameHud` 300+ s in printed
a baseline and ended the watch before its first poll -- the boxes were never observed while the commands ran; the
loop now measures its own window and `--selftest` fails if the old early exit returns. `hud-say.sh`'s watcher
deadline was then too short twice (300 s, then 420 s) and now derives from the whole sequence
(`boot + settle + 90 s + 15 s per command`). What the two boots measured instead is a boot that never started: the
window owned the X focus (`the emulator owns the focus after 0s`), eleven saved frames were the *same* 8,021-byte
PNG -- a black 1,280x960 field with one green dot, no menu and no error dialog -- and `GNames` read no plausible
table in 84 polls over 420 s with 0 polls that could not read it at all (a menu boot reads 34,800, a level
41,265). Either the game never started or the stub answered a zero window for seven minutes; the frozen frames and
the reads agree, which points at the boot. So `say`'s sink is still unmeasured, and the next measurement's problem
is the launch: `missions.sh` produced real, differing frames on the same desktop two hours earlier and differs from
`hud-say.sh` in its step list (`docs/HUD_TEXT.md`).

**The panel is out of the way and the boxes stay empty (measured 2026-11-09, `hudm1`).** After `--post` the harness
presses the pad's `A` once more and measures the effect with two shots (40,532 / 34,096 differing pixels); the frame
after it is the lab with the "Press the X button to open the door / A Continue" panel gone, so M00's modal no longer
explains a silent `say`. The level's `GameHud` `0x09312100` (row `0x08842800`, outer `M00`) held the intro dialogue
(counts 14, 35, 33, 50, 56, 53) and the game's own `New Objective : Open the door for the doctor` banner (count 45 at
480.4 s), then all three window slots read `0` at 677.7-678.5 s, and no box ever held `sg1txt1`, `sg1txt2`, `sg1txt3`
or `sg1txt4`. The same boot retired the reader's 300 s page-cache deadline: `hudf2` died at 479 s with two commands
still untyped and the report still printed ABSENT, so `--deadline` now scales with the sequence and a report that
finds that line prints UNREAD. **What would settle it now:** the submit receipt -- `Console.KeyType` copies
`TypedStr` into `History[HistoryTop]` before clearing the line, so a `History[16]` read after the last command (a
different instrument from the `--watch-key=ret` store watchpoint named above) says whether the commands were
executed; if they were, what is left is the `SetSubtitle`/`SetNotifications` guard, not the input.

**Every console route missed on one boot, and the walk's key was the instrument (measured 2026-11-09, `hudsay3`).** On a
loaded `M00` the level's row `0x08822800` resolved (object 47,885 of 48,494, outer `M00`) with
`+0x1008 -> GameHud 0x092e2100`, and seven box changes came off it -- the intro's turns, then the game's own
`New Objective : Open the door for the doctor` -- so the sink read is live and the frames repainted (42,959 / 32,704
px across the dismissal, against 0 / 0 on `hudsay2`). All three console routes still missed: the row's
`InteractionMaster` (`+0xc0`) read **null**; the HUD's `PlayerConsole` (`+0x398`) read `0x00050967`, a number and
not an address; and a whole-array walk of **48,646** objects read **zero** `Console` objects in 347 page reads /
215.1 s, so both markers printed `UNMEASURED`. The walk is the instrument: it screened `UObject::Class` (`+0x24`)
against `0x0050cb80`, which is the `Console` **class table** in `.rdata`, while a table is the object's **first**
dword (`hud-link.py` measured the typed-into `Console` opening `00 50 cb 80 ...`) and `+0x24` holds the `UClass`
*object* (`ghud1` census: `275,0x00d48a00,Class,Console`; instance `36148,0x02344b00,Console,Console`). The `+0xc0`
slot is `Client`'s, measured on the `XboxClient` object `0x01fb9bc0` (`plr1`, and `ghud1`'s census names the same
address on a second level boot), so a level row's null there says nothing about a console. `sink.rows_holding`
screens both header dwords and `hud-text.py --console-client ADDR` reads `InteractionMaster` from the object that
owns it; `hudsay3`'s zero is therefore void as evidence about the console. **What would settle it:** the same
`hud-say.sh` run with the fixed walk (or `--console-client=0x01fb9bc0`) -- `RECEIPT ... IN HISTORY` or
`NOT IN HISTORY` is the submit verdict, and only then is the `SetSubtitle`/`SetNotifications` guard the next
question.

**There is no guard left in the sink chain, so a marker that drew nothing never reached it (measured 2026-11-09, no
boot).** `tools/hud-messages.py --sink-callers` counts the call sites of every sink over all **2,317** classes in the
shipped `ScriptText`: `GameHud.SetNotifications` (`GameHud:1034`) has exactly **one** caller,
`NotificationManager.DisplayMessages` (`NotificationManager:40`); that has exactly **one**, `StargateHUD.DisplayMessages`
(`StargateHUD:1316`), which is the engine's dispatch because `Hud.PostRender` (`Hud:299`) calls `DisplayMessages(Canvas)`.
`GameHud.DisplayTextBox` writes `DialogueWindow`, `SetNotifications` writes `NotificationWindow`, `SetInteractionText`
writes `InteractionWindow`. The only conditions on the path are `GH == None`, `DeltaTime <= 0.0`, the head-of-queue
`MessageLife <= 0.0` test and the `NotificationLifeTime` eviction -- configured to **4.0 s**
(`System/tweak/Misc/StargateHUD.ini:216`) -- and `SetNotifications` substitutes `NO NOTIFICATION TEXT SET!!!!` for an
empty string rather than dropping it, then draws with duration **0**. Both windows were measurably live on `hudsay3`'s
level: `NotificationWindow` held the game's own `New Objective : Complete fitness Examination` (count 45) and, at
479.3 s, `New Objective : Open the door for the doctor`; `DialogueWindow` held the intro's turns. So `SetSubtitle`'s and
`SetNotifications`' guards are **retired as the cause** of the silent markers, and what is left is the edge a console
command has to cross: `StargateHUD`'s `exec function AddNotification` (`StargateHUD:277`) -> `DisplayNotification` ->
`StargatePlayer.AddNotification` -> `NotificationManager.AddNotification` (9 call sites in the tree), and for `say` the
engine's `PlayerController.TeamMessage` -> `myHUD.Message` -> `SetSubtitle`. **What would settle it:** the submit
receipt -- `Console.History[16]` after the last command (`hud-say.sh --receipt`, with `--console-client=0x01fb9bc0` or
the fixed walk). `IN HISTORY` puts the string in the queue and makes the 4 s lifetime the suspect; `NOT IN HISTORY`
means the exec edge never ran, which is the console's submit rather than any HUD guard.


**The client route reaches the `Console`, and the object it reaches is the class default (measured 2026-11-09,
`hudsay6`).** One `hud-say.sh` boot with `--console-client=0x01fb9bc0` loaded `M00` (name table 40,427 at 281.8 s),
resolved the level's row `0x087c2800` (object 47,885 of 48,494, outer `M00`) with `+0x1008 -> GameHud 0x09322100`,
read six box changes off it (the intro's turns, then the game's own `New Objective : Open the door for the doctor`
at 480.5 s) and dismissed the tutorial panel (42,083 px, 3.42%) -- so the boot was live -- and the receipt's client
route then read a `Console` in **4.3 s with 0 array walks**: the client's `InteractionMaster` `0x02406720` holds it
at `+0x30`. The object is at **`0x02344b00`**, the address `docs/XEMU.md` records as the **class default** on two
earlier boots, and its fields are the shipped defaults (`HistoryTop 0`, `bTyping 0`, `TypedStr` empty, 0 populated
slots), so both markers printed `NOT IN HISTORY` -- a true statement about the default and no statement about the
keys. The slot's class screen cannot separate the two, because the default is of that class, and the frames cannot
either: the marker shot is 0.7 s after `ret`, and a submit clears `TypedStr` and closes the console, so a successful
submit also leaves a blank frame (measured: `06-dismissed -> 041` = 314 px, 0 cyan pixels on both marker shots).
`hud-text.py` now tests the discriminator structurally -- `is_class_default()` reads `UObject::Outer` (`+0x18`) and
compares the outer's first dword with the `Package` class table (`0x004de088`) -- refuses the client route's default
object in favour of the array walk, prints the outer on the first-sight line, and answers `UNMEASURED` when every
object it read is a default. **What would settle it:** the same boot with that instrument. If the walk finds a second
`Console` with a non-`Package` outer, `IN HISTORY` / `NOT IN HISTORY` is the submit verdict; if it finds only the
default, the live console is outside `GObjObjects` and the read has to move to the master's other slots, since the
one that answered names a default rather than the instance.

**The master's block is seven pointers, and the census was reading its own text (measured 2026-11-09, no boot).**
`hudq1`'s census printed **27** rows for the `InteractionMaster` block and **20 named no class** -- the block's own
UTF-16 (`0x007c0020` is `" |"`, `0x00410020` is `" A"`) plus `0x0050cc68`, a dword inside the `Console` class table --
so `master_slots()` now refuses a target that names no class and prints the count (`7 object pointer(s) ... 20
dword(s) that name no class (the block's own text, not slots)`). The seven are `+0x08` an `ObjectProperty`
(`CriticalBreathIn`), `+0x18` a `Package`, `+0x24` the `InteractionMaster` **class object**, and `+0x28`..`+0x34` the
`XboxClient`/`GUIController`/`Console`/`UIXInteraction` defaults -- and **every one of them has a `Package` outer,
the block's own `+0x18` included**, so "a `Package`: the class default" separates nothing in this block (it also
fires on a class object, whose outer is a package); the census now prints the outer's class name beside it. The
contradiction is narrowed, not closed: `class-defaults.py … --class Console` reads Console's five defaults
(`HistoryBot -1`, `TimePerDemo 300`, `TimeTooIdle 60`, `TimeBeforeReboot 5`, `bRequiresTick True` -- the one bool is
`Interaction`'s) and none of its own, so a `Console` default's `bTyping` byte must be 0, and the 3 that object read is
not a default value. The census row `36148,0x02344b00,Console,Console` cannot decide it either: a live console
created by the viewport is also named `Console`. **What would settle it:** one boot, `--console-client` and the
receipt, reading the `bTyping` **byte** before `Tab`, after `Tab`, after the first key and after `ret` --
`0 -> 2|3 -> 1 -> 0` is a packed bool byte (`bIgnoreKeys` is declared next to `bTyping` and swallows the first
keypress), while `0 -> 1` keeps it a plain field and puts the contradiction back on `+0x18`.

**A title screen has no live console (measured 2026-11-09, `hudq3`, `hudq4`).** The client's `InteractionMaster`
`0x02406720` holds the same seven pointers at the title as on a level boot and its `+0x30` is the `Console` **class
default** (`0x02344b00`, outer a `Package`), and a 400 s `--trace` across `Tab`, `a` and `ret` -- all three delivered
(`gkey -> ok`) -- shows the slot and the byte unmoved (`bTyping 0`, `TypedStr` empty, `HistoryTop 0`). So the four-state
`bTyping` curve has to be measured on a loaded level, not at the title, and `hudq1`'s `bTyping 3` (same address, level
boot) is not reproducible at the title. **What would settle it:** the same trace on a level boot, bound to the console the
HUD's `+0x398` names once it is non-null.

## U16. Why `QUICK SELECT MAP` seemed to stop at `M15B` -- closed: it holds 73, and every campaign entry loads

**Answered: there was no truncation.** The list holds **73** entries -- every `.unr` on the disc -- and the "first 31
campaign maps and nothing after them" reading came from a fifth page that held a single item (`docs/XEMU.md`,
corrected 2026-11-05). The trail below is kept because each step removed one candidate.

**Narrowed (2026-11-04):** the entries come from `GUIController.GetMapList`, a `native` function
(`GUI/GUIController.uc:430`), so both the enumeration and the cap are in the XBE -- there is no ini or UnrealScript
table to read or extend.

**The routine is found, and it has no cap (2026-11-05).** `UGUIController::execGetMapList` is `0x002fe5f0`, reached from
the binder name `intUGUIControllerexecGetMapList` (`0x00534a4a`) and its one xref from the log format
`[VL:GetMapList]:%s` (`0x00538d28` -> site `0x002fe7e4`). Measured in the body: it builds `*.%s` from the `MapExt`
global, walks the engine's enumeration source, and adds one list item per hit; `Filter` is stepped and never read, so the
filter box is inert by construction; and no comparison against a constant bound exists anywhere in its 1,256 bytes, so
the truncation is **not** a loop limit in this routine.

**Where a prefix appeared to come from (2026-11-05; superseded by the count at the end of this entry).** `Maps/` holds 73 `.unr`, the XDVDFS directory walk is ASCII order, and
index 40 is `M15B.unr` / 41 is `M15C.unr`: the list is the first 41 names of the disc's own order. A filter drops from
the middle; a fill that stops early leaves a prefix. The enumeration source global (`0x005fbee4`) resolves, over the GDB
stub in a booted guest, to a struct whose `+0x7c`/`+0x11c` arrays are the 8 `[Core.System] Paths` globs from
`System/Default.ini:49-56` -- so the list is filled from the path globs and the file cache behind them.

**What the read actually shows (2026-11-05, correcting the first attempt).** The virtual read *does* work at the title --
`.text` and the `MapExt` global read correctly at every stage, and a caller only sees zeros because the stub raises
`odd hex length from stub` on roughly every other read and a swallowed exception looks like a zero; retrying on a fresh
connection fixes it. With that fixed, `0x005fbee4` still resolves to an object whose only arrays are the two 8-entry
`Paths` copies, so it is **not** the file cache: the routine iterates the path globs and the names come from the callee
at `0x00036cc0`. **The callee, measured (2026-11-05):** `0x36cc0`, `0x36ce0` and `0x36c90` are `_wcsstr`, `_wcscat` and
`_vsnwprintf` -- CRT, not the enumeration. The `*.unr` match is a literal *substring* test on each `Paths` glob, so the
routine collects paths, and the map names arrive from one 4-argument virtual call at `0x2fe806`
(`[[0x005bf514] + 0x30]`) whose result it appends to its own array; `0x300410` (called first) is the widget's array
clear. **The file layer is cleared, and the page holds the full set (2026-11-05).** The call is `[[0x005bf514] + 0x30]` in the
double-dereference sense (`mov 0x5bf514,%ecx; mov (%ecx),%edx; call *0x30(%edx)`), and the pointer at `0x005bf514` is
rewritten at runtime: the image's own chain ends in a 22-byte stub (`0x00034570`) that returns an empty `TArray`, so
resolving it from the file alone answers with a no-op. At the title the object's table is `0x004c8db8`
(`FFileManagerGeneric`, named from `0x000246d0` and the `FFileManagerGeneric.h` assert literal at `0x004c8b40`) and
slot 12 is **`0x00024e70`**, a `FindFirstFile`/`FindNextFile` loop that skips `.`/`..` and directories and has **no
bound**; at the call's return (breakpoint `0x2fe809`) it hands back **73** names in directory order. `GetMapList` sorts
them (`0x000f8ab0`) and appends every one -- the controller's array at `this+0x27c` reads `count=73 max=76` after the
page opens (breakpoint `0x2fe8e0`). There is no cap to patch, and no map needs a save or a `Travel` call to reach.

**And the load half is closed (2026-11-14).** Those 30 maps were not unreachable, only untried: `tools/xemu/missions.sh`
presses the d-pad once per index and selects, and indices 41-70 all loaded (`inventory/map_loads.csv`, 61 of 61
campaign entries; `docs/MAP_LOADS.md`), as do the page's last two entries. So the console's refusal of `open` (#147)
costs nothing here: the list is the route, and it needs no save, no checkpoint and no `Travel` call.

## U17 -- which compiler can write, and read, version 130 / licensee 4

**Measured, and it narrows the question (2026-11-09, `docs/COMPILER.md`).** A UE2 compiler now runs here: Epic's
UE2 Runtime build 2226, under Wine, emits **version 126 / licensee 0** packages from source. Pointed at this build's
packages it stops in `UStruct`'s default-property expression reader -- `Bad expr token 03` in `SerializeExpr` on
`Core.Object` -- before it parses a line of script. So the gap is the **token grammar**, not a version guard: no stock
compiler from any public UE2.5 release can consume these packages, and patching the version constant cannot close it.

**What would settle it.** Two things, and the first is a question about data rather than code:

1. whether the privately held 2006 studio tree contains the UnrealScript compiler's *sources* -- the toolchain that
   wrote these packages is the only compiler guaranteed to agree with them;
2. failing that, a front end written here against the measured format (`docs/BYTECODE.md`, PLAN 3.7), which does not
   depend on data this repository may not carry.

The deciding test is the same either way: one trivial class compiled and byte-compared against the shipped package,
i.e. L1 by the mapping tiers in `AGENTS.md`, with PLAN 3.8's comparator for the tiers above it.

**2026-11-12: the field widths are measured from the writers, and the 9-byte unit is named.** A cooked
package is a saving archive, so `FUN_000637e0` writes every `TArray` count as an `FCompactIndex`, not a dword;
`FUN_0003d900` streams a 12-byte record as one object reference plus two dwords (9 bytes when the reference is one
byte), which is exactly the stride-9 unit this entry's residue had measured without an owner; `FUN_0003d160`'s
elements go through the archive's *name* method, so the two tail arrays are `TArray<FName>` and not `n x 4` dwords;
and `UState::Serialize` (`0x000394c0`) writes 8 + 8 + 2 + 4 bytes before `UClass`'s five dwords -- 22 bytes the
field table did not carry. The forward walk with all of that (`tools/class-record-fields.py`, artifact
`inventory/class_record_fields.csv`) ends on the block start for **0 of 2,209** located classes and runs off the
payload for **1,038**, explaining a median **47** bytes of a 91-byte middle. So the widths are settled and the
field set is not: what would settle the 545 is the writer that produced the per-class body between these fields
and the defaults block, and the record's own `+0x4d0` array is now known to be a streamed 9-byte record rather
than a fixed table. Numbers: `docs/NATIVE_VM.md`, *The record's field widths are compact, and a 12-byte record is
nine*; `docs/CLASS_DEFAULTS.md`, *The tail's widths are named*.
**2026-11-12: the middle is measured -- a record array found by its period, and a tail that is not one
grammar.** `tools/class-record-middle.py` locates the 9-byte record array by its *period* (a run of >=3 identical
units whose reference byte varies) on **536 of 2,204** classes -- median 5 units, max 269 -- against **13** in a
same-length window taken from the block start, so the array is really in the middle and its count field is not where
the field table looks. The leftover after the walk is `mixed` on 1,037 of 1,166, with `dwords` 44, `records` 27,
`compact` 7, `zero` 4, and its commonest size, 20 bytes (190 classes), is the five-dword group's own width, so the
545 are not moved by adding a field in front of the block. The offset sweep lands 80 (3.6%) in the documented order
and 618 (28.0%) with the dword group moved after the arrays, but 360 of the 618 sit in the 367 classes whose middle
is exactly 88 bytes, so the sweep is recorded as a negative rather than as evidence for an ordering.
**2026-11-13: the record region's field map comes from the destructor, and it moves the array in front of
`+0x4d0` from `+0x4c0` to `+0x4c4`.** `UClass`'s three constructors zero the region one dword at a time and each
skips a different set of dwords, so no boundary survives in a store map; `UClass`'s destructor (`0x3dbd0`) destroys
eight members by address, at `+0x4b8`, `+0x4c4`, `+0x4d0`, `+0x4dc`, `+0x4e8`, `+0x4f4`, `+0x500` and `+0x514`, with
destructors `0x3d7a0`, `0x3d850`, `0x3da40`, `0x1c750` (three members), `0x1aa30` and `0x1b310`. The first three are
one `TArray` destructor template instantiated for three element types, and each serves exactly one member in the
image. Corpus-wide the same instrument places 765 members on 493 destructors (`inventory/class_member_map.csv`).
**What would settle the tail's owner** is now narrower and needs no new data: it is a member *after* `+0x4d0` that
`UClass::Serialize` does not write, i.e. one of `+0x4e8` (`0x1aa30`), `+0x500` (`0x1c750`) or `+0x514` (`0x1b310`) --
read the element loop inside `0x1aa30` and `0x1b310` past the shared template prologue, and a 12-byte element freed
through an object-reference destructor names it.

**2026-11-13 (third): the archive's name slot writes one compact field per element, and the middle's count of 2 is
decided by that width.** `tools/archive-slots.py` (`make archive-slots`, `inventory/archive_slots.csv`, `--check`
with no dump) finds the archive object by the version pair every payload carries (130 at `+4`, licensee 4 at
`+0xc`): **six** constructors store it beside a vtable, so the pin alone names nothing, and the one table whose
`+0x18` and `+0x1c` slots are *different* functions is `0x004d40d0`. There `+0x14` is `0x000ea7f0` (`ret 8`, a no-op
`Preload`), `+0x18` is `0x00038bc0` (one compact field, through the proxied table's reference half `[eax+0x24]`), and
`+0x1c` is `0x00038bf0` (one compact field, through its name half `[eax+0x20]`). An `FName` on disk is therefore one
1-5 byte index and an array of `n` names spans at most `1 + 5n` bytes -- which supersedes this entry's "two compact
indices per element" everywhere it appears. Re-reading the record's gap with that width
(`tools/class-record-gap.py`) closes on **3 of 549** against a control of **14**, and the arithmetic bound is what
settles the disputed middle: a name array of the middle's own leading count fits on **14 of 414** parsed rows and on
**2 of 325** of the count-2 middles (median 27 bytes, bound 11), so the middle is not that array and its 20 bytes are
two twelve-byte records of `3+3+3` and `2+8`.

**2026-11-13 (second): every placed member's element width is measured, and it excludes one candidate for the
tail.** The `TArray` destructor template varies in exactly one instruction -- the `PUSH imm32` range-destroy argument
that is the element size: `Class`'s three adjacent arrays are 8 (`+0x4b8`), 4 (`+0x4c4`) and 12 (`+0x4d0`, the record
array, matching `FUN_0003d900`'s stride), the shared `0x1c750` is 4 (`FName`), and the array inherited into `+0x4e8`
is 1 byte per element. `inventory/class_member_dtors.csv` carries the width for all 93 template destructors of the
199. **What would settle the tail's owner** is therefore no longer three candidates: a 4-byte element cannot hold a
value and a reference, so `+0x4c4` is out and `+0x4b8` (8-byte elements) is the remaining `TArray` of the group that
can. What would settle it is the code that indexes `+0x4b8` at a stride of eight, or the writer that fills it.

**2026-11-13 (fourth): the middle's field order is the writer's, and 19-21 bytes in front of its array are not
in any writer the body names.** `UClass::Serialize`'s call order puts the `+0x4b0`/`+0x4b4` pair *after* both
arrays, and the two slots (`0x00038bc0`, `0x00038bf0`) are one `FCompactIndex` each -- they differ only in the proxy
map they consult (`[eax+0x24]` for an object, `[eax+0x20]` for a name), so no width can tell the two encodings apart.
Walking that order from `prefix_end + 42` closes **0 of 2,204** located classes; walking it from `+19` closes on
**732** (and `+20` on 518, `+21` on 232), with the array's count at 2, the reference one byte and inside the tables,
and the config name a valid index on every one of them; controls -- pair in front, adjacent name arrays, last array
dropped -- close on 167, 33 and 37 classes at any of 32 offsets. `tools/class-record-order.py` (`make
class-record-order`, `inventory/class_record_order.csv`) is the instrument. **What would settle the 19** is the body
of `UStruct::Serialize` (`0x000392b0`) after the third reference the prefix page ends at: the writers it calls
should sum to 19-21 bytes, and one of them is likely the compact that makes the width vary. Depth:
`docs/NATIVE_VM.md`, *The middle's field order is the writer's*.

**Related, and still open:** whether the 545 withheld `defaultproperties` blocks in `docs/CLASS_DEFAULTS.md` are the
same `SerializeExpr` stream. A tag stream would not parse package imports as property names, and that residue does;
`UStruct::Serialize` at `0x000392b0` is the reader that would decide it.

**2026-11-12: the reader is `UClass`'s, and it is read -- the residue is a start problem, not a grammar one.**
`UClass::Serialize` (`0x0003a800`) is the function that decides this entry's open half, and `UStruct::Serialize` is
not: the defaults are a `UClass` field (`+0x4e8`/`+0x4ec`/`+0x4f0`), read by vtable **+0x98** = `0x0003bdc0` into a
buffer sized by `GetPropertiesSize()` (+0x88 = `0x0003d520`) and asserted `Defaults.Num()==GetPropertiesSize()`
(`UnClass.cpp:0x454`/`0x459`, `UnClass.h:0x235`). So the 545 withheld blocks are not a `SerializeExpr`-versus-tag
question: the reader reads tagged properties, the stream carries no length, and the record in front of it ends in two
named `TArray` fields (12-byte and 4-byte elements) that a forward scan lands inside. Depth: `docs/NATIVE_VM.md`,
*`UClass::Serialize`*, and `docs/CLASS_DEFAULTS.md`'s *What the 545 withheld classes are*.
**2026-11-12: reading the payload backwards is measured, and it does not relocate the 545.** `tools/class-tail.py`
reads the four fields `UClass::Serialize` writes before the defaults from the block's side, on the 1,664 starts whose
block accounts for itself as the control. The field in front of a start is a plausible 32-bit count on **1** of them,
three empty fields on **0**, and a one-byte compact count walks back cleanly on **636 of 1,664** at scattered
offsets -- so the stream's tail cannot be walked from the block end in the order the object's own table gives. The
540 withheld candidates differ in the way "start inside a table" predicts (69 periodic against 1,664's 1), and
walking those runs back relocates exactly **1**. A bytecode walk over the same windows is refused too -- a clean run
ends exactly on a start for **5 of 1,664** control payloads at most 26 bytes long, and the epilogue `04 0b 47` in
none -- so the record's tail is neither a tag stream nor script, and the field widths in `UStruct::Serialize`'s body
(`0x000392b0`) are what is left. Numbers: `docs/CLASS_DEFAULTS.md`, *The bytes in front of a block start, measured*.
`0x000392b0`) are what is left. Numbers: `docs/CLASS_DEFAULTS.md`, *The bytes in front of a block start, measured*.

**2026-11-12: the prefix is measured, and the tail field list is closed with a control.** `tools/class-prefix.py`
(`make class-prefix`, `--check` in `make ci`, artifact `inventory/class_prefixes.csv`) decodes the record's opening
with `UStruct::Serialize`'s own order over all 2,319 class payloads: a **compact** index at offset 0 equals the
export's `super` on 2,319/2,319 (1 byte on 1,451, 2 on 868, never 4), the field after it is one `00` byte on
2,319/2,319 — a null object reference, since an `FName` there could not be `NAME_None` — and the third field names the
class's own `TextBuffer` export on 2,319/2,319. The prefix is therefore three references in 3-6 bytes, and
`FriendlyName`, the source line and the text position are not in the cooked stream, which is why the field list the
bytecode attempt used validated on none of 3,931 functions: encoding was never the problem, the field *set* was.
`UClass::Serialize`'s tail list is not in the record either — walking it from every offset lands exactly on the block
start for 191 of 2,209 located classes against 184 of 2,209 for the same walk aimed 8 bytes short, i.e. chance, at 124
distinct offsets. What remains for the 545 is the middle itself (median 91 bytes, p90 394): not the prefix, not that
field list, not a tag stream read backwards and not bytecode. Numbers: `docs/NATIVE_VM.md`, *The class record's
prefix*; `docs/CLASS_DEFAULTS.md`, the section above.

**2026-11-12: the run's head is not there, and the null says so.** The previous note read the record array's
element by its period on 536 of 2,204 classes and recorded that "the count field in front of it has not been
found". `tools/class-record-run.py` (`make class-record-run`, `--check` in `make ci`, artifact
`inventory/class_record_run.csv`) asks that question with a null beside every answer: a compact count equal to the
run's unit count ends within six bytes in front of it on **16 of 536** (3.0%) while the same test at every other
byte position of the same middles answers **9,675 of 268,834** (3.6%) — expected 19, observed 16, so the head is at
chance — a raw 4-byte count is there on **none**, and a count equal to the whole array's element count at a 9-byte
lattice point up to 40 units back lands on 184 (34.3%) where 41 attempts over a 3.6% background should land on about
78%, i.e. below chance rather than above. So the array is not head-located by a fixed 9-byte step count, and the
reading that survives is the writer's own: an object reference is a compact index of **1 to 5 bytes**
(`FUN_0003d900`, archive vtable `+0x18`), so the array's earlier elements need not be 9 bytes wide and its head sits
in front of a *variable*-stride lattice. **What would settle it:** a walk back from the run that decodes each
preceding element as one compact reference plus two dwords (9-13 bytes per element, chosen by the reference's own
length) and accepts a head only where the count there equals the elements it walked — with the same walk aimed at a
shifted start as the control, because a chain of variable steps is cheap to fit. Numbers: `docs/NATIVE_VM.md`, *The
run has no head*; `docs/CLASS_DEFAULTS.md`, *The array's head is not in front of its run*.

**2026-11-12: the walk back finds the record's widths and still no count.** `tools/class-record-walkback.py`
(`make class-record-walkback`, `--check` in `make ci`, artifact `inventory/class_record_walkback.csv`) walks back
from the 9-byte run's first element boundary at the widths its writer uses -- one compact reference of 1-5 bytes
plus two dwords, an element taken only where its reference names an object the package's tables hold, a head
accepted only where a compact count ends *exactly* at the boundary and equals the elements walked plus the run's
units. The body is real: **242 of 536** classes walk at least one element back and **24** walk eight or more (11 on
`AmbientCreatures.HerdAnimal`) against **52 and 0** for the same walk aimed one byte off. The head is not: **1 of
536** (0.2%) against **3 of 536** (0.6%) for the null, with 15 more matching the arithmetic 1-5 bytes early. And the
dword the run repeats is a small integer, not a reference -- resolving it names `Pawn` (43), `Canvas` (28),
`UWindowCheckbox` (24), i.e. the first entries of each package's table (a one-byte index cannot name more than 63
objects), and on one package it lands on `bDeleteMe`, a `BoolProperty`. So the run is a stretch inside a
variable-stride body and the array's count is not at the end of it. **What would settle it:** the writer that
allocates the array -- `FUN_0003d900`'s caller, whose `push count; call operator new` pair and `TArray` head say
where the count is written -- or a reading in which the body is not a `TArray` but a run terminated by a sentinel,
as `UClass`'s defaults are. Numbers: `docs/NATIVE_VM.md`, *The walk back continues the record's own widths*;
`docs/CLASS_DEFAULTS.md`, *The elements continue past the run*.

**2026-11-12: the count question is answered from the caller, and the array is read where it sits.** The walk-back
note asked for `FUN_0003d900`'s caller and its `push count; call operator new` pair. There is no such caller:
`tools/call-census.py --target 0x3d900 --names` finds **one** `E8` site in the 6.2 MB image, `0x3a8a6` inside
`UClass::Serialize` (`0x0003a800`), which passes the archive and the class object's own member at `+0x4d0` (with
`+0x4dc` going to the `FUN_0003d160` call at `0x3a8af`, and `+0x4f4` to the one at `0x3a904` on version > `0x62`).
`FUN_0003d900` writes that member in place through `FUN_000637e0` on its `Num` word -- a compact index whenever
either archive flag is set -- and then one element per unit. So the count is the array's **first field**, written
by the function that writes the elements, and the reading in which it is a field of the record *before* the body is
dead: there is no allocation to hang it on. Measured from the bytes at the position the writer implies
(`prefix_end + 42` = prefix + `UState`'s 22 bytes + five dwords), `tools/class-record-array.py`
(`inventory/class_record_array.csv`, 2,204 rows) parses the array on **549** classes (24.9%) -- against **36** of
those 549 when the dword group is moved after the array and **0** when the walk is aimed one byte late -- but only
**3** of the 549 walk on to land exactly on the defaults start. The rest stop short by a mode of exactly **20**
bytes (190 classes; 22 on 59, 21 on 31), the dword group's own width, so the field between the array's tail and
the block start is 20 bytes wide and the 545 withheld blocks are still one field away. **What would settle it:**
that 20-byte field's writer -- `UClass::Serialize`'s own body between the `+0x4f4` call and the defaults branch,
read with `ExportAt.java` at `0x0003a800` and paired with the 190 classes whose residue is exactly 20 -- or a
version-gated field above `+0x4f4` in the class object that the current field table does not carry. Numbers:
`docs/NATIVE_VM.md`, *The array's count is its own first field, and its writer has one caller*;
`docs/CLASS_DEFAULTS.md`, *The array's head is at +42 and the bytes after it are one field, not a walk*.

**2026-11-12: the field between the array and the block does not exist, so U17 is about the array's own end.** This
entry's "what would settle it" named `UClass::Serialize`'s body between the `+0x4f4` call and the defaults branch.
That body is `ADD ESP,0x8` and `MOV EAX,[EBP+0x10]` -- no field, with the `0x63` version gate at `0x3a8f6` the last
one in the function -- so the record's field list is complete and the gap can only be the three fields the writer
already has: a name array, the 8-byte config `FName`, a name array. `inventory/class_record_array.csv`'s `gap`
column measures that gap from the array's *bare* tail: 27 bytes on 188 of 549 classes (29 on 61, 28 on 30, 30 on 9;
the earlier 20-byte mode is this gap minus the 7 bytes its `k=2` name-array walk had consumed), 13 classes where the
walk runs past the block start, and **0** where the writer's remaining fields close it. Two controls: the defaults
property stream parses from the block start to the payload's last byte on 543 of 551 (65 from the array's tail --
chance), so the block start is not what is off, and the array aimed one byte later parses on 0 classes, so the head
at `prefix_end + 42` is not either. **What would settle it:** the array's element span from its writer --
`FUN_0003d900`'s per-element loop, which says how wide an element is and whether `FUN_000637e0` counts elements or
something smaller -- or the config `FName`'s name index as an anchor: it must resolve in the package's own name
table, so locating it in each gap splits the gap into the writer's own fields and says which side is short.
Numbers: `docs/NATIVE_VM.md`, *After the last name array the body writes nothing, so the gap belongs to the array*;
`docs/CLASS_DEFAULTS.md`, *The 20 bytes are not a field, and the writer's body is why*.

**2026-11-12: the element span is measured, and it moves the array's head rather than the gap.** This entry's "what
would settle it" named `FUN_0003d900`'s per-element loop. Read, it scales both `TArray` words by twelve and writes
each element in three calls -- the reference through vtable `+0x18`, then two raw dwords through `0x00037110` and
`0x000370e0` -- so a record is twelve bytes in memory and `ref + 8` on disk, and no element is 27 bytes wide.
Restarting that grammar at the array's bare tail closes the record exactly on **291 of 386** classes (75.4%, against
76 of 10,384 for the same grammar elsewhere in the following 40 bytes), and **all 291** are classes whose parse at
`prefix_end + 42` is all-null records (336 of 549 are) while none of the second parses is. So the residue is not
after the array: the `prefix_end + 42` position holds a member shaped like the array with null references, and the
array begins at its tail. **What would settle it:** that member's own writer -- it is not in `UClass::Serialize`'s
body, which calls `0x3d900` once, so it is a `TArray` the class's constructor fills, 19 bytes on the zone family (a
count of 2 and eighteen zero bytes) -- plus the disk width of one `FName` through vtable `+0x1c`, which sizes the two
name arrays that end every gap. Numbers: `docs/NATIVE_VM.md`, *The element is twelve bytes in memory and nine to
thirteen on disk* and *The bytes at the array's own tail parse as the same array again*;
`inventory/class_record_element.csv` (549 rows).

**2026-11-12: the last bytes are a value/reference pair list, and it is package-constant on five of seven packages.**
`tools/class-record-tail.py` reads the 6-to-9-byte gap end on the 291 closing records (`inventory/class_record_tail.csv`)
as a compact count (3 on 219, 2 on 6) plus that many (compact, compact) pairs, ending exactly on the block start on 219
with **0 of 225** when the same grammar starts one to three bytes later. The middle pair's second value is negative on
219 of 225 and takes four distinct values across the whole corpus -- one per package (-6, -3, -4, -7) -- and every first
value resolves in the package's own name table, so the pair is a value and a *reference*, not the two `FName` indices
this entry's name-array reading assumed. The tail is also byte-identical across all 51 `Stargateeffects` classes, all 6
`StargateGame`, 123 of 149 `Sharpeffects`, 5 of 6 `Engine` and 7 of 12 `GamePlay`, so on five of the seven packages it is
package-level content and not class data; `StargateContent` (48 distinct over 61) and `StargateVehicles` (6 over 6) vary
per class. **What would settle it:** parse the defaults property stream from `block_start - width` on those 291 classes
and ask whether it accounts for the payload to its last byte, as the stream already does from `block_start` on 543 of
551 -- a stream that also parses from the earlier position would make the pair list the first tag of the defaults block
and not a field of the record, and one that parses only from `block_start` leaves a field nothing in `UClass::Serialize`
writes. Numbers: `docs/NATIVE_VM.md`, *The tail's elements are a value and a reference*; `docs/CLASS_DEFAULTS.md`,
*The bytes after the array are a pair list*.

**2026-11-12 (second measurement): the tag side answers it, and the pair list is in the record.** `class-record-tail.py`
`--sweep` now runs the property-tag parser from both positions with the same rule (terminates on `None`, declared names
only, ends exactly on the payload's last byte): the stream closes on **291 of 291** classes from `block_start` and on
**67 of 291** from `block_start - width`. The block start is confirmed from the tag side, the pair list is the record's
own bytes, and the record's field list is complete only if something outside `UClass::Serialize`'s two callees writes
them -- which is the residual this entry now carries. Numbers: `inventory/class_record_tail.csv` (`def_exact`,
`earlier_exact`); `docs/CLASS_DEFAULTS.md`, *The pair list is not the defaults stream's first tag*.

**2026-11-13 (third): the `+0x4b8` census finds two readers that index it at eight, and neither is a
`Class`.** `tools/member-access-census.py` sweeps all 14,973 placed bodies and takes every memory
operand whose `disp32` is `0x4b8` at an exact instruction boundary: **17 sites in 15 functions** -- 9
loads, 2 `lea` (the two destructors), 6 stores -- with 36 stack forms counted apart. Three loads index
at stride 4, **two at stride 8**, four pass the value on. The stride-8 pair: `0x0010b719`
(`Engine_UnChan`) reads element+0 as an object pointer and element+4 as an integer (`imul`), the
(reference, value) shape the tail parser reports; `0x00358c4a` (`FUN_00358bb6`) reads the same eight
bytes as two 16-bit fields. Neither object is one the destructor map places a member at `+0x4b8` for
(`Class` 8-byte, `GameReplicationInfo` 4-byte), and on a `Class` the array is only destroyed and
zero-filled -- `UClass::Serialize` has no access and nothing indexes it at any stride. So an array
nothing reads cannot produce the identity pair list, while the width argument that made it the tail's
candidate stands, unowned. **What would settle it:** the same census at `+0x4c4` and `+0x4d0` (which
of the three arrays a `Class` is read through at all), and the same census with each base register's
origin followed, so the two stride-8 objects are typed rather than assumed. Numbers:
`docs/NATIVE_VM.md`, *Every `+0x4b8` access in the image, and the two that index it at eight*.

**2026-11-13 (fourth): the group of three is censused together, and `+0x4b8` is the one array no
`Class` code names.** `member-access-census.py --disp 0x4b8 --disp 0x4c4 --disp 0x4d0` gives **61
sites**: 29 at `+0x4c4` (8 loads, 16 `lea`, 5 stores) and 15 at `+0x4d0` (3 loads, 7 `lea`, 5 stores)
beside the 17 already measured. `Class`'s own region initialiser `0x3a670` (table slot 10) takes
`+0x4c4`'s and `+0x4d0`'s addresses and passes `push 0x4` and `push 0xc` to the same helper, and
`UClass::Serialize` takes `+0x4d0`'s address at `0x3a89e` -- so the record array's writer is reached
by address, which is why no site at these displacements indexes what it loads. No `Class` function
takes `+0x4b8`'s address at all: it is zero-filled inline by three constructors and destroyed, and
nothing else. The displacement is also shared by seven other classes at `+0x4c4` (`Pawn` at six
slots, `Controller@30`, `SparkEmitter@47`) and three at `+0x4d0` (`xEmitter@35`, `Controller@52`,
`PlayerController@52`), so a displacement is not a member. **What would settle the tail:** the body of
`FUN_0003d900` past its element loop -- the writer is handed the record array's address from
`UClass::Serialize`, so whatever writes the 3 x 9-byte tail after that loop is in the same function
and not in a member of the class. Numbers: `docs/NATIVE_VM.md`, *The group of three, and the one array
no `Class` code names*.

**2026-11-13 (fifth): the array writer's body is read to its end, and the members behind the array cannot be the
tail's.** `FUN_0003d900` is **306 bytes** with two loops -- the archive's two directions of one array -- and no code
after either of them: both exit into the same epilogue (`0x3d9d6`, `0x3da2b`) and the next body in the symbol map,
`0x3da40`, is the record array's own destructor. So the previous "what would settle it" is answered negatively: the
writer does not produce the `3 x 9`-byte pair list. The four members behind the array are censused over the whole
image (`inventory/member_access_census.csv`, 217 sites over seven displacements): `+0x4e8` 105 (95 of them loads;
the defaults buffer -- its destructor `0x1aa30` is the template's Empty path with no element loop, so it holds
bytes), `+0x4f4` 22, `+0x500` 12 and `+0x514` 17, whose map entry `0x1b310` is a reset/constructor shape (zero
`Num`/`Max`, `PUSH 2`, then `0x1a920`) shared by 328 members rather than a per-element destroy. A byte buffer, two
4-byte name arrays and a 2-byte string member cannot hold a (reference, value) pair. **What would settle the tail**
is therefore its own grammar, which needs no new data: the config name at `+0x4b4` is read as an index through
archive `+0x18` and a number through `+0x1c`, and the tail's (compact, compact) pairs with a negative second term are
that same two-field shape -- parse each pair as one `FName` and ask whether the leading compact value is the pair
count or the first name's index. Numbers: `docs/NATIVE_VM.md`, *The array writer is 306 bytes, and the four members
behind it are arrays*.

**2026-11-13 (third): the tail's grammar is a count and three (name, reference) pairs, and neither field is an
`FName`.** The previous "what would settle it" asked whether the leading compact value is the pair count or the
first name's index. It is the count: the bare-`FName` reading closes **0 of the 219** three-pair tails, and the
count-leading parse closes all 219 exactly. Each pair's second value fits the archive's reference encoding (0 null,
`-v-1` import, `v-1` export) on **225 of 225** parsed tails, and the negative one names the same import --
`Class.Object`, i.e. `Core.Object` -- on **219 of 225**, where the same test one import earlier scatters over five
targets (123 `Class.Emitter`, 52 `Package.Core`, 26 `Class.Pawn`, 12 `Class.Actor`, 6 others). So the four spellings
of that term are one object at four import-table positions, and an `FName`'s unsigned number cannot be it. The first
values classify as the package's own name, `Core` and `System` on 189 rows, which is why a package's classes share
one tail. **What would settle the tail's owner** is now its writer: the tail is written once per package, and
`UClass::Serialize`'s only per-class writers after the record array are the two name-array calls at `0x3a8af` and
`0x3a904`, so the body between the second of those and the defaults branch is the read. Numbers:
`docs/CLASS_DEFAULTS.md`, *The tail's leading value is a count, and each pair's second field is a reference*;
`inventory/class_record_tail.csv`.

**2026-11-13 (fourth): the list is the record's last field, and the reader says so without a boot.** `0x0003a800`'s
listing gives the field order in the reader: `+0x4d0` records (`0x3a8a6`), `+0x4dc` names (`0x3a8af`), the config
name written by **two** virtual calls on adjacent members (`+0x18` on `+0x4b0`, then `+0x1c` on `+0x4b4`,
`0x3a8c2`-`0x3a8d9`, version >= `0x3e`), then `+0x4f4`'s names (`0x3a904`, version >= `0x63`), then the defaults
buffer (`0x3a96d`: `Num=0`, `Max=GetPropertiesSize()`, `CALL 0x55ed0`). Nothing is written between the `+0x4f4`
call and the defaults, so the 6-to-9-byte list is that array -- a count of 3 and three two-byte elements -- and not
an unowned tail; `inventory/class_record_element.csv`'s `second_end` is not a field boundary. **What would settle
the rest** is two reads and no new data: the split of the constant 20-byte middle between the `+0x4dc` array and the
8-byte config name, and what the archive's `+0x18`/`+0x1c` pair actually writes, since one `FName` takes both calls
and the element's second value is signed. Numbers: `docs/NATIVE_VM.md`, *The reader writes nothing between the last
field and the defaults, so the list is a field*.

**2026-11-13: the gap is an array, not three fields, and the empty-list test does not fire.** This entry's
remaining question was the split of the 20 bytes in front of the list. Measured (`inventory/class_record_gap.csv`,
549 classes): a **second array of `FUN_0003d900`'s grammar** parses at the located array's tail on 361 classes,
20 bytes at the median (20 on 260), two records on 321 with reference widths `2+1` on 258, references inside the
object tables on 355, and the same grammar one to eight bytes later parses 811 times but lands on the defaults
start **once**. So the 20 bytes are count + a ten-byte and a nine-byte record, and the eight bytes the "name array
plus config `FName`" reading assigns to the config are a record's second dword: **0 of 355** are a name index and a
counter <= 64 (the second int32's median magnitude is 1.34e9). The falsifier -- a class whose `+0x4f4` array is
empty carries no list -- does not fire: 7 rows carry no list field, 7 carry a single byte holding `0x02`/`0x03`, and
the list closes the record on 219. **What would settle what U17 now asks** (which member the second array is) is a
comparison of the two arrays' reference lists per class: identical values mean one array written twice, disjoint
values mean two members -- both ends are already in the artifact, so it is a column, not a boot. Numbers:
`docs/NATIVE_VM.md`, *The 20-byte middle is a second record array, and the config name is not in it*;
`docs/CLASS_DEFAULTS.md`, *The 27-byte gap is an array and a list*.

**2026-11-13 (fifth): the second array is a different member, and it is a class-reference list.** The test this
entry named -- identical reference lists mean one array written twice, disjoint ones mean two members -- is measured
on 361 classes (`inventory/class_record_gap.csv`): **0** identical in order, **0** holding the same values
reordered, **0** identical byte spans, **333** with no reference in common and 28 sharing one to ten. So the two
arrays are not the same list, and the count matching on 319 rows makes them parallel rather than copied. Their
contents separate them further: the second array's references are **93.5% exports of class `Class`** (738 of 789
resolvable) and 94.4% of its imports, against 4.0% of the corpus's exports, while the first array is 80.3% classes
padded with 735 nulls. **What would settle it now** is the writer: the reader `0x0003a800` calls the record writer
`FUN_0003d900` once (`0x3a8a6`) and the pair writer `FUN_0003d160` twice (`0x3a8af`, `0x3a904`), so a 20-byte
two-record array at the tail is not packed by that body -- a census of the `0x3d900` call sites and the member
displacement each one passes names the offset the second array occupies without a boot. Numbers:
`docs/NATIVE_VM.md`, *The second array is a different member, not `+0x4d0` written twice*.

**2026-11-13 (sixth): the record writer has one call site in the whole image, and it is handed `+0x4d0`.** A call
census (`tools/record-writer-calls.py`, `inventory/record_writer_calls.csv`) scans the executable sections for
`E8 rel32` into the two record writers and decodes back to the instruction that wrote the pushed argument's
register. `FUN_0003d900` -- the 12-byte-record writer -- has **one** call site (`0x3a8a6`, member `+0x4d0`), so a
second array of that element grammar is packed by nothing anywhere in this build, and the "second member packed
elsewhere" reading is dead. `FUN_0003d160` (name array, archive slot `+0x1c` per element) has seven: `+0x4f4`
(`0x3a904`), `+0x70` (`0x500a1`), `+0x2ec`/`+0x2f8` (one function at `0x22d53f`), two whose single pushed argument is
the archive the previous call returned (`0x3a8af` after `0x3d900`, `0x172fe4` after `0x172fe0`), and one from the
stack (`0x66a93`). No site in the image is handed `+0x4dc`, which is the offset the previous block gave the reader's
second call. **What would settle the 20-byte middle now** is what `0x3a8af` invokes with one argument: whether
`FUN_0003d160` has a one-argument entry reachable there, or the reader's field order is off by one call. Numbers:
`docs/NATIVE_VM.md`, *Only one call site writes the record array, and the reader's second call is not a member*.

**2026-11-13 (seventh): the reader's second call *is* handed a member, staged under the previous call's pushes.**
This entry's question was whether `0x3a8af` invokes a one-argument entry of `FUN_0003d160` or the field order is off
by one call; it is neither. The reader emits `LEA EDX,[ESI+0x4dc]; PUSH EDX` at `0x3a897`, before the `+0x4d0`/`EBP`
pair, `FUN_0003d900` is handed two arguments and pops neither, and the `ADD ESP,0x8` at `0x3a8ab` skips exactly those
two -- so `+0x4dc` is still on the stack when `0x3d160` runs and the call is handed `(archive, &+0x4dc)`. The frame
is measured rather than read off the nearest pushes: `tools/record-writer-calls.py` now stack-accounts each site (the
arity from the caller's own `add esp,imm`, the arguments from the topmost dwords of the window, the leftovers
reported per site as staged), and the eight sites answer `record` 1 site / 2 args (leaving `+0x4dc` staged) and
`pairs` 7 sites / 2 args on six. So nothing was missing the `+0x4dc` push; the census's window started after it.
The consequence is that the 20-byte middle is the `+0x4dc` name array's stream -- `0x3d160`'s element is a name
write of one or two bytes through archive slot `+0x1c` -- and not a second array of twelve-byte records. **What would
settle that** needs no new data: parse each class's middle as a count followed by that many archive names and ask
whether it closes on the same 361 classes the twelve-byte parse claims; one of the two grammars accounting for the
bytes to their last one is the field's shape. Numbers: `inventory/record_writer_calls.csv` (`args`, `staged`,
`staged_member`); `docs/NATIVE_VM.md`, the section formerly titled *Only one call site writes the record array, and
the reader's second call is not a member*.

**2026-11-13 (eighth): the reader's field order does not account for the gap either.** The seventh entry named the
middle's writer (`FUN_0003d160`, handed `+0x4dc` at `0x3a8af`) and proposed reading the middle as that writer's own
stream. Measured on all 549 located classes (`tools/class-record-gap.py`, `inventory/class_record_gap.csv`, `nm_*`
columns): the three-part reading -- `+0x4dc` names as a compact count plus compact pairs, the config name as two
compact fields, `+0x4f4` names again, the last ending exactly on the defaults start -- closes on **0 of 549**, and
the shifted-start control closes on 15, so the model is wrong rather than the position. The middle read as a
count-plus-pairs list *alone* parses on 408 of 549 but consumes a median of 6 bytes and leaves 21, matching the
leading count of the twelve-byte array rather than a field. Both grammars therefore fit the bytes and neither fits
the position. **What would settle it** is the writer's reservation rather than another grammar: `FUN_0003d160`
reserves `count*4` through archive slot `+0x14` and then calls `+0x1c` once per element, so a name element carries a
4-byte proxy in memory -- read the middle's leading byte against that, and the count that the two grammars disagree
about (2 for the twelve-byte parse) becomes decidable. Numbers: `docs/NATIVE_VM.md`, *The gap does not decompose in
the reader's own field order either*.

**2026-11-13 (sixth): the `+0x4c4` array has no writer, so the middle is not its stream.** The previous note
left `+0x4c4` -- the 4-byte-element `TArray` between the 8-byte one and the record array -- as the candidate
for the bytes the name parse leaves. The test is its head, not another grammar: data `+0x4c4`, count `+0x4c8`,
max `+0x4cc`, and a writer must write the count or the max. `inventory/class_array_head_census.csv` (70 sites,
`make class-array-head`) finds that on a `Class` **all nine stores into the head are the three constructors' dword
zero-fills**, each writing every head dword with a zeroed register, and no site writes a non-zero count or max; the single
dword store at the displacement (`0x001fd005`) is another class's scalar field, whose body never touches
`+0x4c8`/`+0x4cc`. No site indexes any of the three at any stride, and on a `Class` the only two sites at all
are the region initialiser (`0x3a675`, `push 0x4; call 0x1b846`) and the destructor (`0x3dc60` -> `0x3d850`).
So the member is initialised empty, destroyed empty and never appended, and a stream of it would be `1 + 4n`
bytes (5/9/13/17/21) where the middle is 27, 20 or 23. **What would settle what U17 asks now** is the
`+0x4b0`/`+0x4b4` pair -- the config `FName` the reader writes as two dwords at `0x3a8c2`/`0x3a8d9` -- which
has never been censused: with the twelve-byte array, the compact pairs, the name array and now the 4-byte
array eliminated, the config pair is the last field in the record without a store map. Numbers:
`docs/NATIVE_VM.md`, *The array at +0x4c4 is initialised and destroyed, and no body fills it*.

**2026-11-13 (ninth): the pair is a class reference and a name, and the image names the first itself.** The
sixth entry left `+0x4b0`/`+0x4b4` as the last field of the record with no store map.
`tools/member-access-census.py --disp 0x4b0 --disp 0x4b4` (`inventory/class_config_pair_census.csv`,
`make class-config-pair`) returns 534 sites at `+0x4b0` and 20 at `+0x4b4`, and only three and five of them
belong to a `Class`: 494 of the 534 are one instruction shape, `mov [eax+0x4b0],ecx`, one per function across
493 functions, and the two sampled bodies (`0x2ca80`, `0x317100`) install a lazily created global rather than
write a member of their own. The dword at `+0x4b0` is `ClassWithin`: `0x397b0` loads it, tests it for null
and calls the assert printer with the expression string `ClassWithin` at `UnClass.cpp:1000`. The dword at
`+0x4b4` is the config name, and it is **one** dword: the full constructor installs an argument there
(`0x3b065`, `ret 0x34`), the reader writes it through the archive's name slot `+0x1c` (`0x3a8d0`), and below
version `0x3e` it is `FName(L"System")` first dword (`0x3a8de`..`0x3a8f0`, `UnName.cpp`). So the middle
between the two name arrays is a **reference followed by a name**, not a count plus two name indices -- which
is the assumption every grammar over those bytes has made, and why none of them closed. **What would settle
the middle now** is the reference slot's own encoding applied to the middle's first compact value: it is read
by the archive's `+0x18` (the reference method, measured as one compact field on the element writer by
`tools/archive-slots.py`), so a parse has to try that width where every earlier attempt tried a name index.
Numbers: `docs/NATIVE_VM.md`, *The pair at +0x4b0/+0x4b4 is a class reference and a name, and the image names
the first*.

**2026-11-13 (tenth): the gap is three to five compact values in front of sixteen zero bytes.** The ninth entry left
the array's head 19 to 21 bytes past the field table's own sum and attributed the difference to
`UStruct::Serialize`. `tools/class-record-order.py --gap-sweep` reads those bytes on all 1,626 closing classes
(`inventory/class_record_gap.csv`, `make class-record-gap-front`): the gap is 19/20/21 bytes on 732/518/232 of them,
its **last sixteen bytes are zero on 1,481 of the 1,482**, its last non-zero byte is never past index 3, and the three
to five bytes in front of that run **all parse as `FCompactIndex`** (95 distinct fronts, none overrunning). So the
arithmetic closes at `prefix_end + 42 + 3..5 + 16 = prefix_end + 61..63` and the 1-3 byte spread is the front's
width, not a mis-measured array.

Two things are still open, and they are one question. The sixteen zero bytes are exactly the width of the four dwords
`UStruct::Serialize` writes at `+0x5c`/`+0x58`/`+0x54`/script-size and are zero on every cooked class, but the body
writes them before `UState`'s 22 bytes rather than after the five dwords, so either they are that tail and the writer
order is not the stream's order here, or the tail is absent from the cooked stream as `FriendlyName`, the line and the
text position already are. And the compact run in front of them is written by no body in `0x38dc0`/`0x392b0`/
`0x394c0`/`0x3a800`.

**What would settle it.** A live `UClass` from a link, read at `+0x5c`, `+0x58`, `+0x54` and `+0x4c`: if the tail is
in the stream those members are non-zero on a class with a source line. Statically, the front's values are in the
artifact beside each class's own table sizes, so the naming test is a query: three to five bytes cannot be a count
plus elements, so the candidates are one `FName` plus one or two references, or one reference plus one `FName`.

## U18. What a `delegate` property's default value is

**Observed.** Three class-level values do not render, and they are one property in three classes:
`__OnXControllerEvent__Delegate` (declared `delegate<OnXControllerEvent>`), the first property of its block in
`GUI.MenuItemOverlay`, `StargateMenus.ExtrasPage` and `StargateMenus.CreditsPage`. Each slot is a whole block's worth
of evidence rather than a per-class one: the blocks continue cleanly after the tag and end on the payload's last
byte (trailer 0), so the tag's extent is the format's.

**Measured** (`tools/defaults-residue.py`, `make defaults-residue`, `inventory/defaults_residue.csv`):

| class | type byte | decoded type | size | first index | tiles as indices |
|---|---|---|---:|---:|---|
| `GUI.MenuItemOverlay` | `0x50` | type 0 `Invalid`, size class 5 | 110 | 6 | 97 indices over 110 bytes |
| `StargateMenus.ExtrasPage` | `0xbf` | type 15 `FixedArray`, index flag | 12 | 3 | 10 indices over 12 bytes |
| `StargateMenus.CreditsPage` | `0xbf` | type 15 `FixedArray`, index flag | 12 | 3 | 10 indices over 12 bytes |

**Why it is not rendered.** The tag map this reader uses (UELib's, 0-15: `Invalid` ... `FixedArray`) has no
delegate type, so a delegate's default is not described by the tag grammar. The renderer therefore typed these three
by their nibble -- `String` in most classes, `Invalid` in one, `FixedArray` in two -- and read the bytes as text,
which they are not.

**Measured -- a delegate default is `{object, function}`** (`tools/delegate-defaults.py`, `make delegate-defaults`,
`inventory/delegate_defaults.csv`): 42 class-level delegate values across the 18 packages, and **39 of them tile
exactly as two UE1/UE2 compact indices** (bit 7 the sign, bit 6 "one more byte"):

| index | reading |
|---|---|
| first | a signed **object reference** -- negative indexes the import table, positive the export table, 0 is `Class` |
| second | the **FName index** of the bound function |

All 39 resolve the object to **the class's own export** and the name to **a function that the class or one of its
ancestors declares**, so the shipped default for a delegate is "this class handles it itself": `GUILabelButton` binds
`InternalOnKeyEvent` and `InternalOnXControllerEvent`, `MainMenu` binds `MapKeyEvent`, `SGPageBase` binds
`MyKeyEvent`. The value's **width is the object index's width** -- one byte for export 2 (`SGPageBase`,
`RightLockingPanel`), two for export 107 and up -- which is why the same property is 2 bytes in one class and 3 in
another, under two different tag bytes (`0x17` and `0x57`: same type nibble 7, different size class). Nothing in the
value is delegate-specific beyond that pair: no table, no arity, no state reference.

**The three above are not delegate values of that shape, and the tag offset is only the first one's problem.**
Their first two indices resolve to no declared function, and their widths are outside the band a decoded value
occupies: every one of the 39 is 2, 3 or 4 bytes (the object index's width plus the name's), while these are 110 and
12. `tools/defaults-blockstart.py` (`make defaults-blockstart`, `inventory/delegate_blockstart.csv`) then measured
what the shape could not:

* `GUI.MenuItemOverlay`'s 110 bytes tile as **twelve whole streamed record units** plus two spare bytes -- U17's
  unit, a compact object reference plus two dwords, nine bytes at width 1, which no decoded value is long enough to
  hold even once. The block re-read **ten record units** (90 bytes) later opens on the same property name, carries
  the same tag byte, and reads the same 110 bytes again, so the tag is inside the record array at both starts and no
  start within 160 bytes yields a pair. This row is a **locator** result: the class's delegate default is not
  recoverable by re-locating its block.
* `StargateMenus.ExtrasPage` and `CreditsPage` are a genuine tag: their one-byte-later start names nothing, so 83 is
  the only start whose tags name declared properties. Their 12 bytes are one record unit plus three spare, so the
  record test does not apply, and the two classes differ in exactly one of the twelve.

**What would settle the two menu pages.** Not another start within the block: the tag's extent is pinned by the
property after it (`TitleString`) and by the payload's last byte. What settles it is the reader of the *value* for
this property class -- `UDelegateProperty`'s own serialiser rather than the slot-11 body inherited from the common
base -- or a second package from the same build that ships the same property with a value short enough to be read
directly.

**The writer route is closed at slot 11.** `DelegateProperty`'s class table is `0x004e16a8`
(`inventory/class_constructors.csv`) and its slot 11 is `0x00077f70` -- **byte-identical in shape** to
`StructProperty`'s slot 11 at `0x00079660`: `push [esp+0xc]`, `call 0x77630`, `add esi,0x6c`, `push esi`,
`call [eax+0x18]`, `ret 4`. Two different property classes cannot share a per-class value serialiser, so that slot is
inherited from a common base and is not the delegate reader; the payload measurement above is what answers this, not
another vtable round.

**2026-11-13 (tenth): the tiling test is calibrated, and it does not place the tag.** The entry above rested on
`GUI.MenuItemOverlay`'s 110 bytes tiling as twelve streamed record units. `tools/defaults-blockstart.py --census`
(`make defaults-blockstart-census`, `inventory/defaults_blockstart_census.csv`) asks that question of every block the
locator settles -- **2,209** classes, 16,788 properties -- and the answer is a base rate: **159 of the 181** blocks
whose first value is at least two units wide tile at their own start (87.8%), and the same grammar started 1-4 bytes
into the same region tiles **151/149/147/153 of 181** (82-85%). This class's value tiles as 12 units at its own start
and **12, 12, 12, 12** at the four controls, so the count is a property of the bytes, not of the position. The
alt-start half of that entry is base-rate-common too: **154 of 159** tiling blocks have a later clean start. What
survives is the re-read -- same property name, same tag byte, same 110-byte value 90 bytes on, 11 properties against
20 -- together with the width argument, so the row stays a locator result and the two menu pages stay open. **What
would settle whether any block start in the corpus sits inside the record array** is the array's own head rather than
a tiled value: the count and the `+0x4d0` member from `UClass::Serialize` (`docs/NATIVE_VM.md`) checked against the
located start, a bound the tiling test does not have. Numbers: `docs/CLASS_DEFAULTS.md`, *The record-unit test has an
88% base rate, so it does not locate a block start*.


**2026-11-13 (eleventh): the array's element keeps two raw dwords, so the 20-byte residue is not an element width.**
The route the tenth entry left -- read the writer for the element's widths -- is closed, and it corrects the
reason that entry gave. `FUN_0003d900` passes **three** fields per element to the archive's `+0x18` slot
(`element+0`, `+4`, `+8`, pushed at `0x3d9a3`-`0x3d9b0`), so a three-compact-field element was a live hypothesis.
`tools/class-record-element-fields.py` (`make class-record-element-fields`,
`inventory/class_record_element_fields.csv`) ran it on the 395 classes the shipped walk read at `k=2`: 364 complete,
**0** land on the block start, 363 stop short of the one-reference walk (past it: 1), the span between the two walks
is **17 bytes on 293** of them (18 on 21, 16 on 9) rather than six bytes per element, and the three-field element
reads a median **3.00** bytes against the shipped model's 9. The second and third dwords are raw, the 9-byte element
stands, and what the array's tail leaves in front of the block start -- 20 bytes past the two name arrays, 27 bare --
is still neither the writer's fields nor an element width. **What would settle what U18 asks now** is that residue
read against `UStruct::Serialize`'s own tail (`0x000392b0`), which is where the prefix came from, rather than another
element grammar.

**2026-11-13 (twelfth, corrected the same day): the array's head sits after the sixteen zero bytes, and there
its end is the block start.** The tenth entry left the array's head as the test the tiling census could not be -- the
count checked against the located start, a bound a tiled value cannot give. The first attempt read it at
`prefix_end + 42` and credited 297 heads with a uniform 20-byte margin; that margin was the front. The page's gap
census had already measured the array 19-21 bytes later, after sixteen zero bytes and a 3-5 byte compact front, and
`tools/class-record-bound.py --position` now joins the two readings: the compact value at `prefix + 42 + gap_len`
equals the census's `arr_count` on **1,626 of 1,626** classes, while the value at `+42` is a front value (and equals
the census's first front value on all 234 credited rows that have a census row -- the numbers coincided because `2` is
the commonest small value in both). Read after the zero run, the same credit test admits **4** classes, and on all four
the record ends exactly on the block start: `Engine.PlayerInput` (count 15, ends 217 = block), `Engine.AIMsgManager`
(204, 1,912 = block), `Engine.AdminBase` (69, 724 = block), `Engine.XBoxPlayerInput` (5, 123 = block). **This settles
what the entry asked, in the stronger form** -- where the head can be credited the record's end *is* the block start,
so no located start sits inside the array and there is no residue between them -- and it corrects the first attempt's
claim that a 20-byte gap sat between the two. What it does not explain is unchanged: the two menu pages'
12-byte values, for which the routes remain `UDelegateProperty`'s own value serialiser (not the slot-11 body inherited
from the common base) or a second package from the same build that ships the property with a value short enough to read
directly. Independent of all of it: **32** located rows leave fewer than 42 bytes between prefix and block, which
`UClass::Serialize`'s unconditional fields forbid, so those locator rows are wrong whatever the array's position is.
**2026-11-13 (thirteenth): the 32 starts the record forbids are all at their payload's end, and 24 of them are empty
blocks.** The twelfth entry left **32** located rows that put the defaults block closer to the prefix than
`UClass::Serialize`'s unconditional 42 bytes allow, and called those rows wrong without locating anything better.
`tools/class-record-room.py` makes the locator cheap: a block is a run of tagged properties, so every offset where one
parses and ends at the payload's end is a candidate (`class-defaults.py`'s own `candidates()`, which also bounds the
trailer at 64 bytes), and the record's room bounds the candidate from below. The candidates are not scarce -- **8 to 14
per payload**, median 9 -- so only a tag that names a property the class declares counts, and the 32 declare 5 to 586
names. That leaves **8** re-located and **24** with no declared tag anywhere in the payload:

| verdict | rows | start | room | trailer |
|---|---:|---|---:|---:|
| `Engine.SubAction*` (4) | 4 | 97 | 91 | 0 |
| `KBSJoint`, `HaakenMK2Weapon_Bot`, `P90Torch`, `AttachedDeathGliderTurretController` | 4 | 69/68/68/75 | 63/63/63/70 | 21/24/25/14 |
| empty block at the payload's last byte | 24 | payload - 1 | 90-102 | 0 |

The four `SubAction` classes are the clean ones: their walk start is **8** (room 2) on a two-property parse whose first
tag is `Desc`, and the only candidate that clears the record is a single `Desc` tag at room 91 that tiles the payload
exactly, its value being the string the payload ends on (`FOV`, `Shake`, `Scene Speed`, `Trigger`). That is the locator
finding the same tag the walk found and the record then rejecting the walk's *position* -- the first case here where
the room filter changed an answer rather than a column. The four with tails stop at a tag that begins at a clearing
start and do not tile the payload, so their starts are located and their blocks are not. The 24 name no declared
property in any of their 7-13 parses and their last byte is the `None` terminator, which is an empty block at room
90-102; seven of them are `*VoicePack` classes that `inventory/voice_sets.csv` marks `silent`, the same payloads
`docs/systems/AUDIO.md` calls the class record alone on 16 of 25 packs. **What is still open** is two things, and both
are the tail: whether the four tails are a second table after a single tag, and whether the 24 carry a bare terminator
or a one-tag block whose name the cooked stream does not declare. The route is `class-defaults.py`'s
`single_declared_block` against a class whose single declared property is known from its script text.

**2026-11-13 (fourteenth): the record's own tail settles the four re-located starts, and leaves the 24 as a
negative.** The thirteenth entry asked whether the four tails are a tag followed by a second table and whether the 24
carry a bare terminator, and named the route `class-defaults.py`'s `single_declared_block` against a class whose one
declared property is known from its script text. That route works, and it supplies the control the thirteen entries
were missing: `Engine.SubActionCameraShake`, `SubActionFOV`, `SubActionSceneSpeed` and `SubActionTrigger` each declare
one property and set it, so the single declared tag that tiles the payload is the block, at start **97** on all four.
Reading forward from the record's last streamed field, every one of the 32 payloads ends in a run of units
(`<name index> 01 00 00 00 <4 bytes>`) and a short closing run -- `02 01 02 83 03 00` on the anchors, with the block
immediately after it -- and corpus wide a located block start sits **6 to 9** bytes past the last unit's flag dword on
**1,180 of 2,204** rows (mode 7 with 449; 59.8% within 12), so the closing run is the record's and not the block's.
That refutes all four re-located starts: `KBSJoint` 69, `HaakenMK2Weapon_Bot` 68 and `P90Torch` 68 are one byte before
a unit's flag dword and `AttachedDeathGliderTurretController` 75 is inside one, and the parse accepted them by reading
the dword as a property value (`bHidden`, `FidgetAnims`, `RunMovementAnims`, `KParams`). **What it does not settle is
the 24**, and it settles them only as a negative: no single declared tag tiles any of their payloads anywhere, the
same test that finds all four anchors, so the payload carries no tag the class declares -- which is compatible with an
empty block but does not show there is no tag syntax inside the closing run. **What is still open** is therefore the
closing run's own grammar: its count and index bytes on the payloads whose block is a known tag, joined against
`inventory/class_record_gap.csv`'s array counts, which would say whether those six bytes are the tail of the
version-gated `+0x4f4` name array or a field of its own.

**2026-11-13 (fifteenth): the closing run is the writers whole group, and the six bytes are a names array, an
import reference, a config name and an empty list.** The fourteenth entry deferred the run's grammar and named a
join against `inventory/class_record_gap.csv`'s array counts. `UClass::Serialize` fixes the order -- `+0x4dc` as
names, `+0x4b0` through slot `+0x18`, `+0x4b4` through slot `+0x1c`, then `+0x4f4` as names when the archive
version is at least 99 -- and `inventory/archive_slots.csv` measures both slots as one compact field, so
`tools/class-tail-run.py` parses exactly that group and requires the parse to end on the block start. It holds on
**953 of the 1,318** rows whose run is at most twelve bytes (72.3%), and on 984 of the 2,139 located rows overall.
The byte that defeated every earlier reading is the reference: compact `83` is **-3**, and the field is negative on
981 of 984 parses with every magnitude inside the import table -- an import-side reference. The `+0x4f4` array is
**empty on 801 of 984** classes. **What the join settles, negatively**, is the census: `class_record_gap.csv` says
`nm_mid_count = 2` on all 294 overlapping rows while this tool reads 3 or 4, so that column is measuring a
position inside the record's unit stream and its `nm_*` block needs re-deriving before it is quoted.
**What is still open** is that re-derivation: join each row's `last_unit + 8` to the census's `array_end` and, if
it lands inside a unit, recompute the census from the group above. Nothing here needs a boot or a decompile.

**2026-11-13 (sixteenth): the census's array tail is a unit body, and the counts to quote are the groups.** The
fifteenth entry left the `nm_*` re-derivation open and named the join -- each row's `last_unit + 8` against the
census's `array_end`. On the **545** rows both artifacts carry the tail is at the group's start on **none**: before
the record's last unit on 402 (mode -12 on 262), past the block start on 93, inside the closing run on 49, exactly
on the block on 1. On the 294 rows that are before the last unit and parse here, the census reads
`nm_mid_count = 2` on all 294 with `nm_shape = open` on all 294, and its `nm_exact` closes on **0 of the 330**
overlapping parses, so that block describes the record's unit stream and not its arrays. Re-derived at the group
start the same rows read 3 names on 217, 4 on 69 (2 on 6, 5 on 2), a config name inside the name table on
**294 of 294**, and an empty `+0x4f4` list on 280 (6 on 10, 4 on 4). **What is still open** in the closing run is
nothing: its grammar is measured from both sides and `make class-tail-run` prints the join. What is outside this
entry is the 93 rows whose located array lies inside the defaults block -- the locator's question, not the run's.
Nothing here needs a boot or a decompile.

**2026-11-13 (seventeenth): the 95 overshoots are not fixed by another head, so the head census is no discriminator.**
The sixteenth entry named the rows whose located array lies inside the defaults buffer, and proposed reading the
locator's candidate list on them. Measured (`tools/class-record-overshoot.py`, `inventory/class_record_overshoot.csv`):
**95 of the 549** located rows end past the block start (17.3%; mode 105-109 bytes, deepest 281, median 30.4% of
the payload), and a free head closes exactly on the block start on **55** of them against **316 of 454** (69.6%) on
the rows that land correctly. The overshoot rows carry a head *less* often than the control, so the candidate list is
a property of the payload rather than a discriminator, and "the locator should prefer another head" is refused.
Where a head exists on an overshoot row it is at `prefix + 61` (32 rows) or `+62` (14), count 2 on 33, two name tails
on 35, references in range on 50 of 55 -- the same shape as on the correct rows. The record side carries 93 of the
95 and parses the closing group on 36, and there the 6-byte run ends on the block start, so the block is confirmed
from the record exactly where the located array overshoots it. **What is still open** is what to do on the 40 rows
where no head closes either: none of the three witnesses (the documented array end, a free head, the record's
group) is available there, so the test is the defaults stream -- `tools/class-defaults.py`'s tag parse from
`block_start`, which closes on 543 of 551 -- and whether it parses on those 40 would confirm the block start without
using the record at all. Nothing here needs a boot or a decompile.

**2026-11-13 (eighteenth): the defaults stream is the witness the 40 headless rows were missing, and the block start
holds on 92 of the 95.** The seventeenth entry left the 40 rows where the located array, the head census and the
record's closing group are all silent, and named the test: the tag parse from `block_start`. Read there over all 95
rows (`tools/class-overshoot-stream.py`, `inventory/class_overshoot_stream.csv`) the stream terminates on the
payload's last byte on **37 of the 40** and on **55 of the 55** rows that do carry a head, so the block start is
confirmed without the record on 92 rows -- and the 36 rows the record confirms are exactly the 36 on which the stream
closes too, so the two witnesses never disagree. A close alone is a 33% base rate (125 of the 380 starts at
`block_start + 1 .. + 4` close as well, and 70 of the 95 rows have a closing neighbour), so the column that separates
them is the names: on the 44 rows whose class declares properties the block start names more declared tags than any
closing neighbour on 41, the same number on 3, fewer on none. **What is still open** is the three rows that stop short
-- `Engine.KBSJoint` 41 bytes, `StargateContent.CTFGateTeleporter` 49, `StargateVehicles.HoverTankPilotBot` 59, all
inside the 64-byte trailer `class-defaults.py` allows -- where the block start is plausible but not pinned to the last
byte, and `HoverTankPilotBot` is the one row no witness reaches. Settling them is the trailer's question and not
another start: parse the tag stream from each offset in `[block_start, block_start + 59]` on those three and report
whether any of them lands on the payload's last byte with declared names -- a start that does makes the trailer a
misplaced boundary, and none makes those 41-59 bytes a field the class serialises after its defaults. Nothing here
needs a boot or a decompile.

**2026-11-13 (nineteenth): the same parse on every located start says the close is the norm, so the witness is a
non-refutation and the names are the discriminator.** The eighteenth entry read the defaults stream from the block
start on the 95 overshoot rows and found 92 closing on the payload's last byte. Measured against the whole corpus
(`tools/class-overshoot-stream.py --corpus`, `inventory/class_blockstart_stream.csv`, the 2,204 rows of
`inventory/class_record_bound.csv`) that rate is ordinary: **2,113 of 2,204 (95.9%)** close, and the 91 that stop short
leave a trailer of 1-64 bytes (mode 1 on 22, 43 on 6, 41 on 5, with 44 of them in 33-64). The 95 split 451 of 454
(99.3%) where the array lands at or before the block against 92 of 95 (96.8%) past it, so the block start is
consistent with the payload everywhere and the witness confirms nothing on its own. What it does settle is that the
three short rows are not outliers: 41, 49 and 59 bytes sit inside a 44-row bucket of the corpus. The position
control is on the same order -- 3,394 of 8,816 later starts close (38.5%) -- and the name column is what separates
them: among the 919 rows whose class declares properties and whose neighbour closes, the block start names more
declared tags on 606, the same on 299 and fewer on 14 (1.5%). **What is still open** is unchanged and is the
located array, not the start: what the 95 share is the array's own tail. **What would settle the three short rows**
is still the trailer read named in the eighteenth entry -- the tag parse from each offset in
`[block_start, block_start + 59]`, looking for a start that lands on the payload's last byte with declared names.
Nothing here needs a boot or a decompile.

**2026-11-13 (twentieth): the trailer read finds no later start that names a declared property, so the short rows'
41-59 bytes are a field after the defaults and not a misplaced block start.** The eighteenth and nineteenth entries
named the test and this is it: `tools/class-overshoot-stream.py --trailer 59` reads the same property parse at every
offset in `[block_start, block_start + 59]` on the three rows that stop short
(`inventory/class_trailer_scan.csv`, 164 offsets). Later starts reach the payload's last byte on **3 of 48**
offsets on `Engine.KBSJoint` (+39, +41, +44), **0 of 56** on `StargateContent.CTFGateTeleporter` and **5 of 60** on
`StargateVehicles.HoverTankPilotBot` (+20, +38, +56, +57, +59) -- 8 of 164 (4.9%) against the 38.5% the corpus
measured at `block_start + 1..4`, so a close is *rarer* inside the trailer than at an ordinary neighbour. **Every one
of the eight names zero declared properties**: two of the three rows carry no row in `inventory/class_properties.csv`
so the name test is silent there, and on `HoverTankPilotBot` (591 declared names in the closure, 1 matched at the
block start) the closing parses resolve to `Timer`, `Engine`, `Tweak_Pawns_HoverTankPilot` and `InVal`, none a
declared property, while the block start names `Mesh` and `ControllerClass`, one of which is declared. So the block
start is not misplaced on any of the three and the trailer is a region serialised after the defaults block. **What
would settle what the region is** is the writer's own body -- `Core/0003a800_Core_UnClass.c` in
`$WORK/module-export`, read between the defaults call and the function's return for the next archive write -- or a
second class whose trailer is byte-identical (a shared constant field). Nothing here needs a boot or a decompile.


**2026-11-13 (twenty-first): the located array's tail is its head, and at the writer's head the record closes on the
block start on 1,612 of 1,612 rows.** The seventeenth to nineteenth entries left one question: what the 95 overshoot
rows share, since the block start and the payload are consistent on all of them. The answer is the walk's head.
`tools/class-record-array-head.py` (`make class-record-array-head`, `inventory/class_record_array_head.csv`) reads
the array at `prefix + 42 + gap_len` -- the position the gap-front census measured the count at -- and then parses
`UClass::Serialize`'s closing group from the array's own end. On the **1,612** located rows that carry a gap-front
row: the count equals that census's `arr_count` on **1,612**, the block start is past the array's end on **1,612**
(never inside or on an element), and the array's end plus names-ref-name-list lands exactly on the block start on
**1,612**, with every name index in range on 1,612. The residue is the group's own 6-9 bytes (median 7), and the
reference is negative on 1,604 of them. The 95 overshoot rows are inside that: 89 carry a head and all 89 close. **The
field the walk over-consumed is therefore not a field of an element** -- it is the 19-21 bytes in front of the
count: the compact front and the sixteen zero dwords, read as a count plus elements. The control that says so is the
value: the count the shipped walk reads equals the front's first value on 413 of 413 rows (and the real count on 256
of them, which is why it looked right). **What is still open** is the 592 located rows with no gap-front row, and
that is a locator question, not a grammar one: the same closure can be tested from the block side by walking back
through the group to a count. Nothing here needs a boot or a decompile.


**2026-11-13 (twenty-second): the head cannot be located from the block side where the closing group is absent, and
that is exactly the 578 rows the census is silent on.** The twenty-first entry named the test: try each start in
`[prefix + 42, prefix + 42 + 40)`, walk the element grid, and accept the head whose walk `class-tail-run`'s closing
group lands on the block start from (`tools/class-record-array-search.py`, `make class-record-array-search`,
`inventory/class_record_array_search.csv`). On the **1,626** control rows the census supplies, there is a candidate on
1,616 (99.4%), it is unique on 1,399 (86.0%), and **the census head is among the candidates on 1,612 of 1,616
(99.8%)** -- so the block side reproduces the measured position from a different direction, at offsets 19 (730), 20
(519) and 21 (233). On the **578** rows without a census head there is a candidate on **13 (2.2%)**. **The reason is
the anchor, not the search:** 521 of those 578 carry a `class_tail_run.csv` row and none of the 521 parses as
names-ref-name-list (984 of 1,618 on the control population), so no closing group precedes the block start there and
the record's field order does not reach that block at all. The 13 that do resolve sit at offset 32-34, a longer front
(`SquadAIBasic` 471 elements, `CTFFlag` 80, `GameObject` 59). **What is still open** is the other half of the same
question: the 565 blind rows need an anchor that is not the group -- the class's own element count from the property
inventory, or the block's tag stream read backwards to the last element -- and the 217 ambiguous control rows need the
grid test that separates two candidates (the earliest wins here by rule, not by measurement). Nothing here needs a
boot or a decompile.

**2026-11-13 (twenty-third): the blind rows are not a hole -- the element grid walked back from the block start locates
368 of 575.** The twenty-second entry left the 565 rows no group precedes with one anchor named and untried: the
block's own side. `tools/class-record-array-blind.py` (`make class-record-array-blind`,
`inventory/class_record_array_blind.csv`) walks the record's element grid -- a compact reference of 1-5 bytes inside the
package's tables plus two raw dwords -- back from the block start, and accepts a compact count in
`[prefix + 42, prefix + 42 + 40)` whose decode ends where the walk is and whose value is the number of elements walked.
No group is parsed. On the **1,629** rows the group parse does resolve it reproduces the gap-front census's head on
**1,569 of 1,616 (97.1%)** and the group's own head on **1,585 (97.3%)**, at start offsets 5-9 -- the group's width,
measured from the far side. On the **575** rows the group does not reach it finds a head on **429 (74.6%)**, and on
**368 (64.0%)** that head carries the sixteen zero bytes the gap-front census measured in front of the count, at
offsets 19-21 past `prefix + 42` on 363 of them. Those rows are short arrays (median count 3), which is why the group
was absent: a two- or three-element tail does not parse as names-ref-name-list. The null -- the same walk one byte past
the block start -- answers on 4.4%, so the anchor is the discriminator. **What is still open** is the **146** blind
rows with no head at all, **41** of which die at depth 0 (no in-table reference immediately in front of the tags, so no
element ends there), and the 61 whose head lacks the front and so stays a candidate; and the ten rows the census has a
head for while the group does not parse. Nothing here needs a boot or a decompile.

**2026-11-13 (twenty-fourth): the blind remainder is not a longer trailer -- widening the window buys 33 control rows
and zero blind ones.** The twenty-third entry located 368 of 575 blind rows by walking the element grid back from the
block start, starting at most 13 bytes behind it. `class-defaults.py` tolerates a 64-byte trailer, so
`tools/class-record-array-blind.py` now runs the same walk from every start in `[block_start - 40, block_start]` and
carries both answers in `inventory/class_record_array_blind.csv`. On the control the census head is reproduced on
**1,601 of 1,616 (99.1%)** against 1,569 (97.1%), the 33 gained rows are all census rows (their winning start is 14-21
bytes back, just past the group's 6-11), and the widened null -- a start past the window's own far end -- answers on
14 of 2,204 (0.6%), so the extra room fits nothing. On the blind rows the count with the sixteen-byte front is
**368 in both windows and 0 rows gain it**: the 82 extra heads the wider window finds carry no front, and the 207 blind
rows that lack the front do not gain one. **What is still open**, therefore, is not the gap but the array's own
content: the **207** blind rows whose count is not in `[prefix + 42, prefix + 42 + 40)` or whose element reference is
outside the package's tables, of which 41 have no element boundary at all immediately in front of the tags. Nothing
here needs a boot or a decompile.

**2026-11-13 (twenty-fifth): the missing count is not outside the window, and out-of-table references are not the
block -- read forward, the walk fits one byte past the tags as often as at them.** The twenty-fourth entry named two
explanations for the 207 blind rows with no sixteen-byte front, both of them bounds of the backward read: the count is
outside `[prefix + 42, prefix + 42 + 40)`, or an element reference is outside the package's tables.
`tools/class-record-array-blind.py` now also reads each row forward: every position in `[prefix + 42, block_start)` is
taken as a count, its elements are walked forward (compact reference of 1-5 bytes inside the tables plus two dwords),
and the walk is accepted when it ends on the tags within the group's 0-11 bytes; the count is read again as a raw
dword, and a third pass admits references outside the tables. The forward read answers on **1,623 of 1,629 control
rows (99.6%)** with the group parse's head among the fits on 1,529, and on **482 of 575 blind rows (83.8%)** -- but its
null, the same walk aimed one byte past the block start, fits on **2,100 of 2,204 rows (95.3%)** with mean fits per row
2.06 against 2.05 at the block (1.86 against 1.76 blind), so the 482 are chance fits and the read locates nothing. On
the 146 rows the backward walk finds nothing at all, the forward read's 54 answers are matched by its null on 65 (mean
0.71 against 0.90). Admitting out-of-table references gains 5 blind rows and 1,626 of 1,629 control rows fit without
admitting any, so the tables were never the constraint; the raw-dword count answers on 33 blind rows against a 59.9%
null. The defaults block is not in doubt either: it reaches its `None` terminator on all 2,204 rows (median 34 bytes,
4 properties) and closes on the payload's last byte on 2,113 (485 of the 575 blind ones). **What is still open** is
therefore neither the window nor the tables: on the 207 rows no count-plus-grid exists at *any* position in the gap,
so the record's extent has to come from a bound that is not a payload fit -- the writer side, `UStruct::Serialize`
(`0x000392b0`) and `UClass::Serialize` (`0x0003a800`), which is the same route U17 left open. Nothing here needs a
boot or a decompile.

**2026-11-13 (twenty-sixth): the 3-5 byte front is not a name, a property tag or an object reference -- every reading
it passes is a small integer's base rate.** The gap-front census left one question: which field the compact run in
front of the sixteen zero bytes is. `tools/class-record-front.py` tests four readings on all 1,490 gap rows that carry
a front, each against three same-length controls. Every value is a valid name index on 1,484 (99.6%) -- and on 1,484
for the same window one byte later, so the reading has no power. Every value is a valid object reference (with `0` as
null) on 5,457 of 5,468 (99.8%), and on 5,468 of 5,468 one byte later: the control is *better*. The one reading that
can fail, whether the first value names a `Property` export of the class itself, answers on **1 of 1,490** against 0,
1 and 0 for the controls, so the front is not a property tag. And **1,479 of 1,490 fronts contain no value that
needed more than one byte**, so every value is 0-63 and cannot be a name or export index above 63 -- which is most of
both tables -- while the only field `UStruct::Serialize` (`0x000392b0`) names between its second reference and the
record's four zero dwords is `FriendlyName`, an `FName` of two compacts: 8 fronts are two values and the rest are three
to five, so the front is a field wider than the one the writer puts there.

**Settled 2026-11-13 (`tools/class-record-open-front.py`): the front is a record field.** The population that decides
it is the 578 located rows the walk does not close on, which carry no gap-census row by construction -- so nothing
found there can be the walk's offset. The same 3-5 value + sixteen-zero-byte shape is at `prefix + 42` on **515 of
them (89.1%)**, with the same width range (19/20/21) and zero run as the population this test reproduces the census on
(1,607 of 1,626 closing rows, exact census length on 1,473 of 1,607), against a local base rate of **0.000** for the
same test at 24 offsets just past the head in the same payload. From that head the element walk lands within 9 bytes
of the block start on 245 of the 515, so the open rows fail after the head, not at it.

**Settled 2026-11-13 (twenty-seventh): the 1-9 bytes the element grid stops short on are the closing group, and the
block start sits inside it.** `tools/class-record-open-tail.py` parses `UClass::Serialize`'s four closing fields from
the element end with no required end: it closes on **245 of the 245** rows that stop 1-9 bytes short, on 475 of 475
walked open rows, and on the closing population it ends exactly on the located block start on **1,596 of 1,603
(99.6%)** -- so the free parse reproduces the anchored one. On the open rows the group's end lies 2-10 bytes **past**
the located start (never at it, never short of it): the start falls inside the group's names array. The property
stream names the block: on the 245 the located start's first tag is a package or class name read as a tag and is
fully declared on **2 of 245 (0.8%)**, while the group's end opens a declared property and is fully declared on **126
of 207** non-empty streams (60.9%). The unanchored parse alone is not the evidence -- it also fires at 3 of 12
neighbouring offsets -- the pair is.

**What is still open** is the front's identity -- the writer-side read (`UStruct::Serialize`, `0x000392b0`, between
its second reference and the four zero dwords) is still the route, and the compact-versus-`FName` question is
unchanged -- plus the block-start chooser itself: it
picks a start inside the group on 243 of the 245, so every count that reads `class_defaults_per_class.csv`'s
`block_start` as the record's boundary is 2-10 bytes early on those rows. Rejecting a candidate that falls inside a
committed closing group is the next measurement, and the group's start and end are now per-row committed. Nothing here
needs a boot or a decompile.

**Answered 2026-11-13 (twenty-eighth): 42 of those 63 are not shapeless, their head sits 10-48 bytes later.**
`tools/class-record-no-shape.py` scans `prefix + 42 - 16 .. + 48` with the same shape test: 21 of the 63 answer nowhere
in that window, and 42 answer at a distant offset (`k` = 11 on 11 rows, 20 on 7, 10 and 40 on 4 each, the rest 12-48),
from which the element walk lands 0-9 bytes before the located block start on 35 of them (residue median 7, within 3 on
only 3). The control refuses the easy reading twice: the 515 rows whose shape is at `prefix + 42` also fire at
`k` = -1/+1/-2/-3 (515/510/492/457), the front's leading zeros merging into the run, so rows are compared by count
position; and 188 of those 515 carry some hit at `|k| > 4`, so distance alone is not evidence. The 21 that answer
nowhere, and the reason the head sits later on the 42, are what stays open -- the writer side (`UStruct::Serialize`,
`0x000392b0`) is still the route.

**Answered 2026-11-13 (twenty-ninth): the chooser's start is inside the group, and refusing it recovers 281
blocks.** `tools/class-blockstart-guard.py` makes the closing group a rule in the chooser and re-runs it: a candidate
start that a committed group **covers** is refused, and a committed group's **end** joins the candidate set. On the
2,190 readable rows of `class_defaults_per_class.csv` the guard fires on **297 (13.6%)** and **252 move**; the refused
starts had a mean declared fraction of **0.364** (fully declared on 13 of 297) against **0.919** (fully on 231 of
252) at the start the guard leaves. 224 of the moved rows are answered by the group's end -- many of them
**one-property** blocks the two-property floor withholds -- and 28 by another parsed block; 45 rows are refused with
no replacement. On the 245 open rows the previous entry named, the guard refuses **245 of 245**, moves 216, lands 206
exactly on the group's end and takes fully-declared from **2 to 206**. Over the population, fully declared moves from
**1,655 to 1,936 of 2,190 (75.6% to 88.4%)**, 62 of them from the group-end candidate alone on cleared rows. The
controls hold: joined to `class_record_open_tail.csv`'s element-side `overlap`, the guard refuses **282 of 481** rows
whose group ends past the start (58.6%) and **10 of 1,596** whose group ends on it (0.6%), and the commitment test --
a group's reference must be negative, as the archive's reference slot writes imports -- holds on **297 of 297**
covering groups, which is what keeps zero padding from covering every start. The chooser reproduces the shipped start
on **2,188 of 2,190** (`Engine.Speech` and `StargateGame.CTFFlagMesh` are one chooser version behind), so a movement
is the guard's.

**What is still open.** The guard's start is a separate artifact: `class_defaults_per_class.csv` still carries the old
start and five artifacts and pages downstream of it would move, so switching the chooser is its own change. The **45**
refusals with no replacement (`Engine` 17, `UWindow` 10, `StargateGame` 7, `GamePlay` 6, `Fire` 4, `UnrealEd` 1) are
the next measurement: on them every parsed candidate is inside a group, and the free group parse looks like it
over-reads (`Engine.SavedMove`: group at 347, ends at 359 of a 360-byte payload, leaving no block), which the anchored
parse -- every index in range and the list ending at or before the block -- would show.

**Answered 2026-11-13 (thirtieth): the 45 refusals with no replacement are an empty defaults block, not an
over-read.** `tools/class-group-stop.py` reads the property tag stream from the closing group's end on the 297 rows
`class_blockstart_guard.csv` refuses and on a control: **234** of the 297 open a block that runs to the payload's last
byte (fully declared on 224 of 241 non-empty streams, mean 0.929), **55** open an **empty** block, 6 overrun after a
tag or two, 1 stops short and 1 group ends on the payload's last byte. Split by the guard's own verdict: of the 252
rows it **moved**, a block opens on 232; of the **45** it refused with no replacement, a block opens on only **9** and
**36 are empty**, and on **36 of 45 the group ends within a byte of the payload** -- those classes set no defaults
after their record, so there is no start to find and the refusal stands. The control is the instrument: on the
**1,586** rows whose group end is the located block start by two witnesses (`class_record_open_tail.csv`'s
`overlap == 0`), the same reader opens a block to the payload end on **1,586 of 1,586**, fully declared (mean 1.000,
`Emitters` first on 185), so the 19% that open nothing on the refused rows is the payload's shape and not the reader's.

**What is still open.** Thirteen refused rows are not accounted for by an empty block at the payload's end: 6 parse
nothing at the group end with 4-10 bytes after it, 7 open an empty block with 2-459 bytes still after it (274, 296 and
459 among them), and `StargateVehicles.HoverCraftFixedGun` opens one tag with 108 bytes left. And `class_defaults_per_class.csv` still carries the pre-guard
start, so the 234 rows this page puts a block at the group's end are still read 2-10 bytes early by every count
downstream of that artifact: switching the chooser and re-running the five artifacts that read it (`class_defaults_emit`,
`class_defaults_coverage`, `class-defaults.py --coverage`, `docs/CLASS_DEFAULTS.md`, `docs/STATUS.md`) is the change
this leaves.

**Answered 2026-11-13 (thirty-first): the 13 refused rows split 7 with a boundary 3, 4 or 11 bytes past the group and
6 with no block at all.** `tools/class-group-after-scan.py` reads the tag stream from every offset in
`[group_end, group_end + 12)` on those 13. Offset 0 answers on **0 of 13**; some offset answers on **7** --
`Engine.Weapon` at **k=3** (34 tags, all declared, 271 bytes to the payload end), the two `ZatGun_*` rows at **k=4**
(one tag, `AmmoName`) and all four FORMATION rows at **k=11** (47/32/21/7 tags, `offset` first). The six that answer
nowhere (`Engine.Admin`, `Engine.AvoidMarker`, `Engine.ProceduralSound`, `Engine.SoundGroup`,
`StargateContent.DecipheringTrigger`, `StargateContent.HackingPanel`) leave only 2-6 bytes after the group and read as
empty, short, undeclared or unreadable at all twelve offsets, so they carry no defaults block at all. The missing width
is 3 on `Weapon` (its three bytes plus the group's last are `00 00 80 3f`, a float 1.0) and the same eleven bytes on
all four FORMATION rows, so it is a class-family field plus the group's optional tail, not one constant. The control
makes the hit mean something: on the **1,586** rows whose group end is the located block start by two witnesses offset
0 answers on **1,586 of 1,586** and the widest reading picks it on **1,586 of 1,586**, while **1,180 (74.4%)** of them
also answer at a second offset -- 1,888 extra answers -- so a bare "some offset answers" is a 100% base rate.

**What is still open.** `class_defaults_per_class.csv` still carries the pre-guard start, and the chooser does not yet
read the 3, 4 and 11 byte offsets: switching it and re-running the five artifacts downstream (`class_defaults_emit`,
`class_defaults_coverage`, `class-defaults.py --coverage`, `docs/CLASS_DEFAULTS.md`, `docs/STATUS.md`) is the change
those seven rows and the 234 moved ones leave. `StargateVehicles.HoverCraftFixedGun` (one tag, 108 bytes left) is the
one refused row that opens a block and stops short, and the six rows with no block should be recorded as having no
defaults rather than refused.

**Answered 2026-11-13 (thirty-second): the chooser carries the group rule, and its recovered block is one tag.**
`tools/class-defaults.py` now refuses a candidate start a committed closing group covers, takes the group's end as a
start, and takes the offsets in `[end, end + 12)` when that end opens an empty block. **373 of 2,319** starts move
(+4 on 100, +3 on 83, +7 on 16, +89 on 4), and the reference test had to be corrected first: an `FCompactIndex` is
five bytes, so a parse can carry -13,555,990,527, and of the **1,654** control groups that end on the shipped start
**1,653** are inside the import count (median 6, largest 42) while **18 rows' only covers are outside it** (`Engine`
holds 120 imports). The recovered blocks are **one property each on 316 rows** (0 before), so the two-property floor
withholds the verdict and the head-line moves from 1,664 to **1,663 fully declared**, 110 -> **122 with no block**,
`single-declared` 263 -> 288 of 656. **What is still open.** Whether a declared tag that tiles the payload's tail from
a group's end is a block or a table tail -- the floor says it is undecidable without a tag walk past the group or one
stock `ucc` build -- and the 58 rows that move without their declared fraction changing (22 start to start).

**Answered 2026-11-13 (thirty-third): the 58 moves are the group's own bytes, and 14 of their replacements sit in front
of the group.** `tools/class-defaults-moved.py` recomputes the pre-rule choice and places both starts against the
committed groups: all **58** pre-rule starts sat inside a committed group, so the refusal is the only cause -- **22** to
a different start and **36** to no block, all 36 at fraction 0. The new starts are **36** with none, **14 before the
payload's first group** (naming nothing the class declares; on **11 of the 14** the first tag is a package name such as
`Core` or `Gameplay`, a name-table entry that cannot be a property tag), **5** in the 12-byte window past a group's end
and **3** exactly on one. The "past the payload's last byte" option the previous entry named is empty by construction:
`candidates()` drops any start whose parse overshoots the payload. **212** of the 1,663 published blocks still name no
declared property, so `defaults = 1` on them is not evidence of a block. **What is still open.** Whether those 212 keep
`defaults = 1`; the settling floor is the trailer-0 witness `guarded()` computes into `kept` and then discards -- **16
of the 1,972** refused rows publish a nonzero-trailer start because of it.

**Answered 2026-11-13 (thirty-fourth): the chooser's trailer floor is in force, and it withdraws 16 starts that named
nothing.** `guarded()` now applies its "a candidate must run to the payload's last byte once the guard refused
something" floor to the list it returns; it had been built as `kept` and dropped, with the return re-filtering the
unfiltered `cands`. Chosen starts **2,197 -> 2,181**, classes with no block **122 -> 138**, published blocks naming no
declared property **212 -> 196**, fully declared unchanged at **1,663** (all 16 were fraction-0 parses); coverage
ambiguous 354 -> **291**, compile readiness 1,823 with `no-defaults-block` 91 -> **105**. The unchanged-fraction
population grows 58 -> **61** (block losses 36 -> 52, start moves 22 -> 9) and none of the 61's new starts leaves a byte
behind. **What is still open.** The **196** zero-fraction blocks: no byte test settles them, it needs a tag-grammar walk
past the group over those payloads or one stock `ucc` build of a class whose block is a single declared tag.

**Answered 2026-11-14 (thirty-fifth): the 196 read names that are not properties, and no offset of their payloads
names one.** `tools/class-defaults-silent.py` (`make class-defaults-silent`, `inventory/class_defaults_silent.csv`)
walks the locator's grammar from each of the 196 published zero-fraction starts and classifies every tag against the
corpus. **120** of the 196 parses run to the payload's last byte and **76** stop on a `None` name with **1..62** bytes
left (median 41). Of the **440** tags read, **374 (85.0%)** name no property of any class -- **166** plain names,
**127** package names, **68** `Function` and **13** `Class` exports -- and **125 of the 196 (63.8%)** read a package
name, which cannot be a property tag at all; the remaining **66** tags are property names of *another* class. Walking
every offset of every payload, a `>= 2`-tag parse to the payload's end opens at **0-18** offsets (median 3, a median
2% of the payload) and **none** of those parses names a declared property: `best_declared = 0` on all 196, so the
locator missed no block and there is no reading of these bytes that names one. **What is still open.** The label
itself: `defaults = 1` still stands on all 196 while the names support none of them, and withholding it is a rule in
`choose()`/`guarded()` (a parse that names no property of the class anywhere in the payload is not a start) plus the
five downstream artifacts.

**Answered 2026-11-14 (thirty-sixth): the label is withheld from the 196, and the block count is 1,985.**
`tools/class-defaults.py` refuses a chosen start whose parse names none of the class's declared properties
(`all_packages()`, and the same rule in `coverage()`), withholding the start rather than moving it. Blocks
**2,181 -> 1,985**, classes with no block **138 -> 334**, fully declared unchanged at **1,663** (all 196 parsed zero
declared names), ambiguous **291 -> 197**; `class_defaults_misses.csv` explains the 196 as `empty-block`
(135 -> **331**) with `incomplete` falling 200 -> **4**, and compile readiness keeps **1,823** while its blockers move
to `no-defaults-block` 249 / `defaults-ambiguous` 247. **What is still open.** `tools/defaults-emit.py` still locates
class blocks with its own path and still reports 2,209 blocks and 15,518 rendered properties, so the renderer is the
one artifact that has not moved with the label.

**Answered 2026-11-14 (thirty-seventh): the renderer withholds the same label, and its residue is the 87 the group
guard accounts for.** `tools/defaults-emit.py` now locates through `choose_scored()`, the same named-property
floor, on both of its paths (`block_of` for a class, `block_for_export` for an `editinline` subobject), and
`--explain` reports the shape as `no block: the chosen parse names no declared property`. Blocks
**2,209 -> 1,898**, `dirty_properties` **1,267 -> 491** (the 311 withheld parses held 776), and nothing that
renders moves: clean blocks **1,664**, properties **15,521**, rendered **15,518**, subobjects 1,313 / 15,798 /
15,790. The renderer is still **87 blocks short** of `class-defaults.py` (1,898 against 1,985), and the new
`--reconcile` mode shows every row of the gap is a class the guard touches -- **63** renderer-withheld /
locator-block, **25** no-candidate / block, **52** withheld / no-block, **1** block / no-block
(`Engine.PSBumpBSP`) -- with the same start on both paths for **1,663** of the 2,319 classes and the locator's
start the later one on all **234** that disagree. **What is still open.** Whether the renderer should choose from
`guarded()`'s candidates as the locator does: that is the 87, and it changes which blocks render rather than what
one holds.

**Answered 2026-11-14 (thirty-eighth): the renderer should not take the guard, because the guard adds 88 one-tag
blocks.** `tools/defaults-emit.py --reconcile` now classifies the rows where the two locate paths disagree about
having a block: the **88** blocks `guarded()` would add are **all fully declared and all one property each** (88
properties in all), and the **1** it would remove is `Engine.PSBumpBSP`'s two-property block. Adopting the guard
would take the renderer from **1,664** fully-declared blocks to **1,751**, every addition being the single-tag
shape the locator's two-property floor refuses a verdict on (`single-declared`, 288 rows). The renderer carries no
such floor, so it would publish all 88 on one name each. **What is still open.** A tag walk past the group end for
those 88 starts -- what the single tag names and whether a second follows -- since that is the witness that would
make a one-tag start emittable; and `Engine.PSBumpBSP`'s two-property block, which the guard's trailer floor
withdraws while the renderer emits it.

**Answered 2026-11-14 (thirty-ninth): a one-tag start is witnessed on 88 of 88, so what withholds the 88 is the
verdict rule and not the bytes.** `tools/class-defaults-silent.py --gained` walks the tag grammar from the 88 starts
`guarded()` would add and from the 288 one-tag starts the locator already publishes, into
`inventory/class_defaults_single.csv`. On all 88 the block is one property tag naming a declared property whose type
the property record allows, with **0 bytes** after the terminating `None` (so no second tag can follow), one or two
committed groups ending exactly on the start and none covering it, and **no** later offset of the payload opening a
`>= 2`-tag parse of declared names that ends on the last byte. The control reads the same way (287 of 288; the odd
one, `Engine.PlaylistParserBase`, is a two-tag block with no group end, so it is not a one-tag start), and the two
paths choose the same start on **88 of 88** -- the locator has published these blocks all along. **What is still
open, and it is a decision rather than a measurement:** publish one-property blocks (1,664 -> 1,751 fully declared)
or keep `MIN_PROPERTIES = 2`; nothing in the bytes separates the 88 from the 288. `Engine.PSBumpBSP`'s two-property
block stays open as before: the guard's trailer floor withdraws it while the renderer emits it.

**Answered 2026-11-14 (fortieth): the cover rule was counting a group parse that begins inside another, and fixing
that closes the cross-tab.** `Engine.PSBumpBSP` was the last `block / no-block` row: the renderer's two-property
block at start 100 was refused because a committed group parse at 99..108 covered it, and that parse begins one byte
inside the record's own tail run `89..100` (`class_tail_run.csv`: names {1,2}, reference -3, config `System`, list
{7,66,66}, ending exactly on the start). `tools/class-defaults.py` now keeps only **outermost** parses
(`group_parses()` + `outermost()`) for the candidate filter and the group-end offers, while the trailer floor still
triggers on any parse. Measured: the cross-tab's `block / no-block` arm 1 -> **0**, the block gap 87 -> **85**, both
paths agreeing on 1,670 starts (1,663), blocks **1,983**, fully declared **1,664 (71.8%)**, and three one-tag starts
withdrawn at nested group ends (`ZatGun_Weak`, `ZatGun_JaffaCaptain`, `HighlightStaticMeshActor`) plus six starts
moved one property earlier that still tile their payloads. **What is still open:** whether the 86 remaining one-tag
starts should be published as blocks (1,664 -> 1,750 fully declared) -- the witness is measured (all 86 name a
declared property, agree on type, end on the payload and sit on a group boundary), and the only thing withholding
them is `MIN_PROPERTIES = 2`. The shipped text cannot settle it: **two** classes of 2,319 keep a `defaultproperties`
block in `ScriptText` (`Core.Locale`, `GUI.SGVertScrollBar`) and neither is in the population.


