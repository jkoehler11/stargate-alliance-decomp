# `QUICK SELECT MAP` holds the disc's 73 maps, in sorted name order

`QUICK SELECT MAP` looks truncated and is not. The routine behind it, `UGUIController::execGetMapList`
(`0x002fe5f0`), asks the engine's file layer for every `*.unr` under the `[Core.System] Paths` globs, sorts what
it gets and appends each one -- with no comparison against any bound anywhere in its 1,256 bytes
(`docs/UNKNOWNS.md` U16). At the call's return (`0x2fe809`) the generic file manager's
`FindFirstFile`/`FindNextFile` loop hands over **73** names, and the controller's own array reads
`count=73 max=76` at `0x2fe8e0`. All 73 are in the list.

## The order, and the two anchors that pin it

The list is sorted by name, so the disk's own sort is the prediction and no boot is needed to write it down.
Two measured highlight positions are the check -- a `pad=down` count a frame in `docs/XEMU.md` shows:

| presses | highlight | what this table says |
|---|---|---|
| 10 | `M00` | index 10 is `M00` (the tutorial: the SGC lab) |
| 40 | `M15B` | index 40 is `M15B` |

Both agree, so the "first 41 names and nothing after them" reading was a scrolled page table, not a cap:
40 presses land on `M15B` because `M15B` *is* the 41st name, not because the list ends there. The 30 campaign
maps after it sit at indices **41-70**, and **every one of them loads**: the sweep ran to the end of the page on
2026-11-14 and `inventory/map_loads.csv` holds a row for all 61 campaign entries (`docs/MAP_LOADS.md`). The
last two entries are measured with the same instrument -- 72 `SGCTF-AbydosVsSGC` loads, and 71 `MainMenu`
returns to the main menu, because it is the shell's own menu package.

    python3 tools/map-list-order.py                    # the table below, from $SG1_DUMP/Maps
    python3 tools/map-list-order.py --check            # both anchors and the 73-name count, exit 1 on a mismatch

## The table

73 entries: **61 campaign** (`M*`), 7 deathmatch, 3 CTF (two `CTF-`, one `SGCTF-`) and 2 shell maps
(`Entry`, `MainMenu`). Index is the number of `pad=down` presses from the top of the list.

| index | map | kind |
|---|---|---|
| 0 | `CTF-Abydos` | ctf |
| 1 | `CTF-LostCity` | ctf |
| 2 | `DM-Ha'tak` | deathmatch |
| 3 | `DM-KelownanTrain` | deathmatch |
| 4 | `DM-Langara` | deathmatch |
| 5 | `DM-LostCity` | deathmatch |
| 6 | `DM-Subterranean` | deathmatch |
| 7 | `DM-Tomb` | deathmatch |
| 8 | `DM-WorstCaseScenario` | deathmatch |
| 9 | `Entry` | shell |
| 10 | `M00` | campaign |
| 11 | `M02A` | campaign |
| 12 | `M02B` | campaign |
| 13 | `M02C` | campaign |
| 14 | `M02D` | campaign |
| 15 | `M04B` | campaign |
| 16 | `M04a` | campaign |
| 17 | `M07A` | campaign |
| 18 | `M07B` | campaign |
| 19 | `M07C` | campaign |
| 20 | `M07D` | campaign |
| 21 | `M07E` | campaign |
| 22 | `M10A` | campaign |
| 23 | `M10B` | campaign |
| 24 | `M10C` | campaign |
| 25 | `M10D` | campaign |
| 26 | `M12A` | campaign |
| 27 | `M12B` | campaign |
| 28 | `M12C` | campaign |
| 29 | `M12D` | campaign |
| 30 | `M12E` | campaign |
| 31 | `M12F` | campaign |
| 32 | `M13A` | campaign |
| 33 | `M13B` | campaign |
| 34 | `M13C` | campaign |
| 35 | `M13D` | campaign |
| 36 | `M14A` | campaign |
| 37 | `M14B` | campaign |
| 38 | `M14C` | campaign |
| 39 | `M15A` | campaign |
| 40 | `M15B` | campaign |
| 41 | `M15C` | campaign |
| 42 | `M15D` | campaign |
| 43 | `M15E` | campaign |
| 44 | `M15F` | campaign |
| 45 | `M16A` | campaign |
| 46 | `M16B` | campaign |
| 47 | `M16C` | campaign |
| 48 | `M16D` | campaign |
| 49 | `M16E` | campaign |
| 50 | `M16F` | campaign |
| 51 | `M16G` | campaign |
| 52 | `M17A` | campaign |
| 53 | `M17B` | campaign |
| 54 | `M17D` | campaign |
| 55 | `M18A` | campaign |
| 56 | `M18B` | campaign |
| 57 | `M18D` | campaign |
| 58 | `M19A` | campaign |
| 59 | `M19B` | campaign |
| 60 | `M19C` | campaign |
| 61 | `M19D` | campaign |
| 62 | `M19E` | campaign |
| 63 | `M20A` | campaign |
| 64 | `M20B` | campaign |
| 65 | `M20C` | campaign |
| 66 | `M20D` | campaign |
| 67 | `M21A` | campaign |
| 68 | `M21B` | campaign |
| 69 | `M21C` | campaign |
| 70 | `M21D` | campaign |
| 71 | `MainMenu` | shell |
| 72 | `SGCTF-AbydosVsSGC` | ctf |

## What this does not say

- **The guest's sort is not read, it is inferred.** The prediction is a plain byte-order sort of the names,
  and the two anchors are the evidence for it; a map whose highlight sits elsewhere would be the counter-example.
  The sort routine is `0x000f8ab0`, called from `GetMapList`, and reading its comparison would settle the
  case-sensitivity question (`DM-Ha'tak` is the only name outside `[A-Za-z0-9]`).
- **Loading past index 40 is untested.** Every load measurement in `docs/XEMU.md` covers indices 10-40; this
  table says where the rest are, not that they boot.
- **The list is not the campaign order.** Sorted names run `M00`, `M02A`... `M21D`, which is `M`-number order and
  happens to be chapter order, but the list also carries the multiplayer maps the campaign never uses.
