# What a loaded map's frame shows

`docs/MAP_LOADS.md` measures **reaches**: one row per entry of the disc's 73-entry map list, 91 boots, 71 `PASS`
and 2 `FAIL`. Its own "What is left" says what a load does not answer -- *whether a map that loads is playable* --
and names the evidence a separate measurement would read: a `PASS` row's frame, `index-NN/02-mission.png`. This is
that measurement, over the 72 frames the runs left on disk (`tools/xemu/map-frame.py`, `inventory/map_frames.csv`).

## The witness is one corner

`StargateHUD` draws the character portrait and the character's name in the frame's **top-left corner**, on every
gameplay frame. A cutscene letterboxes the picture and the corner is black; a menu draws its own art there. So the
question "did the load reach a frame the player was in control of" is asked of the box `(0, 0, 320, 96)` at
1280x960 -- the fraction of its pixels with any channel above 100 -- and of nothing else.

| population | frames | that corner |
|---|---:|---|
| gameplay | 65 | 0.140 - 0.986 |
| cutscenes | 5 | 0.000 |
| menu screens | 2 | 0.005 - 0.027 |

The populations do not touch: **0.027** (index 71, the main menu's own frame) and **0.140** (the dimmest gameplay
frame) leave an empty band between them, 5x wide, and the threshold is **0.06** -- 2.2x above the first and 2.3x
below the second.

The **other** corner was tried first and refuses the question. The bottom-right 240x100 box does draw the `Press A`
prompt on all five cutscenes (**0.0192**, the same value five times), but it also draws one on the menus -- **0.0668**
on index 71 and **0.1055** on the title screen's highlighted row -- and gameplay frames run from 0.0000 to 0.9601
there, because index 37's weapon fills that corner. A cutscene and a menu are both a dark HUD corner plus a bright
box in the bottom right, so the tool reports that number and does not decide with it.

## The reading

