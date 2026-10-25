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

* Which voice pack a given mission uses, and how `VoiceSelector` picks between them (script bodies, `$WORK`).
* The `.lad` layout (UNKNOWNS U4).
* What each effect sounds like, and which weapon or surface uses it — that is a property value on the sound
  object, i.e. the property-stream reader, not the export table.
