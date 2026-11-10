# `.lad` in the engine — how a controller becomes bone poses

The container is measured end to end in [lipsinc-lad.md](lipsinc-lad.md): header, channel table, twenty pose
frames, a clip section and a track table of `<Map>.<Animation>` records. What was missing is the *other* half —
what the engine does with a channel name and with a key block. This page is that half, read from the image.
Every address below is a function the LIPSinc module's own log strings already named
(`inventory/lipsinc_runtime.csv`), and the reader the format notes kept pointing at turned out to be the wrong
one: `LIPSincReadAnimFile` and `LIPSincReadControllerFile` are **text** parsers (LTF and LBP), and no `.lad`
byte goes through either of them.

## The path a `.lad` takes

`LIPSincInitLevel` (`0x000ba750`) is the entry point:

* it finds the files itself — `FUN_000192a0(L"*.lad")` — and logs `Searching for .lad files... %s` and
  `Found files...` around the result;
* each found name is compared with `_wcsstr` against the level's controller-prefix list (0xc-byte `FString`
  entries, count at `+0x5a4` of the level object) and the stems that match are copied into a list;
* that list goes to `LIPSincLoadDBFromDisk` (`0x000b9e90`), which walks it, strips the directory (`\\`) and the
  extension (`.`) off each name and hands the **stem** on.

So the file name is the controller name: `Controllers/sg1_crew_carter.lad` is the controller `sg1_crew_carter`,
and the directory is presentation only.

`LIPSincLoadDBFromDisk` parses nothing. For each stem it constructs an object of the class at
`PTR_005bf508` **by name** (a UE2-style factory call with the name as the object name), hands it the stem
through `FUN_000b9d50`, and releases it again. `FUN_000b9d50` in turn calls `FUN_000bce40(object, stem)`, which
is the parse: a chain of stages, each handed the object and writing at a fixed offset —

| stage | what it is | the object | the file |
|---|---|---|---|
| `FUN_00037110` ×2 | a 4-byte scalar | `+0x00`, `+0x14` | `0x00`/`0x04`: version `2`, `2` |
| `FUN_00035b60` | an array of strings, element `FUN_00058850` | `+0x18` | `0x08`: the channel table |
| `FUN_000bc470` | an array of `{u32, array of 32-byte records}`, record `FUN_000b1bf0` | — | the pose block: 20 frames × one record per channel |
| `FUN_000bcb60` | an array of clips `{u32, bone names, pose array, name}` | — | the clip section |
| `FUN_000bcd30` | an array of tracks `{u32, name, Map.Anim, u32, f32, f32, key block}` | — | the track table |
| `FUN_00037110`, `FUN_00058850` ×3 | a `u32` and three strings | `+0x60`…`+0x7c` | the tail: `2`, `BN-Head`, `undefined`, `undefined` |

which **is** the file, in order, so the stages are no longer five variables in one equation: `tools/lad-parse.py`
(`make lipsinc-parse`, `inventory/lipsinc_parse.csv`) replays exactly this sequence and all eight `.lad` files end on
their last byte (docs/formats/lipsinc-lad.md). The leaves it calls are as explicit as the chain: `FUN_000637e0` is
the **7-bit count reader** every array uses (bit `0x40` = a second byte, `<< 6`; bit `0x80` = a sign) and it re-encodes
what it read on the save path, logging `Mismatch: %08X %08X` when the two disagree; `FUN_00037040`, `FUN_00037080` and
`FUN_00037110` are one-, two- and four-byte raw reads through the archive's `Serialize(void*, length)`; and
`FUN_00058850` is one string, its count signed: a negative count means two-byte characters.

## The channel table is linked to bones by name

