# `.sgp` / `.PRO` player profiles (INI)

**Container:** plain INI text, CRLF, latin-1. Root has no magic — the first line is a `[Section]`.
The engine's config writer produced these (`Engine.PlayerProfile` is a `config()` class, PLAN 2.5),
so they are read with `configparser` and nothing else.

Four files ship, one per profile directory:

| file | sections | keys |
|---|---|---|
| `System/Profiles/DefaultProfile/DefaultProfile.sgp` | `Engine.PlayerProfile`, `Saved Games` | 11 + 142 |
| `System/Profiles/lbcddnyzq/lbcddnyzq.sgp` | `Engine.PlayerProfile` | 12 |
| `System/Profiles/sean/sean.sgp` | `Engine.PlayerProfile` | 12 |
| `System/Profiles/sean/SG1ALLIANCE_PROFILE.PRO` | `Engine.PlayerProfile` | 9 |

## `[Engine.PlayerProfile]` keys

`CharacterClassName`, `CharacterDisplayName`, `CharacterDisplayPic`, `MusicVolume`, `VoiceVolume`,
`SoundFXVolume`, `MouseSensitivity`, `MouseSmoothingMode`, `MouseAcceleration`, `bInvertY`,
`bAutoCenter` — the eleven that all four files share. The two per-user profiles add
`DifficultyLevel`; the `.PRO` file drops `CharacterDisplayName` and `CharacterDisplayPic`, i.e. it is
the older, smaller variant of the same schema (its extension is a leftover from the profile's
original name, not a different format).

The values are player data: settings and the chosen character. They are documented as keys only —
do not copy values into the repository, and do not treat one profile's contents as a default.

## `[Saved Games]` — the checkpoint index

`DefaultProfile.sgp` carries 142 keys in a `Saved Games` section, one per save slot, named
`C_<map>_<YYYYMMDD>_<HHMMSS>.sgs` (a few carry an `_OLD_` marker). This is the profile's index of
the checkpoints described in [sgs-checkpoint.md](sgs-checkpoint.md): the `.sgs` files themselves live
beside the profile in `System/Profiles/<profile>/`.

Two consequences for later phases:

- The menu's save list is this section, not a directory scan, so a rebuilt checkpoint must be
  registered here as well as written to disk (PLAN 6.4 / 7.2).
- The index is a record of what the studio was testing: it lists checkpoints for maps this build does
  not ship (the `C_M07b_*` series among them). That is evidence for the cut-content report (PLAN 5.6),
  to be counted properly rather than read off one profile.
