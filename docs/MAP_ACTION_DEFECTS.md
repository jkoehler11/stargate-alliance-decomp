# The placements their own data does not decide, and the 36 draws `FRand()` owns

`docs/MAP_ACTION_PATHS.md` reported three counts without naming them — 5 branches whose `ProceedToSectionEnd`
walks off the array, 3 that state no operand, 11 conditions whose `TriggeredCondition` no placed property fires —
and `docs/MAP_ACTION_REACH.md` closed the reach question beside them. Neither listed the placements themselves,
which is what a player-visible bug would come from, and both left `FRand()` as a caveat.

This page is that list. `tools/action-defects.py` walks the 73 maps once and writes one row per defect — map,
the element's class, the holding sequence's class, the slot index — under five kinds:

| kind | what it is | why it matters |
|---|---|---|
| `skip_off_array` | a branch whose section never closes | the skip edge has no target, so the run stops at the branch instead of jumping its body |
| `operand_unset` | a branch that states no operand | the class's own default decides every pass; `ACTION_IfCondition` with no tag leaves `T` at `None` and skips |
| `unfired_condition` | an `ACTION_IfCondition` whose `TriggeredCondition` tag nothing fires | the placed `bEnabled` is the only value that branch can ever see |
| `random_decided` | an `ACTION_IfRandomPct` at or below 0 or at or above 1 | a coin the placement has already called |
| `slot_unreached` | an action a sequence wires at a slot no path reaches | it is wired and never runs, whatever the sequence does |

    python3 tools/action-defects.py "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15" --sweep -o inventory
    python3 tools/action-defects.py --report
    python3 tools/action-defects.py --write-doc docs/MAP_ACTION_DEFECTS.md
    python3 tools/action-defects.py --check         # synthetic maps, no dump, writes nothing

`inventory/action_defects.csv` holds the instances first, then the class table (which operand each branch class
reads), then a per-map row and one `census` row per kind. No placement name and no tag value is written; a map, a
class and a slot index are enough to find the placement in the dump.

<!-- generated:action-defects -->
**19 branch placements in the 73 maps are decided by nothing but their own default, and 0 `ACTION_IfRandomPct` slots are a coin the placement already called -- so `FRand()` decides the 36 that are left.**

`docs/MAP_ACTION_PATHS.md` reported these as counts and `docs/MAP_ACTION_REACH.md` closed the reach question beside them; this page is the list those counts are made of. Each row names a map, the class of the element and the slot index inside its sequence, because what a defect is depends on where it sits: a branch that skips past the end of its array stops a run, while the same branch one slot earlier would only skip a body.

| defect | placements | what it means |
|---|---:|---|
| `skip_off_array` | **5** | the branch's section never closes, so `ProceedToSectionEnd` walks off the array and the run ends there |
| `operand_unset` | **3** | the branch states no operand, so the class default decides every pass |
| `unfired_condition` | **11** | the `TriggeredCondition` it names is fired by no placed property, so the placed `bEnabled` is its value for the whole play |
| `random_decided` | **0** | `Probability` at or below 0 (skips every pass) or at or above 1 (never skips): placed data, not a draw |
| `slot_unreached` | **101** | an action wired at a slot no path reaches |

| control | this walk | `action_paths.csv` |
|---|---:|---:|
| branch slots | **257** | 260 |
| skip that walks off the array | **5** | 5 |
| branch with no operand stated | **3** | 3 |

**`FRand()`'s draw is 36 slots, not a schedule.** Of the 36 `ACTION_IfRandomPct` placements that walk a path, 36 state a probability strictly between 0 and 1 -- the population a play's randomness actually decides -- and 0 state one the placement has already resolved. That is the whole of what 5.2 could not settle, and it is now a count rather than a caveat.

The first rows of `inventory/action_defects.csv` are the placements themselves -- one row per defect, `kind=defect`, with its map, element class, holding sequence class and slot index -- so a claim about a map can be checked against the artifact instead of re-derived.

### The defect placements by kind and class

| defect | element class | placements |
|---|---|---:|
| `operand_unset` | `ACTION_IfCondition` | 3 |
| `skip_off_array` | `ACTION_IfCondition` | 5 |
| `slot_unreached` | `ACTION_TriggerEvent` | 20 |
| `slot_unreached` | `ACTION_WaitForTimer` | 20 |
| `slot_unreached` | `ACTION_Talk` | 11 |
| `slot_unreached` | `ACTION_GotoAction` | 10 |
| `slot_unreached` | `ACTION_WaitForEvent` | 10 |
| `slot_unreached` | `ACTION_LeaveSequence` | 7 |
| `slot_unreached` | `ACTION_PlaySound` | 4 |
| `slot_unreached` | `ACTION_EndSection` | 3 |
| `slot_unreached` | `ACTION_IfCondition` | 3 |
| `slot_unreached` | `ACTION_SetAlertness` | 3 |
| `slot_unreached` | `ACTION_SetBotScript` | 3 |
| `slot_unreached` | `ACTION_MoveToPoint` | 2 |
| `slot_unreached` | `ACTION_StopShooting` | 2 |
| `slot_unreached` | `ACTION_DisplaySGText` | 1 |
| `slot_unreached` | `ACTION_SaveGame` | 1 |
| `slot_unreached` | `Action_PLAYANIM` | 1 |
| `unfired_condition` | `ACTION_IfCondition` | 11 |

| branch class | operand it reads | checks |
|---|---|---|
| `ACTION_IfCondition` | `TriggeredConditionTag` | condition |
| `ACTION_IfProximity` | `ProximityActorTag` | proximity |
| `ACTION_IfRandomPct` | `Probability` | random |

Census: maps=73;branches=257;skip_off_array=5;operand_unset=3;unfired_condition=11;random_decided=0;random_drawn=36;slot_unreached=101.
<!-- /generated:action-defects -->

## What the list says that the counts did not

* **The random population is one class of branch, not a caveat.** No `ACTION_IfRandomPct` in the corpus states a
  probability at either end, so every one of them is a real draw. What 5.2 called "not in the package" is exactly
  this population and nothing else — and `ACTION_IfProximity`, the third conditional class, is placed nowhere.
* **The dead conditions are a small, closed set.** Eleven `ACTION_IfCondition` placements name a
  `TriggeredCondition` that no placed property fires. Together with the three branches that state no operand and
  the five whose section never closes, the undecided branch population is 19 placements out of 260 — and none of
  the five open sections is in the same map as another.
* **`slot_unreached` is a different defect from a stalled sequence.** `docs/MAP_ACTION_REACH.md` counts 9,096 wired
  actions behind no start; these are the ones whose *own sequence* starts and whose slot still cannot be reached —
  a branch's skip edge or a `LeaveSequence` ends the run before them. The two populations do not overlap, and
  neither one is a subset of the other's count.

## What this does not claim

* **That a defect breaks a mission.** A branch that always skips, or a run that ends early, may be the author's
  way of writing a block out of a sequence. The list says where the data decides and how, not whether it was meant.
* **That the placements are all of them.** A conditional reached only through a slot no path reaches is not
  walked, so its operand is not examined: `operand_unset` and `unfired_condition` are lower bounds on the class of
  defect, while `skip_off_array` and `slot_unreached` are exact for the paths `docs/MAP_ACTION_PATHS.md` measures.
* **That `FRand()`'s value is knowable here.** A drawn probability is a runtime value by definition; what this page
  settles is that the draw decides 36 placements, all of them placed data's own random branch.
