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

* How many voices a pack holds, and the per-sound `VoiceDesc` the selector matches on: the `VoiceSets` arrays are not decoded (see *Which pack a pawn speaks with*).
* The `.lad` layout (UNKNOWNS U4).
* What each effect sounds like, and which weapon or surface uses it — that is a property value on the sound
  object, i.e. the property-stream reader, not the export table.
