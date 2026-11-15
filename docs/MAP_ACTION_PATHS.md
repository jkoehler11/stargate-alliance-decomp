# The paths an `Actions` array allows, and what each branch reads

`docs/MAP_ACTION_ORDER.md` measured the order inside a sequence's `Actions` array — index 0 first, five classes
move the counter — and closed on the one thing it would not claim: a schedule. Its words were that
`ACTION_IfCondition` and `ACTION_IfProximity` "decide at run time, so which side of a section a given play takes
is not in the package. What is in the package is the set of paths the classes allow."

This page is that set, measured. Two reads, both from shipped text rather than from bytes guessed at:

* **the walk**, because the skip is a function and the build ships it: `ScriptedAction.ProceedToSectionEnd` steps
  forward from the branch's next index counting `StartsSection` (`ACTION_IfCondition`, `ACTION_IfProximity`,
  `ACTION_IfRandomPct` each answer true) and `EndsSection` (`ACTION_EndSection`), and stops where the nesting
  goes negative. Three lines of UnrealScript, so a branch is a node with two edges rather than an unknown.
* **the operand**, because every conditional reads a property of the *placed element*: a `TriggeredConditionTag`,
  a `ProximityActorTag`/`ActorTags`/`Radius` triple, or a `Probability`. The tag is resolved against the map's
  own export table — the same read `docs/MAP_EVENT_WIRING.md` uses for event names — so "does this branch's
  condition exist in this map" is a join, not a guess.

`tools/action-paths.py` does both in one pass over the 73 maps and joins them:

    python3 tools/action-paths.py "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15" --sweep -o inventory
    python3 tools/action-paths.py --report
    python3 tools/action-paths.py --write-doc docs/MAP_ACTION_PATHS.md
    python3 tools/action-paths.py --check        # synthetic arrays, no dump, writes nothing

`inventory/action_paths.csv` holds the rows. The `straight` column is `action-order`'s own walk run again over
the same arrays in the same pass, and the sweep checks it against that tool's committed artifact on all 73 maps,
so the path walk is that measurement plus branch edges rather than a second opinion about the same bytes.

<!-- generated:action-paths -->
**Every conditional in an `Actions` array reads an operand the placement carries, and 16888 of the 16907 slots a map's sequences wire are on some path: the straight index run reaches 16694, a branch's skip edge reaches 200 more, and 6 of the slots that count includes are on no path at all.**

`docs/MAP_ACTION_ORDER.md` measured the order and stopped at a schedule: "`ACTION_IfCondition` and `ACTION_IfProximity` decide at run time, so which side of a section a given play takes is not in the package". The decision is, though -- not `FRand()`'s draw, but *what the branch reads*, which is placed data. All three conditionals read a property of the placed element itself, and `ScriptedAction.ProceedToSectionEnd` is the skip they share.

