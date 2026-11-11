# Audio: three separate systems behind one word

Speech, music and sound effects in this build are not one system. They have different class structures,
different storage, and different amounts of measured coverage — and only speech is fully indexed.

    python3 tools/class-graph.py --root StargateVoicePack --root IMusicActor -o inventory

## Voice and dialogue

The dialogue system is a pack-and-set hierarchy, all script (zero natives):

| class | package | functions | properties |
|---|---|---:|---:|
| `StargateVoicePack` | StargateGame | 34 | 24 |
| `TeamVoicePack` | StargateContent | 21 | 29 |
| `VoiceSet` | StargateGame | 14 | 7 |
| `SGVoicePackExtended` | StargateGame | 12 | 9 |
| `VoiceSelector` | StargateGame | 10 | 21 |
| `Speech` | Engine | 8 | 20 |

`VoiceSelector` holding 21 properties against 10 functions is the tell: voice choice is configuration —
which speaker, which set, which conditions — and the pack classes do the lookup.

Lip-sync is a third-party layer: `System/LIPSincData/` holds **18 files, 6.5 MiB** — 8 `.lad`, 6 `.dat`,
4 `.map`. The `.lad` payload layout is still **UNKNOWNS U4**; nothing here changes that, but this is where
the data lives.

## Which pack a pawn speaks with

`StargatePawn` declares `SGVoicePackClass` (`class<StargateVoicePack>`) and a spawned `SGVoicePack`, and spawns
that class on itself when it begins play; every bark afterwards is a call on the object —
`SGVoicePack.Say('MSGID_Dead',, TONE_Combat, MSGP_Critical)` among them — so **the pack is a per-pawn default
property**, inherited down the class tree, and no mission, roster or ini file chooses it.
`tools/voice-packs.py` reads the values out of the emitted defaults tree into `inventory/voice_packs.csv`, and
the table below is generated from that artifact (`--check-doc` in `make ci`).

<!-- generated:voice-packs -->
**61 classes under `StargatePawn` — the pack is `own` in 17, `inherited` in 5, `unread` in 1 and `none` in 38, over 13 distinct packs.**

| pack | pawns | how |
|---|---:|---|
| `JaffaMVoicePack` | 4 | `JaffaBase`, `JaffaHorusM` (inherited), `JaffaJackalM` (inherited), `JaffaSerpentM` (inherited) |
| `JaffaHVoicePack` | 3 | `JaffaHorusH`, `JaffaJackalH`, `JaffaSerpentH` |
| `JaffaLVoicePack` | 3 | `JaffaHorusL`, `JaffaJackalL`, `JaffaSerpentL` |
| `JaffaCaptainVoicePack` | 2 | `GoauldFirstPrime` (inherited), `JaffaCaptain` |
| `ReetouVoicePack` | 2 | `Reetou`, `ReetouProtector` (inherited) |
| `TokraVoicePack` | 2 | `Tokra`, `Tokra_Armed` (inherited) |
| `CarterVoicePack` | 1 | `Carter` |
| `HaakenLeaderVoicePack` | 1 | `HaakenLeader` |
| `HaakenVoicePack` | 1 | `HaakenGrunt` |
| `HaakenWarriorVoicePack` | 1 | `HaakenWarrior` |
| `JacksonVoicePack` | 1 | `Jackson` |
| `ONeillVoicePack` | 1 | `Oneill` |
| `TealcVoicePack` | 1 | `Tealc` |

The **38** classes that set no pack and inherit none are silent by construction — the base class sets no `SGVoicePackClass`, so nothing is spawned: `Anubis`, `BigReplicator`, `BloodyGrahamCoulder`, `Extrenite`, `ExtreniteChild`, `ExtreniteSG`, `FemaleDoctor`, `GeneralHammond`, `Goauld`, `GrahamCoulder`, `Jacob_Selmak`, `JaffaGrunt`, `Jonas`, `Langaran`, `Langaran_Minister`, `MediumGreenReplicator`, `MediumPurpleReplicator`, `NPC`, `ONeill_Lite`, `Oma`, `Ptero`, `PteroBoss`, `Replicator`, `ReplicatorPossess`, `ReplicatorQueen`, `SG1Pawn`, `SGCEngineer`, `SGCGrunt`, `SGCMedic`, `SGCOfficer`, `SGCWalter`, `Scarab`, `Shifu_Harsisis`, `StargatePawn`, `SuperSoldier`, `Thor`, `Tokra_Councillor`, `Tokra_CouncillorFemale`.

