# Class defaults: readable for two thirds of the tree

The roster link `map → spawner → squad script → roster` ends in a class default, and the compile loop (3.6)
needs defaults as an input. Three defects stood between this reader and that data; all three are fixed, and the
coverage is measured rather than asserted.

    python3 tools/class-defaults.py DUMP/System --class SGOrder
    python3 tools/class-defaults.py DUMP/System --sweep -o inventory
    python3 tools/class-defaults.py DUMP/System --class SGOrder --walk   # byte-level, for alignment questions

## What was wrong, and what it cost

| defect | symptom | fix |
|---|---|---|
| `Str` (type 13) read as a name index | `Description` decoded as the *name* `BeginState` | read it as an FString: `size` bytes of text |
| block required to end exactly at the export end | 73 of 306 `StargateGame` classes had "no block" | allow a bounded trailer (≤ 64 bytes) after the terminator |
| super chain followed within the package only | inherited properties invisible, so correct blocks looked undeclared (18 of 306 "fully declared") | follow `inventory/classes.csv`'s super map, which is cross-package |

`SGOrder` now reads cleanly:

```
canPerformOneill      Bool     True
canPerformCarter      Bool     True
canPerformTealc       Bool     True
canPerformJackson     Bool     True
Description           Str      Empty Order
maxBotsAssigned       Int      -1
iconTexture           Object   icon_mission'Engine'
Condition             Byte     1
```

Eight defaults, every one declared by the class, values that read like authoring data.

## Coverage, all 18 packages

`inventory/class_defaults_coverage.csv` has the per-package rows:

| | classes | blocks found | every property declared |
|---|---:|---:|---:|
| `StargateContent` | 567 | 541 | **441** |
| `Engine` | 367 | 287 | 170 |
| `StargateGame` | 306 | 248 | 150 |
| `GamePlay` | 262 | 260 | 180 |
| `Sharpeffects` | 158 | 157 | **156** |
| `GUI` | 148 | 139 | 128 |
| `StargateVehicles` | 142 | 133 | 117 |
| `StargateMenus` | 98 | 82 | 65 |
| `UWindow` | 78 | 61 | 5 |
| others (9 packages) | 193 | 174 | 140 |
| **total** | **2,319** | **2,084** | **1,552 (67%)** |

Two thirds of the tree now yields a defaults block whose property names the class actually declares — up from
18 of 306 in the previous attempt, a factor of eight on the package that motivated it.

## Where it still fails, and why

* `UWindow` (5 of 78) and `Fire` (0 of 7) are the worst. `UWindow`'s classes are engine-heavy GUI types whose
  declared properties mostly come from `GUI`/`Core` parents; `Fire`'s are fractals with almost no declared
  properties, so "every property declared" is the wrong test there — they may well be correct blocks scored
  harshly.
* 235 classes still have no candidate: whether their block is absent or failing the trailer bound is not
  measured.
* Ambiguous cases (64 in `StargateGame`, 79 in `StargateContent`) are ties between blocks with equally
  declared names; the tie-break prefers the longer block and then the smaller trailer, which is a rule of
  thumb, not a proof.

## Not measured

* Default *values* in bulk — this file reports one class's values, and defaults are authoring data for the whole
  game.
* The roster itself: the squad scripts maps name (`TSS_7A_B_Jaffa_01`, `AI_Jaffa_Horus_Med_single`) appear in
  those packages' name tables but are neither imports nor exports, so the reference to them lives in data
  inside the `Level` export, which this reader does not parse.