<!-- generated:map-frames -->
| index | map | verdict | top-left corner | bottom-right | run |
|---:|---|---|---:|---:|---|
| 0 | `CTF-Abydos` | hud | 0.1772 | 0.0000 | `xemu/missions-head` |
| 1 | `CTF-LostCity` | hud | 0.2108 | 0.0012 | `xemu/missions-head` |
| 2 | `DM-Ha'tak` | hud | 0.1911 | 0.0000 | `xemu/missions-head` |
| 3 | `DM-KelownanTrain` | hud | 0.9385 | 0.0010 | `xemu/missions-head` |
| 4 | `DM-Langara` | hud | 0.3309 | 0.0336 | `xemu/missions-head2` |
| 5 | `DM-LostCity` | hud | 0.1817 | 0.0080 | `xemu/missions-head3` |
| 6 | `DM-Subterranean` | hud | 0.1774 | 0.1118 | `xemu/missions-head2` |
| 7 | `DM-Tomb` | hud | 0.1735 | 0.0000 | `xemu/missions-head4` |
| 8 | `DM-WorstCaseScenario` | hud | 0.1553 | 0.0000 | `xemu/missions-head4` |
| 10 | `M00` | hud | 0.1721 | 0.0000 | `xemu/missions` |
| 11 | `M02A` | no-hud | 0.0000 | 0.0192 | `xemu/missions` |
| 12 | `M02B` | hud | 0.2397 | 0.0000 | `xemu/missions-retry2` |
| 13 | `M02C` | hud | 0.1704 | 0.0000 | `xemu/missions-run` |
| 14 | `M02D` | hud | 0.2179 | 0.0000 | `xemu/missions-retry` |
| 15 | `M04B` | hud | 0.2735 | 0.0005 | `xemu/missions-run` |
| 16 | `M04a` | hud | 0.1635 | 0.0344 | `xemu/missions-sweep` |
| 17 | `M07A` | no-hud | 0.0000 | 0.0192 | `xemu/missions-sweep` |
| 18 | `M07B` | hud | 0.5895 | 0.0000 | `xemu/missions-sweep` |
| 19 | `M07C` | no-hud | 0.0000 | 0.0192 | `xemu/missions-sweep` |
| 20 | `M07D` | hud | 0.1633 | 0.0000 | `xemu/missions-sweep` |
| 21 | `M07E` | no-hud | 0.0000 | 0.0192 | `xemu/missions-tail` |
| 22 | `M10A` | no-hud | 0.0000 | 0.0192 | `xemu/missions-sweep` |
| 23 | `M10B` | no-hud | 0.0049 | 0.0005 | `xemu/missions-sweep` |
| 24 | `M10C` | hud | 0.9392 | 0.0070 | `xemu/missions-sweep` |
| 25 | `M10D` | hud | 0.3476 | 0.1273 | `xemu/missions-sweep` |
| 26 | `M12A` | hud | 0.8629 | 0.0866 | `xemu/missions-sweep` |
| 27 | `M12B` | hud | 0.2064 | 0.0000 | `xemu/missions-sweep` |
| 28 | `M12C` | hud | 0.1790 | 0.0000 | `xemu/missions-sweep` |
| 29 | `M12D` | hud | 0.2036 | 0.0239 | `xemu/missions-sweep` |
| 30 | `M12E` | hud | 0.1745 | 0.0000 | `xemu/missions-sweep` |
| 31 | `M12F` | hud | 0.3530 | 0.0026 | `xemu/missions-sweep` |
| 32 | `M13A` | hud | 0.2116 | 0.0008 | `xemu/missions-sweep` |
| 33 | `M13B` | hud | 0.1733 | 0.0000 | `xemu/missions-sweep` |
| 34 | `M13C` | hud | 0.2143 | 0.0001 | `xemu/missions-tail` |
| 35 | `M13D` | hud | 0.5660 | 0.0964 | `xemu/missions-tail` |
| 36 | `M14A` | hud | 0.2287 | 0.0012 | `xemu/missions-tail` |
| 37 | `M14B` | hud | 0.5327 | 0.9601 | `xemu/missions-tail` |
| 38 | `M14C` | hud | 0.2103 | 0.0067 | `xemu/missions-38` |
| 39 | `M15A` | hud | 0.1809 | 0.2241 | `xemu/missions-tail` |
| 40 | `M15B` | hud | 0.1672 | 0.0001 | `xemu/missions-tail` |
| 41 | `M15C` | hud | 0.1855 | 0.0776 | `mapback` |
| 42 | `M15D` | hud | 0.1964 | 0.0000 | `mapback` |
| 43 | `M15E` | hud | 0.1871 | 0.1667 | `mapback` |
| 44 | `M15F` | hud | 0.1746 | 0.1159 | `mapback2` |
| 45 | `M16A` | hud | 0.7500 | 0.0002 | `mapback` |
| 46 | `M16B` | hud | 0.2601 | 0.0466 | `mapback2` |
| 47 | `M16C` | hud | 0.9811 | 0.0283 | `mapback2` |
| 48 | `M16D` | hud | 0.4252 | 0.0320 | `mapback2` |
| 49 | `M16E` | hud | 0.2866 | 0.0502 | `mapback2` |
| 50 | `M16F` | hud | 0.6913 | 0.0231 | `mapback3` |
| 51 | `M16G` | hud | 0.9658 | 0.0456 | `mapback3` |
| 52 | `M17A` | hud | 0.2796 | 0.0001 | `mapback3` |
| 53 | `M17B` | hud | 0.1921 | 0.0000 | `mapback3` |
| 54 | `M17D` | hud | 0.1820 | 0.0000 | `mapback3` |
| 55 | `M18A` | hud | 0.1644 | 0.0000 | `mapback3` |
| 56 | `M18B` | hud | 0.1622 | 0.0000 | `mapback3` |
| 57 | `M18D` | hud | 0.2182 | 0.1165 | `mapback3` |
| 58 | `M19A` | hud | 0.2845 | 0.0680 | `mapback3` |
| 59 | `M19B` | hud | 0.2142 | 0.2524 | `mapback3` |
| 60 | `M19C` | hud | 0.9864 | 0.0000 | `mapback3` |
| 61 | `M19D` | hud | 0.2149 | 0.2959 | `mapback3` |
| 62 | `M19E` | hud | 0.2125 | 0.0000 | `mapback3` |
| 63 | `M20A` | hud | 0.1719 | 0.1661 | `mapback3` |
| 64 | `M20B` | hud | 0.2596 | 0.0000 | `mapback3` |
| 65 | `M20C` | hud | 0.1596 | 0.1224 | `mapback3` |
| 66 | `M20D` | hud | 0.1785 | 0.0005 | `mapback3` |
| 67 | `M21A` | hud | 0.1679 | 0.0000 | `mapback3` |
| 68 | `M21B` | hud | 0.1800 | 0.0000 | `mapback3` |
| 69 | `M21C` | hud | 0.1731 | 0.0000 | `mapback3` |
| 70 | `M21D` | hud | 0.1941 | 0.0000 | `mapback3` |
| 71 | `MainMenu` | no-hud | 0.0272 | 0.0668 | `missions-tail` |
| 72 | `SGCTF-AbydosVsSGC` | hud | 0.1404 | 0.0000 | `missions-tail` |
<!-- /generated:map-frames -->

