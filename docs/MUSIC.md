# The music index

Speech is indexed in `docs/DIALOGUE.md`; this is the other and larger half of the audio. Both come
from the dump's file names and sizes only — counts, group names and byte totals, never audio:

```
python3 tools/music-index.py DUMP_ROOT -o inventory     # needs the dump; writes inventory/music_index.csv
```

## What is there

| measurement | value |
|---|---|
| music WAVs | **1,923** of the 3,287 WAVs in the dump |
| size | **348.4 MiB** (speech is 151 MiB; both together ≈ 499 MiB) |
| groups | **16** — one directory per mission group |
| cue variants | four per cue: `_c`, `_n`, `_cn`, `_nc` (476 / 475 / 413 / 413 files) → ≈480 cues |

The four variants per cue name are the only structure in the file names (`M01/1_01_c.wav`); what
`c`/`n`/`cn`/`nc` select is **not established** — a combat/ambient split is the obvious guess and is
recorded here as a guess, not a finding.

## Groups and the missions they cover

`maps` is answered from `inventory/maps.csv`: does a map for that mission id ship in this build?

| group | files | MiB | missions covered | map ships |
|---|---|---|---|---|
| `M01` | 112 | 17.5 | `M01` | **no** |
| `M02A` | 154 | 63.7 | `M02A` | yes |
| `M02B` | 136 | 17.1 | `M02B` | yes |
| `M04` | 109 | 25.0 | `M04` | yes |
| `M05` | 108 | 17.3 | `M05` | **no** |
| `M07A` | 112 | 17.8 | `M07A` | yes |
| `M07B` | 151 | 26.0 | `M07B` | yes |
| `M10_17` | 140 | 17.2 | `M10`–`M17` | yes except `M11` |
| `M12` | 124 | 22.7 | `M12` | yes |
| `M13` | 229 | 23.8 | `M13` | yes |
| `M14` | 95 | 23.7 | `M14` | yes |
| `M16_18` | 120 | 17.3 | `M16`–`M18` | yes |
| `M19` | 104 | 18.7 | `M19` | yes |
| `M20` | 112 | 18.0 | `M20` | yes |
| `M21` | 116 | 18.2 | `M21` | yes |
| `entry` | 1 | 4.3 | — (menu/entry cue) | n/a |

## What the index adds to the cut-content case

Two groups are scored for missions with **no map on disk at all**:

- **`M01`** — 112 cues, 17.5 MiB. This matches the campaign finding that chapter 1 points at
  `m01a.unr`, which is not in the dump (`docs/CUT_CONTENT.md`).
- **`M05`** — 108 cues, 17.3 MiB, and this one is *not* named by the chapter list either: the music
  exists for a mission that neither ships nor appears in the campaign definition.
- **`M11`** is inside the `M10_17` range group and has no map.

Conversely two shipped mission ids have **no dedicated music group**: `M15` (six maps, `M15A`–`M15F`)
and `M18` (`M18A`/`B`/`D`) are served by the range groups `M10_17` and `M16_18`, which is what a
"grouped" music label means rather than missing content.

The distribution also says the audio was produced for a campaign revision with a different mission
numbering: music exists for M01 and M05, no map exists for either, and no music exists for M03, M06,
M08 or M09 — the same fragmentation the chapter list shows in `docs/CONTENT_CENSUS.md`.

## Not claimed

- What the `c`/`n`/`cn`/`nc` variants control (guess above, unmeasured).
- Whether any cue is unused by the shipped maps: that needs the maps' level scripts (5.2's T3D dump).
- SFX: the 3,287 WAVs are speech and music only; sound effects live inside the `.uax` packages and are
  not extracted yet (PLAN 5.1).