**Placement answers nothing.** 0 pawns are placed across the 73 maps in `inventory/map_actors.csv`; the 814 spawner placements (FishSpawner 2, IntelligentSpawner 808, ScriptedSpawner 4) are what fills a mission, and each spawner's squad is a per-instance property of the map, so a mission's pack set is not readable from its actor table.
<!-- /generated:voice-packs -->

The four `source` values are the point of the artifact. `own` is a class whose own block names its pack;
`inherited` is one that sets none and takes a super's — the Jaffa tiering is exactly this, `JaffaHorusM`,
`JaffaJackalM` and `JaffaSerpentM` each inheriting `JaffaMVoicePack` from `JaffaBase`; `none` is a class that
sets no pack and inherits none, so no pack is spawned and the pawn is silent; and `unread` is a class whose
`defaultproperties` block the emitter could not locate (`Tokra_Armed`), where the inherited value is a lower
bound rather than an answer.

Most characters in this build therefore do not speak: the silent set is the SGC rank and file (`SGCGrunt`,
`SGCMedic`, `SGCOfficer`, `SGCWalter`, `GeneralHammond`), the single-pool Goa'uld and Replicator families,
`JaffaGrunt`, `SuperSoldier`, `Ptero`, `Jonas` and `Jacob_Selmak`. The voiced population is the SG-1 four, the
Jaffa officer tier, the Haaken trio, the Tok'ra and the Reetou.

## How many voices a pack holds

`StargateVoicePack` declares one array, `VoiceSets`, and a pack subclass sets exactly that one property, so a
pack's class payload is its class record plus a single tagged array whose elements are `VoiceSet` exports of
`StargateContent.u`. `tools/voice-sets.py` reads them (`make voice-sets`) into `inventory/voice_sets.csv`, and
the block below is generated from that artifact (`--check-doc` in `make ci`).

<!-- generated:voice-sets -->
### How many voices a pack holds

Generated by `tools/voice-sets.py` (`make voice-sets`); `--check` in `make ci` keeps it honest.

**9 of the 25 pack classes carry a `VoiceSets` array**, 337 slots over 336 sets and **1227 `Sound` references**; 1 empty slot, and 16 of the 25 classes set no array at all (their payload is the class record alone).

| pack | sets | slots | empty | sounds | widest set |
|---|---:|---:|---:|---:|---:|
| `CarterVoicePack` | 80 | 80 | 0 | 352 | 24 |
| `ONeillVoicePack` | 76 | 76 | 0 | 279 | 14 |
| `TealcVoicePack` | 86 | 86 | 0 | 252 | 12 |
| `JacksonVoicePack` | 69 | 69 | 0 | 241 | 17 |
| `JaffaLVoicePack` | 7 | 7 | 0 | 30 | 8 |
| `JaffaMVoicePack` | 7 | 7 | 0 | 30 | 8 |
| `JaffaHVoicePack` | 6 | 6 | 0 | 28 | 8 |
| `HaakenVoicePack` | 3 | 4 | 1 | 9 | 3 |
| `ReetouVoicePack` | 2 | 2 | 0 | 6 | 3 |

The four SG-1 packs hold **1124 of the 1227 sounds** in 311 of the 336 sets; the enemy and NPC packs hold single-digit sets of 2-8 sounds each. `CarterVoicePack` is the widest single pack.