| measure | answer |
|---|---|
| maps read | **73** |
| conditional classes in the build | **3** |
| arrays walked | **2437** (16907 elements) |
| branch slots in them | **260** (`ACTION_IfCondition` 224, `ACTION_IfProximity` 0, `ACTION_IfRandomPct` 36) |
| branches whose skip finds its `EndSection` | **255** (none with an empty body: every branch that pairs skips at least one slot) |
| branches whose skip runs off the array (the run ends there) | **5** |
| slots reached by some path (the union) | **16888** of 16907, against **16694** for the straight run |
| slots a branch can skip (conditional) | **649** |
| slots every path runs (the straight run minus a branch's span) | **16239** |
| slots the straight count includes but no path from index 0 returns to (a backward goto) | **6** |
| slots a branch edge reaches that the straight run does not | **200** |
| placed `TriggeredCondition` actors across the maps | **211** |
| `ACTION_IfCondition` slots whose condition actor's tag a placed property in the same map fires / fires nowhere | **199 / 11** |
| branch slots whose operand is stated | **257** (3 unset) |
| branch slots whose operand resolves in their own map | **246** (11 do not) |
| `ACTION_IfCondition` slots whose placed actor states `bEnabled` true / false / nothing | **105 / 0 / 105** |

**`ACTION_IfProximity` is placed in no map.** The class ships, its body is complete, and not one of the 260 branch slots in the corpus is one -- so the proximity test is a branch the build carries and the maps never take, and every branch that does exist reads a tag or a probability.

**The skip is `ProceedToSectionEnd`, and it lands on the `EndSection`.** The function walks forward from `C.ActionNum` counting `StartsSection` and `EndsSection`, and returns where the nesting goes negative -- *before* its own increment, so the counter is left on the matching `EndSection` and that element still runs. A branch is therefore a two-edge node: index + 1 on the fall-through, and the matching `EndSection` on the skip. Where the nesting never goes negative the loop walks off the array and the run simply stops, which is the 5 unmatched branches above.

**Each conditional's operand is a placed property, so the branch's first pass is in the package.**

| class | operand it reads | what decides the skip |
|---|---|---|
| `ACTION_IfCondition` | `TriggeredConditionTag`, resolved against every `TriggeredCondition` actor in the same map | `!T.bEnabled` skips; a tag that resolves to nothing leaves `T` at `None`, and `None.bEnabled` is false, so it skips |
| `ACTION_IfProximity` | `ProximityActorTag`, `ActorTags`, `Radius` | its first line refuses the test unless all three are set, and then every tag must have an actor inside `Radius` of the proximity actor |
| `ACTION_IfRandomPct` | `Probability` | `FRand() > Probability` skips, so a placement at or below 0 skips every time and one at or above 1 never does |

`TriggeredCondition` is a plain `var() bool bEnabled` that only its own `Trigger` flips: `bToggled` toggles it, otherwise it takes the inverse of `bInitialValue`, which `PostBeginPlay` copies from the placed value, and `bTriggerControlled` lets `Untrigger` put it back. So a placement that states `bEnabled=True` is a branch that falls through until something triggers that actor, and one that states nothing inherits the class default. 105 of the 210 resolved conditions state `True`, 0 state `False`, and **105 state nothing** -- recorded as unstated rather than guessed, because a bool the class does not list in its defaults block is a value this read cannot see. What stays outside the package is whether a trigger fires that actor later in the play: that is `docs/MAP_EVENT_WIRING.md`'s name space, and it can only change a branch after the first pass through it.

| sequence class | arrays | elements | branches | reached / straight | conditional | matched / unmatched / empty |
|---|---|---|---|---|---|---|
| `TeamScriptedSequence` | 1141 | 8717 | 73 | 8711 / 8663 | 192 | 73 / 0 / 0 |
| `ScriptedTrigger` | 1073 | 7074 | 168 | 7065 / 6951 | 413 | 163 / 5 / 0 |
| `TriggeredScript` | 193 | 749 | 19 | 745 / 713 | 44 | 19 / 0 / 0 |
| `UnrealScriptedSequence` | 25 | 349 | 0 | 349 / 349 | 0 | 0 / 0 / 0 |
| `ScriptedSpawner` | 4 | 15 | 0 | 15 / 15 | 0 | 0 / 0 / 0 |
| `ScriptedSequence` | 1 | 3 | 0 | 3 / 3 | 0 | 0 / 0 / 0 |

**Two readers, one population.** The `straight` column is `tools/action-order.py`'s walk run again over the same arrays in the same pass: it agrees with the committed `inventory/action_order.csv` on **73 of 73 maps** and reproduces its 16907 elements and 2437 arrays exactly, so the path walk above is that walk plus branch edges rather than a second opinion.

## Reading a row

`kind=map` rows are the per-map counts, `kind=holder` rows the same counts per sequence class (with the scene manager's camera arrays counted apart, not walked), `kind=class` rows are the conditional classes with the operand property their own body reads, and `kind=census` the corpus total with the control in its `note`.

`reach` is the union of every path, `straight` is `action-order`'s single index run, `cond_slots` is the part of `reach` that lies inside a branch's skippable span, and `always` is `reach - cond_slots` -- the slots no path avoids. A branch with an empty body adds no conditional slot, which is why `empty_body` is counted on its own.

## What this does not claim

* **Which pass a play takes.** The operand is placed data, but `FRand()` is not, and a trigger that fires a `TriggeredCondition` mid-play changes the next pass through that branch. The fire side is measured as far as a name can be: 199 of the 210 resolved conditions name an actor whose tag another placement in the same map fires (`event-targets`' reader, the same join `docs/MAP_EVENT_WIRING.md` uses), and 11 name one nothing in the map fires -- a condition that keeps its placed value for the whole play. Whether the firing placement is itself reached is `docs/MAP_ACTION_REACH.md`'s question, and it is answered there as a closure over the three readings rather than left to a runtime trace.
* **That a resolved tag is the actor the engine finds.** `ForEach C.AllActors(class, T, tag)` takes the first match in actor iteration order, and a tag is not unique; the tool counts a shared tag as resolved because the class's own comment says it will use the first one found.
* **That an unset operand is a bug.** A condition with no tag, or a probability at zero, is a branch that always skips -- a legal way to write a block out of a sequence, and only the author knows whether it was meant.

<!-- /generated:action-paths -->
