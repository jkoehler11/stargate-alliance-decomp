# The campaign map loads (PLAN 7.2)

`inventory/map_loads.csv` is one committed row per map-list index: `index,name,result,attempts,runs,detail`.
It is generated from `tools/xemu/missions.sh` run directories by `tools/xemu/map-loads.py`
(`make map-loads ISO=$WORK/sg1.iso RUNS='<outdir> ...'`), and every number in the table below is that
file's.

## Why this is an artifact and not a paragraph

`missions.sh` writes one row per index into its OUTDIR's `missions.tsv` and **truncates that file on every
invocation**, so a sweep's result exists only inside the directory that made it. Until now the campaign-wide
picture was a prose table in `docs/XEMU.md` assembled by hand from several runs, and the per-index evidence
(`index-41/02-mission.png`) was the only record of which boot produced which verdict. Three consequences,
all of them paid for:

* a sweep over part of the range could not be extended -- the next invocation started a new TSV;
* two sessions re-derived the same index -> name table off frames, and one of them recorded the page as 41
  entries when it holds 73 (`docs/XEMU.md`, U16);
* a row's `attempts` (how many boots it took) was lost whenever it mattered most, on the flaky indices.

`map-loads.py` merges any number of run directories, so a sweep is cumulative: this run's directory is
added to the list and the old rows stay. It also refuses two things rather than recording them -- an index
outside the disc's table, and a status that is neither `PASS` nor `FAIL` -- because a merged table that
carries a page that does not exist is worse than a short one.

## What a PASS means, and what it does not

A row is `PASS` when `missions.sh`'s three assertions hold on that boot: the frame after the select
repainted, `guest-halt.py` says it is not the BIOS screen, and `frame-diff.py` says it differs from the
boot frame by at least 2.5%. That is a **load**, not a mission: a cutscene counts (index 70 is one) and so
does a first-person scene with the HUD up (index 41). It is deliberately not a "did a mission start"
criterion -- at least one campaign entry is a cutscene on the shipped disc (index 70, measured), so
requiring a HUD would fail on the disc's own content.

A `FAIL` is almost always **input**, not a map. The measured signature is the opening `A` doing nothing,
which leaves the highlight frame on `MAIN MENU`, so every d-pad press after it goes nowhere; the second is
the `A` **on** the list doing nothing. Both are non-deterministic: four of the 31 indices in the
2026-11-04 sweep hit one (27 loaded first time, 3 on a second attempt, 1 on a third), and two of those --
21 and 38 -- failed both attempts in that run and then passed first time when re-run alone. That is why
`PASS` wins over `FAIL` when a later run contradicts an earlier one, and why `detail` keeps the failure's
own reason (`BIOS screen`, `main menu`, `frame gate`) for the rows that never passed.

Both rows whose `FAIL` is the **map** are the page's two non-game packages: 71 `MainMenu`, the shell's own menu
package, and 9 `Entry`, which behaves the same way. Selecting either puts the guest back on `MAIN MENU` -- read off
the frame, the menu with `Continue` highlighted -- which is exactly what the frame gate reports (`main menu`: 1.58%
of the frame differing from the boot frame, where a load needs 2.5%). The gate ends the index on that verdict rather
than retrying it, and every neighbour loaded in the same sweep: indices 70 and 72 either side of `MainMenu`, 7 and 8
next to `Entry`.

## The runs merged here, and the ones deliberately left out

| run directory | range | note |
|---|---|---|
| `$WORK/xemu/missions-run` | 10-15 | first six, before the boot window was 135 s |
| `$WORK/xemu/missions-retry` | 12, 14 | the two boots `missions-run` lost |
| `$WORK/xemu/missions-retry2` | 12 | the retry that answered the index `missions-run` lost |
| `$WORK/xemu/missions-sweep` | 16-33 | the 18-index sweep |
| `$WORK/xemu/missions-tail` | 21, 34-40 | the tail of the first half plus the two failures re-run |
| `$WORK/xemu/missions-38` | 38 | the second failure re-run alone |
| `$WORK/mapback` | 41-45 | the back-half sweep, stopped at a run's budget |
| `$WORK/mapback2` | 44, 46-49 | the resume at 44 |
| `$WORK/mapback3` | 50-70 | the last 21, one boot each |
| `$WORK/xemu/missions-mapall` | 71, 72 | the page's last two entries: the shell map and a CTF map |
| `$WORK/xemu/missions-head` | 0-3 | the page's first four: two CTF and two DM maps (`DM-KelownanTrain` took two attempts) |
| `$WORK/xemu/missions-head2` | 4-6 | the next three: `DM-Langara` on its second attempt, `DM-Subterranean` on its first, `DM-LostCity` failed twice with the opening `A` swallowed |
| `$WORK/xemu/missions-head3` | 5 | `DM-LostCity` alone, re-booted: one attempt, a loaded map |
| `$WORK/xemu/missions-head4` | 7-9 | the three entries up to `M00`: `DM-Tomb`, `DM-WorstCaseScenario`, `Entry` |