The sets themselves are `VoiceSet` exports of `StargateContent.u`, and their shape splits three ways: **274 carry the full `VDesc` + `VSounds` + `VSoundsDesc` block and hold one descriptor per sound**; 25 carry sounds but no descriptor array (load-priority flags instead); 37 are `VDesc`-only templates. Every sound reference is a *negative* compact index -- an import, not an export: the sounds live in the character's own package, which is why `Carter` and `Oneill` carry 260-410 effects of their own beside their voice sets.

<!-- /generated:voice-sets -->

Two things about the shape are worth stating plainly, because both were wrong in the first reader. A `VoiceSet`
block is `VDesc` (a **struct**: its name index precedes the size class, so the tag is 66 bytes, not the 19 the
first walk measured) followed by `VSounds` and `VSoundsDesc` — and `VDesc`'s own bytes can spell a tag-shaped
coincidence (`VSounds` plus an Array info byte), so the block has to be read tag by tag through the sizes,
never scanned for names. `tools/prop-stream.py` is that reader (it is the layout measured from the game's own
code), and this tool locates the block that ends at the payload's last byte rather than re-deriving it. A sound
reference is a **negative** compact index: the sets live in `StargateContent.u` but the sounds live in the
character's own package, so `VSounds` points into the import table, and counting only positive references is
what made a correctly parsed array read as zero sounds.

The 20 `*VoicePack` rows in `inventory/class_defaults_misses.csv` are this same one-property block seen from
the other side: the emitter's locator (`tools/class-defaults.py`) requires at least two properties in a
candidate block, so a pack's `defaultproperties` can never be a candidate and is reported `incomplete`. The
values are read here instead; the emitter's floor is its own fix.

<!-- generated:voice-descs -->
### What a per-sound `VoiceDesc` carries

Generated by `tools/voice-descs.py` (`make voice-descs`); `--check` in `make ci` keeps it honest.

**1125 descriptors decode over 273 sets in 4 packs, and every one of them is the same 11 fields.** The array exists only where the extended pack does: `CarterVoicePack`, `ONeillVoicePack`, `TealcVoicePack`, `JacksonVoicePack` carry one, the other packs' sets hold `VDesc` + `VSounds` and no per-sound descriptor at all.

| pack | sets | descs | multi-tone sets | recipient | subject | customize | forbid | text |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `CarterVoicePack` | 75 | 353 | 58 | 82 | 53 | 33 | 26 | 352 |
| `ONeillVoicePack` | 69 | 277 | 44 | 58 | 29 | 39 | 29 | 276 |
| `TealcVoicePack` | 69 | 253 | 44 | 69 | 34 | 34 | 19 | 252 |
| `JacksonVoicePack` | 60 | 242 | 43 | 27 | 37 | 34 | 27 | 241 |

**Tone is the field the per-sound array exists for.** 189 of the 273 sets name more than one tone among their descriptors; over all descriptors the histogram is `none` 4, `normal` 341, `combat` 553, `stealth` 227 -- `TONE_Critical` and `TONE_Other` are declared and never used. A set is one line of dialogue and its `VSounds` are the alternate takes, so `VSoundsDesc[i].Tone` is what puts take *i* in the selector's tone-match or tone-generic bucket; without it every take of a line would land in the same bucket and only `Rand` would choose between them.

**A descriptor names its type by string, and the int is written at load.** `Type` and `GenericType` are 0 in all 1125 descriptors and `SGenericType` is set in 195; `VoiceSet.Build` runs `GetMsgTypeFromString` over the strings when the pack builds, which is when the ints the selector's `IsSubTypeOf` compares appear. So the descriptor file does not hold the type the lottery matches on -- it holds the name the build resolves it from.

**What the descriptor does not hold is a probability.** It carries match keys -- `Id`, `Recipient` (236 descriptors name one), `Subject` (153), `Tone`, `bForbidGeneric` (101) and `eCustomize` (140, the recipient-name prefix/postfix modes) -- and every probability lives on `VoiceSelector`'s own defaults. 84 of the 273 sets have no `VDesc.Id` of their own (the descriptor Ids define the line) and 379 descriptors name an Id different from their set's, which is the fall-through `GetVoiceID`/`GetVoiceTone` chain the set's own `VDesc` covers.