## Status

<!-- generated:map-frames-status -->
**72 frames read: 65 `hud`, 7 `no-hud`.**

90% of the frames whose verdict was taken with a picture still on disk show the HUD corner, so the load reached a frame the player was in control of.

The corner reads **0.140** on the dimmest `hud` frame and **0.027** on the brightest frame that is not one, so the 0.06 threshold sits 2.3x below the first and 2.2x above the second.

1 row(s) here are not `PASS` in `inventory/map_loads.csv` -- index 71 (`MainMenu`, the load table says FAIL). A `PASS` row for them is on disk, so a run that table was not given passed them; the corner reading is what decides whether that pass was real.

The `no-hud` rows are `M02A` (index 11, corner 0.0000, bottom-right 0.0192); `M07A` (index 17, corner 0.0000, bottom-right 0.0192); `M07C` (index 19, corner 0.0000, bottom-right 0.0192); `M07E` (index 21, corner 0.0000, bottom-right 0.0192); `M10A` (index 22, corner 0.0000, bottom-right 0.0192); `M10B` (index 23, corner 0.0049, bottom-right 0.0005); `MainMenu` (index 71, corner 0.0272, bottom-right 0.0668).
<!-- /generated:map-frames-status -->

## The seven frames with no HUD, read

Five are the disc's cutscenes, and their frames agree with that on every other count: 11 (`M02A`), 17 (`M07A`),
19 (`M07C`), 21 (`M07E`) and 22 (`M10A`) are letterboxed pictures with a `Press A` prompt in the bottom right and
the same 0.0192 reading. The load reached a mission; the mission opened on a film.

23 (`M10B`) is the interesting one: its frame is the **`MISSION FAILED`** screen -- `Retry`, `Restart Chapter`,
`Load Game`, `Return to Main Menu` -- which is a live mission that ran, spawned the player and lost him while
nobody was playing. It is the strongest evidence in the set that the load reached play, and it is filed as
`no-hud` because that is what the pixels say.

71 (`MainMenu`) is the shell's own menu package. This is the one row where the two tables disagree: a run recorded
`PASS` for it, and `inventory/map_loads.csv` records `FAIL` with the detail `main menu`. The frame read is the
tie-breaker and it sides with the load table -- the frame is the main menu, corner 0.027, bottom right 0.067 -- so
that stray `PASS` is a menu that repainted past the frame gate, not a load.

## What this still cannot say

* A `hud` verdict is **one moment**, the frame the load gate shot 60 s after the map came up. It proves the player
  was in control then; it says nothing about a mission's objective state, its completion, or whether the map is
  still playable a minute later.
* The cutscene-versus-menu split is measured to be out of reach of these two crops (above), so the page reports
  `hud` / `no-hud` and names the rows rather than guessing a kind.
* Index 9 (`Entry`) has no frame at all, because no run ever passed it and `missions.sh` keeps no picture for a
  `FAIL`. The load table's other `FAIL` row, 71, does have one, from the stray pass above.

## Reproducing

    export WORK=$HOME/sg1/work
    python3 tools/xemu/map-frame.py --work $WORK -o inventory/map_frames.csv
    python3 tools/xemu/map-frame.py --work $WORK --missing
    python3 tools/xemu/map-frame.py --frame path/to/02-mission.png
    python3 tools/xemu/map-frame.py --write-doc docs/MAP_FRAMES.md
    python3 tools/xemu/map-frame.py --check-doc docs/MAP_FRAMES.md

`make map-frames` regenerates the table from the run directories under `$WORK` (two levels deep, so
`xemu/missions-*` and the back half's `mapback*` are both found) and skipping the directories that exist to prove a
failure path. `make ci` runs `--check-doc`, which re-renders both blocks from the committed CSV and
`inventory/map_loads.csv` -- no disc and no frames -- and the tool's own `--check` classifies six synthetic frames
at two resolutions and re-decodes a fixture through each of the five PNG row filters, so the number on this page
cannot drift from the table or from the instrument.