Fourteen directories, and the set is its own evidence: the ten that made the committed 63 rows regenerate them
**byte for byte** (measured 2026-11-14), so the four `missions-head*` directories are the only
diff, and their ten rows are it. A directory listing cannot confirm that -- `$WORK` also holds `missions/`, `gtime`-era retries and
the manufactured-failure directories below.

Three other directories in `$WORK/xemu` carry a `missions.tsv` and are **not** merged, on purpose:
`missions-neg` (index 10 with `--boot=25`, a manufactured failure that proves the loop retries),
`halt-test` and `halt-test2` (index 10 booted from an ISO whose `M00.unr` was truncated to 1,000 bytes, the
manufactured crash that proves `guest-halt.py` catches a BIOS screen). They are failures by construction, and
merging them would put `BIOS screen` on a row whose map loads.

## The table

<!-- generated:map-loads -->
**73 rows, 71 PASS / 2 FAIL, 91 boots:** all 61 campaign entries (10-70) have been loaded.

Outside the campaign range: 0 `CTF-Abydos` (PASS), 1 `CTF-LostCity` (PASS), 2 `DM-Ha'tak` (PASS), 3 `DM-KelownanTrain` (PASS), 4 `DM-Langara` (PASS), 5 `DM-LostCity` (PASS), 6 `DM-Subterranean` (PASS), 7 `DM-Tomb` (PASS), 8 `DM-WorstCaseScenario` (PASS), 9 `Entry` (FAIL), 71 `MainMenu` (FAIL), 72 `SGCTF-AbydosVsSGC` (PASS).

| index | map | result | attempts | runs | detail |
|---:|---|---|---:|---:|---|
| 0 | `CTF-Abydos` | PASS | 1 | 1 |  |
| 1 | `CTF-LostCity` | PASS | 1 | 1 |  |
| 2 | `DM-Ha'tak` | PASS | 1 | 1 |  |
| 3 | `DM-KelownanTrain` | PASS | 2 | 1 |  |
| 4 | `DM-Langara` | PASS | 2 | 1 |  |
| 5 | `DM-LostCity` | PASS | 3 | 2 |  |
| 6 | `DM-Subterranean` | PASS | 1 | 1 |  |
| 7 | `DM-Tomb` | PASS | 1 | 1 |  |
| 8 | `DM-WorstCaseScenario` | PASS | 1 | 1 |  |
| 9 | `Entry` | FAIL | 1 | 1 | main menu |
| 10 | `M00` | PASS | 1 | 1 |  |
| 11 | `M02A` | PASS | 1 | 1 |  |
| 12 | `M02B` | PASS | 3 | 3 |  |
| 13 | `M02C` | PASS | 1 | 1 |  |
| 14 | `M02D` | PASS | 2 | 2 |  |
| 15 | `M04B` | PASS | 1 | 1 |  |
| 16 | `M04a` | PASS | 1 | 1 |  |
| 17 | `M07A` | PASS | 1 | 1 |  |
| 18 | `M07B` | PASS | 1 | 1 |  |
| 19 | `M07C` | PASS | 1 | 1 |  |
| 20 | `M07D` | PASS | 2 | 1 |  |
| 21 | `M07E` | PASS | 3 | 2 |  |
| 22 | `M10A` | PASS | 1 | 1 |  |
| 23 | `M10B` | PASS | 2 | 1 |  |
| 24 | `M10C` | PASS | 1 | 1 |  |
| 25 | `M10D` | PASS | 1 | 1 |  |
| 26 | `M12A` | PASS | 1 | 1 |  |
| 27 | `M12B` | PASS | 1 | 1 |  |
| 28 | `M12C` | PASS | 2 | 1 |  |
| 29 | `M12D` | PASS | 1 | 1 |  |
| 30 | `M12E` | PASS | 1 | 1 |  |
| 31 | `M12F` | PASS | 1 | 1 |  |
| 32 | `M13A` | PASS | 2 | 1 |  |
| 33 | `M13B` | PASS | 1 | 1 |  |
| 34 | `M13C` | PASS | 3 | 2 |  |
| 35 | `M13D` | PASS | 1 | 1 |  |
| 36 | `M14A` | PASS | 1 | 1 |  |
| 37 | `M14B` | PASS | 1 | 1 |  |
| 38 | `M14C` | PASS | 3 | 2 |  |
| 39 | `M15A` | PASS | 1 | 1 |  |
| 40 | `M15B` | PASS | 1 | 1 |  |
| 41 | `M15C` | PASS | 1 | 1 |  |
| 42 | `M15D` | PASS | 1 | 1 |  |
| 43 | `M15E` | PASS | 1 | 1 |  |
| 44 | `M15F` | PASS | 1 | 1 |  |
| 45 | `M16A` | PASS | 1 | 1 |  |
| 46 | `M16B` | PASS | 1 | 1 |  |
| 47 | `M16C` | PASS | 1 | 1 |  |
| 48 | `M16D` | PASS | 1 | 1 |  |
| 49 | `M16E` | PASS | 1 | 1 |  |
| 50 | `M16F` | PASS | 1 | 1 |  |
| 51 | `M16G` | PASS | 1 | 1 |  |
| 52 | `M17A` | PASS | 1 | 1 |  |
| 53 | `M17B` | PASS | 1 | 1 |  |
| 54 | `M17D` | PASS | 1 | 1 |  |
| 55 | `M18A` | PASS | 1 | 1 |  |
| 56 | `M18B` | PASS | 1 | 1 |  |
| 57 | `M18D` | PASS | 1 | 1 |  |
| 58 | `M19A` | PASS | 1 | 1 |  |
| 59 | `M19B` | PASS | 1 | 1 |  |
| 60 | `M19C` | PASS | 1 | 1 |  |
| 61 | `M19D` | PASS | 1 | 1 |  |
| 62 | `M19E` | PASS | 1 | 1 |  |
| 63 | `M20A` | PASS | 1 | 1 |  |
| 64 | `M20B` | PASS | 1 | 1 |  |
| 65 | `M20C` | PASS | 1 | 1 |  |
| 66 | `M20D` | PASS | 1 | 1 |  |
| 67 | `M21A` | PASS | 1 | 1 |  |
| 68 | `M21B` | PASS | 1 | 1 |  |
| 69 | `M21C` | PASS | 1 | 1 |  |
| 70 | `M21D` | PASS | 1 | 1 |  |
| 71 | `MainMenu` | FAIL | 1 | 1 | main menu |
| 72 | `SGCTF-AbydosVsSGC` | PASS | 2 | 1 |  |
<!-- /generated:map-loads -->