`inventory/voice_descs.csv` holds one row per pack. Each set's own `VDesc` is a 66-byte `VoiceDesc` and carries the same fields; no descriptor *text* is published here, only whether one is set.

<!-- /generated:voice-descs -->

## How one line becomes a sound

Three layers, and only the last chooses:

1. **A caller names a message.** `StargatePawn` and `BotEnemy` say `MSGID_Dead`, `MSGID_Wounded`, `MSGID_Gasp`,
   `MSGID_Breath`, `MSGID_Alert`, `MSGID_Suspicious`, `MSGID_StandingDown`, `MSGID_SpotEnemy` and
   `MSGID_IncomingGrenade`; squad talk goes through `SayAIMsg` with the three `AIMSG_*` messages
   `docs/systems/AI.md` counts. Twelve call sites, nine distinct ids — the barks are a fixed vocabulary,
   not a script surface.
2. **The pack filters and queues.** `Speak` → `FilterSpeech` (mute, priority, and `MaxSpeechesPerSquad` /
   `bRandomMaxSpeechesPerSquad`, set to 2 on `SG1VoicePack`) → `BuildSpeech`, which owns a four-slot rotation
   (`Speeches[4]`: current, previous, next, buffer) and plays through `Speech`, the `Engine` class.
3. **The pack picks a voice, and which code runs depends on the pack's own ancestry.** `StargateVoicePack`'s
   `SelectVoice` is a fast search: the *first* `VoiceSet` whose own `VDesc.ID` matches the requested id, then
   `Rand(VSounds.Length)` inside it — no tone, recipient or subject matching. `SGVoicePackExtended` replaces it
   with `VoiceSelector`, which scores every voice under every set into six buckets and draws from a
   probability-weighted pool. **`SG1VoicePack` is the only subclass of the extended pack, and the SG-1 four are
   its only subclasses**, so Carter, Jackson, O'Neill and Teal'c get tone/recipient matching while the nine
   enemy and NPC packs take the id-only path — a real asymmetry between the player's team and everyone else.

`VoiceSelector`'s own defaults are the weights, and they are lopsided: `FullMatchProba=1.0`, `ToneMatchProba=0.5`,
`FullGenericProba=0.5`, `ToneGenericProba=0.5` and **`DefaultMatchProba` / `DefaultGenericProba` unset, i.e.
0.0**. The pool logic is "if the pool is empty, take one from this bucket; otherwise take one with probability
p", so the last two buckets can only be heard when no better bucket matched at all: a line whose recipient,
subject and tone all differ falls back to a default-parameter voice only when there is nothing else.
`MaxRecentlyUsedVoices=3` with `bDiscardRecentlyUsedVoices=True` drops the last three picks from the pool, but
only while more than one suitable voice remains, so a single-voice set repeats; the tone ladder is
one-directional per pair (`TONE_Critical` accepts `Combat`, `Combat` accepts `Critical` and `Normal`); and a
matching set that sets `bForbidGeneric` clears the generic buckets for the rest of that search.