`LIPSincLinkBones` (`0x00225cd0`) is called once a level's skeletal meshes exist. It finds the controller for
the mesh's name (`FUN_000b5250`: `_wcscmp` over `count` names at `+0x14`, returning `index * 0x94 +
*(this + 0x10)` — the controller store is an array of **0x94-byte** records) and then calls `FUN_000b4930`,
which is the link:

* for each channel index `i` (count at controller `+0x08`) it reads the channel's name (`FUN_000b48e0`; the
  names are 0xc-byte `FString` entries at `+0x04`);
* it compares that name with `_wcsicmp` against every bone name of the mesh (0x40-byte bone entries at
  `mesh + 0x1dc`, count at `mesh + 0x1e0`) — case-insensitive, so the file's `BN-Head` is the mesh's `BN-Head`;
* on a match it writes the **mesh bone index** `j` into a dword array on the controller: `*(controller + 0x10)
  + i * 4 = j`. That is the only place a channel number and a bone meet, and the file never states it — the
  runtime derives it from the names;
* a channel that matches nothing is appended (name only) to a caller-supplied list, and the function logs
  `is valid controller` only when **every** channel matched, `NOT VALID CONTROLLER!` otherwise.

So the channel table is the file's bone list because the loader resolves it as one; a pack whose names do not
exist on the mesh is rejected rather than partially applied.

After linking, a bone is an **id**, not a name: `FUN_000b3880` (the pose builder, `ERROR -> bone %d doesn't
exist in base set!`) searches the mesh's id array (`+0x24`, count `+0x28`) for the record's id and indexes the
transforms at a 0x20 stride (`+0x3c`), reading a 32-byte record whose fields are the same shape the container
carries — three floats, three words that are not float data, and a float that is negated on the way out
(`+0x1c`).

## The channel names are bones of five skeletons

The name comparison above is only meaningful if the two sides exist in the data, so `tools/lad-bone-owners.py`
(`make lipsinc-bone-owners`, `inventory/lipsinc_bone_owners.csv`, `--check` in `make ci`) reads all eight
channel tables and scans the name table of every UE2 package in the dump (**482** of the corpus's packages
parse; the tool refuses a file whose name table it cannot tile rather than guessing):

**all 21 channel names are declared by exactly five packages, and by nothing else** —
`Animations/Grey_anim.ukx`, `Animations/SGC_soldiers_anim.ukx`, `Animations/jaffa_anim.ukx`,
`Animations/sg1_anim.ukx`, `Animations/tokra_anim.ukx`. One facial skeleton family, five characters' animation
sets, and a bone list identical in all five: `BN-Blink_L/R`, `BN-Cheek-Left/Right`, four brow bones, `BN-Jaw`,
`BN-Jaw-Pivot`, nine `BN-LIPS-*`, `BN-Head`, and the two tongues.

The two tongue bones are the exception in the *packs*, not in the skeletons: all five skeletons declare them,
but only the four 21-channel packs list them as channels (`file_count` 4 against 8 in the CSV), which is the same
two-bone difference the channel-table measurement found from the file side.

That is also the answer to "which weight slot is which bone": **none of them is a bone slot.** The runtime maps
one dword per *channel*, by name; a track's 26 key groups belong to an animation, and the 21- and 19-channel
packs carry the same 26 — so a group is not a channel and the slot table is a property of the track schema.

## Why the two named readers were the wrong half

Both functions the format notes pointed at read **text**, not the container:

* `LIPSincReadAnimFile` (`0x000b8340`) splits each line with `Core_UnTemplate(..., L" ")` and converts fields
  with `__wtof`/`__wtol`; its strings are `Read Track %s`, `ERROR: %s is a more recent LTF format`, `ERROR: %s
  is not a valid LTF file!` and `Parsed %s`. One shape is worth carrying forward: its track loop flushes the
  key array every **26** (time, value) pairs — `(i / 2 + 1) % 0x1a == 0` — the same number the binary key block
  uses for its group count. Recorded, not explained.
* `LIPSincReadControllerFile` (`0x000b7260`) does the same, with `Parsed %s`, `WARNING: LIPSinc bone linkup
  failed!` and `Attempt to import an obsolete .LBP file!`, and fills a pose array with a 32-byte stride.

So the `.lad` binary is read by the controller object above (LTF/LBP are the middleware's authoring formats), and
an earlier note's "the reader is the only witness left" was pointing at the wrong two functions.

## What is left

* **What a pose record's three "opaque" words and its `-1.0f` are -- a unit quaternion.** The last four floats
  are `x, y, z, w` with `|q| = 1` in all 3,200 records and `w <= 0` always, so the rest pose stores
  `(6.3e-30, -0, 3.6e-30, -1)` and a record carries a position plus a rotation (`docs/formats/lipsinc-lad.md`).
  `FUN_000b1bf0` is the confirmation and not the evidence: its eight reads go through `FUN_00037110` and
  `FUN_00037170`, which are the same 48 bytes, so the image cannot tell a `u32` from a `float` here.
* **A group has a name, and the corpus leaves it empty.** `FUN_000bc330` is the key block's reader, and it
  reads each group as a **28-byte record**: a `u32` tag, a 12-byte `FString` and a 12-byte `TArray` of keys
  (through `FUN_00058850` and `FUN_000bb650`). On disk the empty name is the single `0` byte the format notes
  called a flag, and all **58,006** groups carry it — so the one field that could name a curve is unused in this
  corpus, and "which weight slot is which bone" cannot be answered from the file.
* **What the clip record's `u32 2` and the track header's `160.0`/`220.0` mean.** `FUN_000bc620` reads the pair
  into two consecutive float fields with no test, and every record reader stores its leading `u32 2` into the
  record's first dword without comparing it -- so `2` is a tag the parser carries rather than a count or a
  marker, and neither constant is explained by its serialiser.
* **What the 7 signed slots mean.** Nothing in the name path touches them: the link is one dword per channel and
  the signed slots are not channels. `ValuesAtTime` samples them like any other slot (`0x000b24f0`, below) and the
  evaluator holds them like any other slot (`0x000b2230`, below), so the remaining route is the code that consumes
  the 26 doubles: `0x000b5f10` and `0x000b63f0`.

## The sampler reads one vector at one time, and no track shares a time axis (measured 2026-11-09)

`ValuesAtTime` (`0x000b24f0`, 235 bytes) is the middleware's own sampler, and its body settles what the runtime
asks for, if not what a slot means. It accepts its `double time` only when `time <= *(int *)(this + 0x1c)` and
`time >= DAT_004c8848` (a float64 global floor), logging `m_nEndTime = %i, time = %f` for the two values when it
refuses; then it fills `*(int *)(this + 0x34)` doubles in **one** loop, `out + 8 + i*8`, calling a single
evaluator per slot with no slot index and no branch. The count is the group count, and the object it samples is
the key-block array itself — `+0x30` base, `+0x34` count, `+0x38` capacity, stride `0x1c` — the same 28-byte
group record `FUN_000bc330` fills and `tools/lad-keys.py` reads. So slots 19–25 are **ordinary slots of one
26-double vector**: nothing in the sampler treats them as curves of another kind, and "what a signed slot means"
is a property of whatever consumes the vector.

The file then says the other half as a negative. If a track were one keyframe table, every populated slot would
live on the same time axis; `tools/lad-groups.py --vector` (dump-side; its `vector_shape()` is covered by the
`--check` that `make ci` runs) walks all **2,231** tracks and finds **none**: 0 share an extent, 0 share a start,
0 share an end, 0 share a grid, with **3–23 distinct key grids per track (median 10)**. The disagreement is not
rounding — inside one track the earliest and latest slot *starts* are a median of **696** ticks apart (max
15,133) and the *ends* a median of **1,187** (max 17,404), in a corpus whose key times span −32 to 8,688.

So the runtime asks a whole vector for a value at one time while the file gives every curve its own key list and
its own extent, and the single bound is per track. Every one of the 2,231 tracks therefore asks the evaluator
about times some of its curves do not cover; whether that is a hold, a clamp or an extrapolation is in the
evaluator's body, not in these bytes.
## The evaluator holds outside a curve's extent, and a segment is a smoothstep (measured 2026-11-09)

`ValuesAtTime` calls one evaluator per slot -- `call 0x000b2230` at `0x000b2560`, with the group record in `ecx`,
the one `double time` on the stack and the result returned in `st(0)` -- and that function is
`Engine_LIPSincSupport` (`0x000b2230`, 435 bytes, module prefix from its assert path
`\alienbrainWork\Stargate-sg1\Game\GameXBOX\Engine\Src\LIPSincSupport.cpp`). Its body answers the question the
sampler left open:

* **Before the first key and after the last it holds.** Both exits are a single `fldl` of the nearest key's
  `double` -- `[keys+8]` for the first, `[keys + count*16 - 8]` for the last -- with no arithmetic between the test
  and the return, so there is neither extrapolation nor a clamp to a range: the answer *is* the endpoint's value.
* **No keys returns 0.0** (the same `0x4c8848` zero the track gate uses) and **one key returns that value** at
  every time.
* **Between two keys it is a cubic smoothstep**, `v0 + (v1 - v0) * (3a^2 - 2a^3)` with `a = (t - t0)/(t1 - t0)`.
  The constants sit in `.rdata` beside the function -- `3.0` at `0x4ea138`, `6.0` at `0x4ea130` -- and the body's
  terms reduce to `v0 + 3(v1-v0)a^2 - 2(v1-v0)a^3`, which reproduces both endpoints (`a=0 -> v0`, `a=1 -> v1`; that
  check is what fixes the reading, because three of the intermediate `st` slots are duplicates of each other). A
  segment is therefore **not** a linear lerp: at `a = 1/4` the sample is `0.15625` of the span above `v0` where a
  lerp would be `0.25` -- 9.375% of the span inside it -- and 9.375% outside it at `a = 3/4`. Both numbers are
  properties of the curve shape, independent of the keys.
* **`|v| < 2^-51` becomes exactly 0.0** (`0x4ea0c8 = 4.44e-16`), and the snap is on the common exit, so it applies
  to a held endpoint and to an exact key hit as well as to an interpolated value.
* **Two asserts** guard the search: `time == (DOUBLE)(m_akKeys(( m_akKeys.Num() - 1 )).Time())` at line `0x161`,
  because the only legal way to find no segment is a time equal to the last key's, and `time2 > time1` at `0x16c`,
  which is what keeps a segment's divisor non-zero.

The search is the one a key list implies -- scan segments until `t0 <= time < t1` -- and the upper test is
**strict**, so two keys on the same tick are never a segment and the `0x16c` assert cannot fire from one.

## The hold is the common case, not the edge (measured 2026-11-09)

`tools/lad-sampler.py` (`make lipsinc-sampler`, `inventory/lipsinc_sampler.csv`, `--check` in `make ci`) implements
that sampler and counts what the shipped data asks of it, over all **2,231** tracks and **35,031** populated slots
-- 5,478,460 slot-ticks of domain, the sum of 26 x each track's own end:

* **57.4%** of that rectangle is answered by a hold, before a slot's first key or after its last. Per track the
  **median is 75.4%** (p10 48.7, p90 82.8, max 94.4): in a typical clip three quarters of the vector a slot
  contributes at any time is a frozen endpoint value.
* **0 of 2,231 tracks** have every populated slot covering the whole domain, and **2,111** have no slot that
  starts at tick 0, so every track in the corpus enters the hold path.
* Per column the least-held slot is **2** (10% of the domain) and the most-held is **7** (88%); the signed columns
  19-25 hold 21-69%, so they are ordinary slots in this respect too.
* **0** adjacent key pairs decrease -- the `0x16c` assert has no input in this corpus -- and **2** pairs share a
  tick, both slot 2 of a `carter` clip (`m07_17_h_carter` at 4086, `m21_09_b_carter` at 4022, values within 0.002
  of each other): duplicate keys from the writer, unreachable as a segment because the upper bound is strict.
* **0** key values are a non-zero magnitude below `2^-51`, so the snap changes nothing here; it exists for the
  runtime's own arithmetic, not for these bytes.

## A slot is read by name, and a zero means "no contribution" (measured 2026-11-09)

The four bodies the sampler section nominated are read, and the half that was left is not another format
field: it is a naming convention and a sentinel.

`0x000b4710` (215 bytes) is the single-slot accessor, and it answers one curve rather than the vector: it
copies its caller's `FString`, resolves it with `0x000b45c0` and hands the group it finds to
`Engine_LIPSincSupport`. `0x000b45c0` (329 bytes) is a linear scan of the key block -- count at `+0x40`,
base at `+0x3c`, stride `0x1c`, each group's name read through `0x000bb2f0` -- that returns
`index * 0x1c + base` for the first `__wcsicmp` match and 0 when nothing matches, and the accessor turns
that 0 into the `0x4c8848` zero. **A slot is addressed by the name in its own 28-byte record**, and that
field is empty in all 58,006 groups of the corpus (`inventory/lipsinc_keys.csv`): the one field that could
name a curve is the runtime's own lookup key, so a slot has no identity a file-side reader can read.

`0x000b5f10` (1,244 bytes) is the pose-pair blend, and its `alpha` argument splits it in two:

* with `alpha == 0` exactly -- the first frame of a fade, below -- it copies the destination's pose
  indices (0x24-byte entries whose `+0x20` is an index into a 12-byte vector table), calls `ValuesAtTime`
  once, resets the pose out of `pDefBonePose` (`0x000b2700`), then walks the controller's 0x48-byte
  records (base `+0x48`, count `+0x4c`): each record's own name goes through `0x000b4710` **at the global
  zero**, the answer is stored at record `+0x40`, and pose assembly `0x000b29a0` is called **only when
  that answer is not zero**;
* with `alpha != 0` it does no curve work at all: per pose entry it lerps the three position floats
  (`(dst - src) * alpha + src`) and writes four orientation dwords that the `0x000b19b0` call beside it
  fills. The first frame captures the start pose; every later frame interpolates from it.

`0x000b63f0` (1,411 bytes) is the fade state machine that drives it. The state is the int at `+0x4` of
the request:

| state | meaning | what it does |
| --- | --- | --- |
| 1 | fade in | duration is the float at `[request+0x28]`; while `elapsed < duration` it calls `0x000b5f10` with `alpha = elapsed/duration`, otherwise it sets state 2 |
| 2 | play | its own clock at `+0x20`; `ValuesAtTime` is a bounds check, and a refusal or a time below the `0x4c8848` floor goes to state 3 (or 0); otherwise every record through `0x000b4710` at that clock and `0x000b29a0` |
| 3 | fade out | duration is the float at `[request+0x2c]`; past it, state 0, a `*(*param_3 + 0xec)` vtable call and every record's `+0x40` zeroed |

Both paths agree on what the double is: **a blend weight whose zero means "no contribution"; a non-zero
value, negative or positive, is applied by the same call.** That is the answer the signed columns were
missing -- nothing in the consumer treats them as a different kind of number.

`tools/lad-weights.py` (`make lipsinc-weights`, `inventory/lipsinc_weights.csv`, `--check` in `make ci`)
counts the corpus against that gate at tick 0, the time the pose-pair path samples at, and at each
track's own end -- 2,231 tracks, 35,031 populated slots:

* at tick 0 **12,080 slots (34.5%) are non-zero and applied, 22,951 (65.5%) are exactly zero and
  skipped**; per track the median share is 36.4% (p10 9.1, p90 66.7), only 3 tracks contribute nothing,
  and the per-file range is 6.7% (`JAnderson.lad`, 3 tracks) to 36.2% (`carter`, 610 tracks);
* at each track's own end only **26 of 35,031** slots (0.07%) are non-zero, so the corpus is authored to
  end on a neutral pose and a clip can stop without leaving a weight behind;
* the signed columns are one number and its negation: slots 19, 24 and 25 are populated in 2,230 tracks,
  **24 == 25 at tick 0 in all 2,230**, **19 == -24 by value in 2,137**, and in the 1,890 tracks whose slots
  19 and 24 share a key grid the negation is **exact key for key**. Slot 19 never takes a negative value
  at tick 0 (0..8.378) and 24/25 never a positive one (-8.378..0). Slots 20-23 stay within +-2.87 and are
  zero at tick 0 in most tracks that carry them (580 of 598, 579 of 596, 866 of 883, 866 of 883).

## The record the runtime walks is a clip, and the loader names it (measured 2026-11-09)

The name writer is not a store, which is why an export of the support region alone found none: it is the
inline **string stage** of the clip reader, and the record it names is a clip and not a track.

`FUN_000bce40` hands each stage a destination that the *compiler pushed before the first call*:
`0x00035b60(Ar, +0x18)`, `0x000bc470(Ar, +0x30)`, `0x000bcb60(Ar, +0x48)`, `0x000bcd30(Ar, +0x54)`. Every
callee pops its own two arguments (`ADD ESP,0x8`), so each later stage consumes the argument still sitting
on the stack -- which is why the decompiler renders that first call with five arguments and the rest with
one. The decoding checks itself inside `0x000bcb60`, where the same shape appears with `+0x04`, `+0x1c`,
`+0x34` and each of the three leaves then identifies exactly one field: `0x00035b60` allocates `count * 0xc`
and reads one `FString` per element (a string array), `0x000bc470` allocates `count * 0x10` and reads a `u32`
plus a nested array per element (a pose array), and `0x00058850` reads a single `FString`.

So the controller carries four sections, and its **clip array is at `+0x48`; the track array is at `+0x54`**:

| controller | section | element |
| --- | --- | --- |
| `+0x18` | channel names | `FString`, `0xc` |
| `+0x30` | pose block | `{u32, array of 0x10-byte records}` |
| `+0x48` | clips | `0x48` bytes |
| `+0x54` | tracks | `0x48` bytes |

The **clip** record is `{u32 +0x00, bone names +0x04 (TArray<FString>), pose array +0x1c, name +0x34
(FString: data `+0x34`, count `+0x38`), weight `+0x40`}`. `FUN_000bb2a0(record, out)` is the name getter --
it copies the `FString` at `+0x34`/`+0x38` -- and both runtime consumers call it for every record of the
`+0x48` array, so **the pose-pair blend and the fade driver walk clips, not tracks**. The double they store
at `+0x40` fills the eight bytes a clip record leaves after its name.

The **track** record is read by `FUN_000bc620` with the same accounting: `{u32 +0x00, FString +0x04, FString
+0x10, u32 +0x24, f32 +0x28, f32 +0x2c, group array +0x30, group array +0x3c}`. A track's `+0x34` is
therefore the *second group array's base*, not a name -- the two families are both `0x48` bytes and that
field is what separates them. `0x000b45c0`'s scan (count `+0x40`, base `+0x3c`, stride `0x1c`) is over that
second block, and `0x000b4710` does not take the key block from the record at all: it comes in `ECX` from a
stack argument of the blend (`[ESP + 0x84]` at `0x000b5fe2`), so the *caller* picks the track a clip name is
looked up in.

The corpus splits the two families sharply: **17 clip records against 2,231 track records**, clips in four
of the eight files (carter 4, jackson 3, oneill 6, tealc 4) and none in the other four, which is why a
clip-only path leaves `grey.lad` and `JAnderson.lad` with nothing to evaluate.

What is left of U4 is the same question one level down: a slot's group name is empty in all 58,006 groups,
so a name-addressed lookup resolves to the empty name only. The `.lad` is not where names could come from --
but the binary's `.map` writer (`0x000b55f0`) formats a text mapping file, and the dump ships four of them
(`System/LIPSincData/Mappings/`), which is the next instrument.