## What is left

The campaign range is 10-70 (61 entries; 0-9 are the multiplayer maps and `Entry`, 71-72 are `MainMenu` and
`SGCTF-AbydosVsSGC`), and **every one of the 61 has a row** (2026-11-11). The twelve entries outside that range -- 0-9, 71 and 72 -- have rows too
(2026-11-14), so the table covers all 73 of the page's entries rather than its campaign prefix. The tool's summary line no longer
names an unmeasured index, so the sweep is finished rather than paused, and `map-index.py --rotate N` -- which
printed N indices a night for a nightly that walks the range without repeating (2 a night covers 61 entries in
31 nights, and because 2 is coprime with 61 the wrap skips nothing) -- has no work left to hand out.

What that does **not** mean is that the list is readable from the names: the indices are dense (10-70) and the
names are not. 15 is `M04B` and 16 is lowercase `M04a`; 52-54 are `M17A`, `M17B`, `M17D` and 55-57 `M18A`,
`M18B`, `M18D`, so `M17C` and `M18C` are on no entry of the list and a name does not predict an index. This
table, generated from `map-index.py`'s own ASCII order, is the only index-to-name record there is.

**Measured pace:** five indices -- 44, 46, 47, 48, 49 -- took 23 minutes of wall clock, 4.6 minutes per boot
with no retry (135 s to the title, one `A`, N d-pad presses, a 60 s settle), and the remaining 21 took 97
minutes, 4.6 apiece: the prediction was the measurement's own rate, and a sweep of this range is an afternoon
of boots rather than a risk. The pause at 50 was where a run's budget ended, not a place the harness failed.

Two things this table still cannot say, and what would settle each:

* **Whether a map that loads is playable.** A `PASS` row's frame is the evidence
  (`index-NN/02-mission.png`); reading the HUD, a checkpoint load or a mission's own objective state from
  it is a separate measurement, and the checkout (`StargateContent`, `docs/systems/CHECKPOINTS.md`) is where
  the save half of it lives. **The frame half is measured for 72 of the 73 rows** (`docs/MAP_FRAMES.md`,
  `tools/xemu/map-frame.py`, `inventory/map_frames.csv`): the HUD's portrait-and-name corner is drawn on 65
  frames and absent on 7, so every surviving frame says whether the load reached play. What is left of this
  question is the checkpoint half, not the picture.
* **Whether the other in-game routes reach a second-half map.** None of the 30 needs one any more: every
  campaign index loads from this page (`GetMapList` enumerates every `.unr`, no cap), so a save inside one, a
  checkpoint load and a `Travel` URL are alternatives rather than requirements -- and the console refuses
  `open` (`docs/XEMU.md`, U16), which is the route that would have made them necessary.

## Status

<!-- generated:map-loads-status -->
**73 rows over 91 boots, 71 PASS and 2 FAIL.**

All **61** campaign entries (10-70) have a row, over 74 of the boots; the other 12 rows are the page's entries outside that range (17 boots).

"Complete" here means coverage, not playability: a `PASS` row is a load, and the two questions under "What is left" are what a row still cannot answer.

<!-- /generated:map-loads-status -->

`make ci` gates both halves. `--check-doc` re-renders this page from the committed rows, so the table cannot
drift from the CSV without a disc, and the tool's synthetic `--check` exercises the merge rules themselves (a
later PASS wins, attempts add up, a reason is kept, an unknown index or status is refused), so the instrument
cannot rot behind a table that looks right.