Two things this page cannot yet say, both artifact gaps rather than open questions on the code side: **how many
voices a pack holds** — no emitted `defaultproperties` block carries a `VoiceSets` value, and
`inventory/class_defaults_misses.csv` lists 20 `*VoicePack` rows the locator cannot complete (19 `incomplete`,
1 `empty-block`; 2–3 properties found of 299–308 declared), so the per-sound `VoiceDesc` the selector matches
against stays unread — and **which pack a given mission uses**, because the placement route is empty (see the
generated block: the mission's pawns come from 814 spawner instances whose squad is per-instance map data).

## Music

`IMusicActor` (20 functions, 11 properties, **3 natives**) and `IMusicController` (11/6) are an interface pair
in `Engine`, and they are the only audio classes with natives — music is the one part of audio that touches
C++.

The content side, from `inventory/music_index.csv`: **1,923 WAVs, 348 MiB, 16 groups**, with per-group cue
vocabularies rather than one scheme:

| group | files | variants |
|---|---:|---|
| `M02A` | 154 | `amb1, amb2, c, cn, n, nc` |
| `M02B` | 136 | `c, cn, n, nc` |
| `M01` | 112 | `c, cn, n, nc` |
| `M04` | 109 | `amb1, c, cn, n, nc` |
| `M05` | 108 | `c, cn, n, nc` |
| `M07A` | 112 | `01`–`28`, `cn`, `nc` |

Two shapes of scoring show up: mission-long ambient/cue sets with the four `_c`/`_cn`/`_n`/`_nc` variants, and
`M07A`'s 28 numbered cues, which is sequence-driven music rather than a looping bed. The variant letters are
still unestablished (`docs/MUSIC.md` records the guess as a guess).

## Speech

From `inventory/speech_index.csv`: **1,364 lines, 150.8 MiB**, spread over **15 missions and 207 scenes**,
with **30 distinct speakers**. The distribution:

| speaker | lines |
|---|---:|
| (untagged) | 545 |
| `oneill` | 312 |
| `carter` | 170 |
| `daniel` | 125 |
| `tealc` | 69 |
| `grey` | 38 |

The SG-1 four are 676 of the 819 tagged lines. Two fifths of the corpus carries no speaker tag — barks,
mission control, crowd and radio chatter are not attributed in the file names, so any dialogue analysis has
to treat "untagged" as a category rather than an error.

Per mission (`inventory/dialogue_by_mission.csv`, 14 missions): `m07` 133 lines / 30 scenes / 7 speakers is
the densest, then `m15` 81, `m02` 79, `m14` 78.

## Sound effects are now indexed

The gap recorded in the first version of this file is closed: `.uax` packages parse, and each `Sound` export
is one effect.

| | |
|---|---:|
| `.uax` packages | **55 of 55 parsed** |
| sound effects | **3,047** |
| bytes | 92,222,592 (88 MiB) |

The five biggest are character and weapon sets, not ambient beds:

| package | sounds | bytes |
|---|---:|---:|
| `Carter` | 407 | 4,975,024 |
| `Oneill` | 297 | 3,480,809 |
| `Tealc` | 262 | 4,050,534 |
| `Jackson` | 261 | 3,492,544 |
| `jaffasounds` | 218 | 6,433,796 |

So the per-character arrangement is confirmed from the effect side: `Carter`, `Oneill`, `Tealc` and `Jackson`
each carry 260–410 effects of their own, next to their voice packs. `weaponsounds` (132) and `vehicles` (47)
are the shared sets; `surfaces` (156) is footsteps and impacts by material.

`inventory/sfx_index.csv` holds all 55 rows.

### What that means for the audio totals

| audio layer | count | source |
|---|---:|---|
| speech lines (loose WAVs) | 1,364 | `inventory/speech_index.csv` |
| music (loose WAVs, 16 groups) | 1,923 | `inventory/music_index.csv` |
| sound effects (inside `.uax`) | **3,047** | `inventory/sfx_index.csv` |

All three layers are now counted, which is what PLAN 5.3 asked for. Extraction is still separate from
indexing: the effects live inside packages and pulling them out as WAVs is the export pass (5.1), not this.

## What this means for the rebuild

1. **Speech and music are safe to rebuild** — loose WAVs plus script-only classes (except `IMusic*`'s 3
   natives).
2. **SFX are not**, for the same reason as everything else in content: they are inside packages.
3. **Lip-sync is a risk multiplier for dialogue**: 8 `.lad` files whose layout is unknown sit between the
   voice packs and the animations.

## Not measured

The per-sound `VoiceDesc` has left this list (above): it carries match keys and no probability. What stays
open is downstream of it -- which take the game actually picks at runtime, which needs a mission, not a reader.

* The `.lad` layout (UNKNOWNS U4).
* What each effect sounds like, and which weapon or surface uses it — that is a property value on the sound
  object, i.e. the property-stream reader, not the export table.
