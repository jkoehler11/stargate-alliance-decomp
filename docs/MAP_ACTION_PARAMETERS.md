# What a placed action says about itself — its parameters, and the defaults that decide a run

`docs/MAP_ACTION_WIRING.md` through `docs/MAP_ACTION_DEFECTS.md` read the graph a map's placed actions sit in:
which sequence names which action, in what order, on which path, whether the sequence starts, and which branches
its own data cannot decide. Every one of those pages reads *one* parameter per branch — `ActionNumber`,
`Probability`, `TriggeredConditionTag`. None of them asked what the rest of a placement carries, and the answer
turns out to be the missing half of "placed data": an action's property block is the action's argument list.

A placement states what it overrides; **a parameter it does not state is its class's own `var(Action)` default**, so
the package decides every parameter either way. This page is the census of that surface — one row per class and
parameter, with how many placements state it — and the four defaults that decide a run on their own:

* `ACTION_GotoAction.ProceedToNextAction` assigns `C.ActionNum = Max(0, ActionNumber)`. A placement that states no
  `ActionNumber` therefore does not fall through: it jumps the run back to **slot 0**.
* `ACTION_WaitForEvent.InitActionFor` resolves a `TriggeredCondition` with
  `foreach C.DynamicActors(Class'Gameplay.TriggeredCondition', t, ExternalEvent)`. A placement that states no
  `ExternalEvent` passes the name `None`, which the iterator does not filter on; the loop body is a bare `break`, so
  the **first** `TriggeredCondition` in the level is the one whose `bEnabled` decides whether the wait completes
  immediately — and this page now names that condition for every one of the 19 placements that leave the name out.
* `ACTION_TriggerEvent.InitActionFor` hands its `Event` to `C.TriggerEvent(Event, ...)`, and `Actor.TriggerEvent`
  returns at the top when the name is `None` — so a placement that states no `Event` fires **nothing**, where the
  same name on the wait side filters nothing.
* `ACTION_WaitForTimer` hands `PauseTime` straight to `C.SetTimer(PauseTime, false)`. The value is in seconds and
  the largest placed value is how long a sequence can sit still; 100,000 seconds is the corpus's way of saying "not
  in this play" without ending the run.

    python3 tools/action-parameters.py "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15" --sweep -o inventory
    python3 tools/action-parameters.py "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15" --relocate -o inventory
    python3 tools/action-parameters.py --report
    python3 tools/action-parameters.py --write-doc docs/MAP_ACTION_PARAMETERS.md
    python3 tools/action-parameters.py --check-doc docs/MAP_ACTION_PARAMETERS.md
    python3 tools/action-parameters.py --check          # synthetic, no dump, writes nothing

`inventory/action_parameters.csv` holds a `map` row per map, a `class` row per action class, a `property` row per
(class, parameter) with its type, how many placements state it, and — for a numeric parameter — its min, max and
median; a `residue` row for each placement whose default is the interesting part (a goto with no target, a wait
with no event, a property the class chain does not declare); and the corpus census. The walk uses the same property
reader as `actor-properties` and the checkpoint work (`tools/prop-stream.py`), its placed-action total is checked
against `action_wiring.csv`, and every statement is checked against `class_properties.csv` — the class chain has to
declare the name, which is what refuses a mis-parsed block's invented property instead of publishing it.

That refusal is also the locator's control, so it comes with a repair: a refused block is re-located by scoring the
export's every candidate start on the *fraction* of names its class chain declares (the chooser
`tools/class-defaults.py` ships for a class's defaults block) and adopting the better start — never a worse one.
`inventory/action_block_relocation.csv` (`--relocate`, `make action-relocation`) is the measurement that says the
rule is a repair: over all 73 maps the chooser picks exactly the shipped locator's start on the 15,429 placements
whose block was never refused, so the only blocks it moves are the ones the declaration control had already
rejected. Those twelve blocks also carry declared names the long parse had dropped, so the published statement
total rises from 21,584 to 21,596 — more than the 21 refused statements the repair recovers.

**No parameter value that is content is written.** A name-typed parameter contributes its distinct count and how
often it is `None`; a string parameter only whether it is stated; a residue row names a map, a class and a slot.
That is enough to find a placement in the dump and not enough to carry game data into the repository.

<!-- generated:action-parameters -->
**18510 placed actions over 83 classes carry 21596 stated properties in 73 of the 73 maps' packages; every other parameter is the class default, and three of those defaults decide a run on their own.**

The wiring, order, path, reach and defect pages read the graph the actions sit in. This page reads what each placement says about itself: its property block, parsed with the shared reader (`tools/prop-stream.py`, the same one `actor-properties` and the checkpoint work use). A property the block does not state is the class's own `var(Action)` default, so a parameter is decided by the package either way -- and for three classes the default is enough to change what a play does.

| population | count |
|---|---:|
| placed actions (the `ScriptedAction` family, minus its two bases) | **18,510** |
| with a property block that terminates exactly at the export's end | **15,441** |
| with no block at all (the class default is the whole placement) | **3,069** |
| property statements read | **21,596** |
| placement classes | **83** |

| control | this walk | committed artifact |
|---|---:|---:|
| placed actions | 18,510 | `action_wiring.csv` totals 18,510 |
| property statements refused because the class chain does not declare them | 5 (was 26 before the re-location) | `class_properties.csv` |

### The three defaults that decide a run

| parameter | placements | stated | default decides | what the class's own text does with it |
|---|---:|---:|---:|---|
| `ACTION_GotoAction.ActionNumber` | 580 | 165 | **415** | `C.ActionNum = Max(0, ActionNumber)`: a placement that states none sends the run back to slot 0 |
| `ACTION_WaitForEvent.ExternalEvent` | 2722 | 2703 | **19** | the tag is looked up under `None`, which the engine treats as no filter, so the level's first `TriggeredCondition` decides whether the wait completes at once |
| `ACTION_WaitForTimer.PauseTime` | 2216 | 2214 | 2 | handed straight to `SetTimer(PauseTime, false)`, so the largest placed value is how long a sequence can sit still |

**A goto with no `ActionNumber` is a jump to the first element, and none of the 415 sits at the first element already -- each one re-runs its own sequence from the top. `ProceedToNextAction` clamps the value (`Max(0, ActionNumber)`), so the default is slot 0 rather than a no-op. 385 of them sit on a slot of some sequence's array -- the population `MAP_ACTION_ORDER` counts as gotos with no target (its 377 is by slot on its own walk) -- and 30 in none. Of the 165 that state one, 111 target a slot behind themselves, 40 a slot ahead, 0 their own slot, and 14 sit in no sequence's array at all.

**`PauseTime` is the sequence's clock, and the top of its range is a park.** The class hands the value straight to `SetTimer(..., false)`, so 2214 placements state a pause and 2 state none at all -- a zero-second timer that fires as soon as the action starts, and both are named in the residue rows. The stated values run from 0.001 to 1e+05 seconds (median 2); the top is 27.8 hours, the corpus's way of holding a sequence where it is for a whole play, and 0 placements sit at or below zero.

**The wait with no `ExternalEvent` is a resolved lookup, not a caveat.** `InitActionFor` runs `foreach C.DynamicActors(Class'Gameplay.TriggeredCondition', t, ExternalEvent)` with the name the placement states; a placement that states none passes `None`, which the iterator does not filter on, and the loop body is a bare `break`, so the **first** condition the level yields is the one kept. The test that follows is `if(t != none && t.bEnabled) return false` -- an enabled condition completes the wait at once, a disabled one lets the action block, and a map with no condition leaves `t` at `None` and blocks the same way. 2703 of the 2722 `ACTION_WaitForEvent` placements state the name and **19 do not** -- the same 19 the `MAP_EVENT_WIRING` wait count was short of this placement total. Of those 19, **4 complete at once**; the other **15 block** -- 10 behind a disabled first condition and 5 in a map that places no `TriggeredCondition` at all. The class's `bCheckRecentEvents` arm cannot rescue any of them (it is guarded by `ExternalEvent != 'None'`), and **18 of the 19 do not depend on the actor order at all**, because every condition their map carries resolves the same way. The one row that does depend on it is `M15A`'s: five conditions, one enabled and four not, and the package's own order puts the enabled one first.

| map | sequence slot | `TriggeredCondition`s | the first one's `bEnabled` | what the wait does |
|---|---:|---:|---|---|
| `M00` | 3 | 6 | default (false) | blocks |
| `M00` | 5 | 6 | default (false) | blocks |
| `M00` | 5 | 6 | default (false) | blocks |
| `M00` | 5 | 6 | default (false) | blocks |
| `M00` | 16 | 6 | default (false) | blocks |
| `M00` | 25 | 6 | default (false) | blocks |
| `M14A` | 6 | 4 | default (false) | blocks |
| `M14A` | 6 | 4 | default (false) | blocks |
| `M15A` | 9 | 5 | stated true | completes |
| `M16D` | 7 | 0 | none | no_condition |
| `M16D` | 7 | 0 | none | no_condition |
| `M16D` | 9 | 0 | none | no_condition |
| `M16D` | 9 | 0 | none | no_condition |
| `M16E` | 3 | 0 | none | no_condition |
| `M16F` | 5 | 1 | stated true | completes |
| `M16F` | 5 | 1 | stated true | completes |
| `M16F` | 11 | 1 | stated true | completes |
| `M17A` | 1 | 4 | default (false) | blocks |
| `M17A` | 2 | 4 | default (false) | blocks |

**The fire with no name is a no-op, not a broadcast.** `ACTION_TriggerEvent.InitActionFor` hands its `Event` to `C.TriggerEvent(Event, ...)`, and `Actor.TriggerEvent` returns at the top on `EventName == 'None'` -- before the actor iterator and before `Level.AddEventToCache`. So the **1** `ACTION_TriggerEvent` placement that states no `Event` fires nothing at all, which is the opposite of what the same name means on the wait side: there `None` is an iterator tag that filters nothing, here it is an early return. The class states its event on 2657 of its 2658 placements.

**`ACTION_Talk` carries its subtitle in the package.** 718 of its 719 placements state `SubTitleText` (a localized `string`, so the text ships in the map, not only in a voice package), 256 state `DialogueFinishedEvent`, and the parameter surface is otherwise `TalkerTag`/`DialogueName`/`myDirectory`. `Action_PLAYANIM` -- the class whose name the `ACTION_` prefix cannot see -- states `BaseAnim` on 227 of its 227 placements, which is what makes it a real action rather than a marker.

### The re-located blocks, and the five tags the class does not declare

A block is located by where it *ends*: every property boundary in the export's first 64 bytes is a valid start, so "earliest start with the most properties" is a tie-break rather than a reading, and on a minority of placements it lands inside the block. The control that catches that is the class declaration (`class_properties.csv`), which refuses a block naming a property its chain does not declare. **12 placements carried 26 refused statements, and 8 of the 12 are a start the locator picked too early.** Re-scoring every candidate start by the *fraction* of declared names -- `tools/class-defaults.py`'s chooser, the rule the class-defaults work already ships -- moves 8 placements and recovers **21 statements**; the five that remain are not a locator defect at all.

| map | class | sequence slot | start | refused names it recovered | still refused |
|---|---|---:|---:|---|---|
| `M07B` | `ACTION_Talk` | 9 | 3 -> 0 | `Oneill`, `m07_12_g_oneill`, `SheerRate`, `StaticMeshActor832`, `Brush98` | none |
| `M14A` | `ACTION_SetIMusic` | 0 | 0 -> 4 | `UseStateCheck` | none |
| `M14A` | `ACTION_SetIMusic` | 1 | 0 -> 4 | `UseStateCheck` | `EndType` |
| `M15A` | `ACTION_DisplayMessage` | 0 | 4 -> 0 | `Max`, `Roster_JaffaHorusM8`, `Location` | none |
| `M15D` | `ACTION_DisplayMessage` | 0 | 4 -> 0 | `Distance`, `StaticMeshInstance5933`, `Region` | none |
| `M15F` | `ACTION_SaveGame` | 0 | 9 -> 0 | `Group`, `SubActionFade0` | none |
| `M20A` | `ACTION_DisplayMessage` | 0 | 4 -> 0 | `StaticMeshActor`, `StaticMeshInstance584`, `Region` | none |
| `M20C` | `ACTION_DisplayMessage` | 0 | 4 -> 0 | `Group`, `SpriteEmitter198`, `Level` | none |

**The five left are one name on one class.** Every remaining refusal is `EndType` on `ACTION_SetIMusic`, in two maps; the name is a declared property of `Engine.IMusicActor` (`UByteProperty`, enum `LevelInfo.EIMusicEndType`), and `ACTION_SetIMusic`'s own chain declares four names, none of them `EndType`. The tag sits *last* in a block whose other names are all declared, its value is a one-byte enum index, and the same class's other placements in the same maps read back as exactly its four names -- so the block is where it belongs and the tag is the residue: a placement authored against a class revision that declared one more property than the shipped `GamePlay.u` does. The engine's property reader skips a tag it cannot resolve by the size in its info byte, which is what makes the leftover harmless at run time -- `inventory/tag_origin.csv` measures the name's origin and `docs/NATIVE_PROPERTIES.md` §4 the walk that skips it.

| map | class | sequence slot | refused name |
|---|---|---:|---|
| `M02A` | `ACTION_SetIMusic` | 1 | `EndType` |
| `M14A` | `ACTION_SetIMusic` | 1 | `EndType` |
| `M14A` | `ACTION_SetIMusic` | 1 | `EndType` |
| `M14A` | `ACTION_SetIMusic` | 3 | `EndType` |
| `M14A` | `ACTION_SetIMusic` | 4 | `EndType` |

**The chooser is a repair, not a rewrite.** Run over every placement in all 73 maps (`--relocate`, `inventory/action_block_relocation.csv`), the declared-name chooser picks exactly the start `find_block` does on **all 15,429 placements the refusal never fired on** (0 moved) -- so the rule changes no published block, and the 8 it does change are precisely the rows that were refused.

### Each class's parameter surface

| class | placements | properties it states | block / no block |
|---|---:|---|---|
| `ACTION_WaitForEvent` | 2,722 | `ExternalEvent`, `bCheckRecentEvents` | blocked=2703; unblocked=19 |
| `ACTION_TriggerEvent` | 2,658 | `Event` | blocked=2657; unblocked=1 |
| `ACTION_WaitForTimer` | 2,216 | `PauseTime` | blocked=2214; unblocked=2 |
| `ACTION_SetAlertness` | 1,404 | `Alertness` | blocked=1066; unblocked=338 |
| `ACTION_MoveToPoint` | 1,266 | `DestinationTag` | blocked=1255; unblocked=11 |
| `ACTION_SetViewTarget` | 901 | `ViewTargetTag` | blocked=739; unblocked=162 |
| `ACTION_Talk` | 719 | `DialogueFinishedEvent`, `DialogueName`, `ListenDistance`, `LookAtTag`, `Play3D`, `SubTitleText`, `TalkerTag`, `myDirectory` | blocked=719; unblocked=0 |
| `ACTION_GotoAction` | 580 | `ActionNumber` | blocked=165; unblocked=415 |
| `ACTION_SetBotScript` | 557 | `ScriptTargetTag`, `TargetPlayerName`, `TargetTag` | blocked=557; unblocked=0 |
| `ACTION_Run` | 423 | none | blocked=0; unblocked=423 |
| `ACTION_TeleportToPoint` | 326 | `DestinationTag`, `bPlaySpawnEffect` | blocked=322; unblocked=4 |
| `ACTION_LeaveSequence` | 301 | none | blocked=0; unblocked=301 |
| `ACTION_SetIMusic` | 281 | `NewState`, `SetActor`, `SetState`, `iMusicActorTag` | blocked=281; unblocked=0 |
| `ACTION_EndSection` | 279 | none | blocked=0; unblocked=279 |
| `ACTION_Walk` | 278 | none | blocked=0; unblocked=278 |
| `ACTION_SquadMoveInFormation` | 271 | `DestinationTag`, `FormationClass` | blocked=270; unblocked=1 |
| `ACTION_Crouch` | 269 | none | blocked=0; unblocked=269 |
| `ACTION_PlayAmbientSound` | 247 | `ActorTag`, `AmbientSound`, `Emitters`, `SoundPitch`, `SoundRadius`, `SoundVolume` | blocked=245; unblocked=2 |
| `ACTION_IfCondition` | 244 | `TriggeredConditionTag` | blocked=241; unblocked=3 |
| `Action_PLAYANIM` | 227 | `AnimIterations`, `AnimRate`, `BaseAnim`, `BlendInTime`, `BlendOutTime`, `StartFrame`, `bLoopAnim` | blocked=227; unblocked=0 |
| `ACTION_ShootTarget` | 211 | `FiringMode`, `NumShots`, `ViewTargetTag`, `bSpray` | blocked=211; unblocked=0 |
| `ACTION_PlaySound` | 180 | `ActorTag`, `Pitch`, `Sound`, `Volume`, `bAttenuate` | blocked=173; unblocked=7 |
| `ACTION_SetStance` | 156 | `Stance` | blocked=39; unblocked=117 |
| `ACTION_PlaySGSound` | 121 | `PauseTime`, `Pitch`, `Sound`, `SoundSourceTag`, `SoundType`, `SubTitleText`, `SubtitleDuration`, `Volume`, `bAttenuate`, `bWaitPauseTime`, `whoSpoke` | blocked=121; unblocked=0 |
| `ACTION_SetBotMoveMode` | 115 | `MovePreset`, `PawnTag` | blocked=93; unblocked=22 |

### The widest parameters

| class | property | type | stated | distinct | min | max | median |
|---|---|---|---:|---:|---:|---:|---:|
| `ACTION_WaitForEvent` | `ExternalEvent` | Name | 2703 | 1557 |  |  |  |
| `ACTION_TriggerEvent` | `Event` | Name | 2657 | 1851 |  |  |  |
| `ACTION_WaitForTimer` | `PauseTime` | Float | 2214 | 98 | 0.001 | 1e+05 | 2 |
| `ACTION_MoveToPoint` | `DestinationTag` | Name | 1255 | 1094 |  |  |  |
| `ACTION_SetAlertness` | `Alertness` | Byte | 1066 | 3 | 1 | 3 | 2 |
| `ACTION_SetViewTarget` | `ViewTargetTag` | Name | 739 | 318 |  |  |  |
| `ACTION_Talk` | `DialogueName` | Name | 719 | 704 |  |  |  |
| `ACTION_Talk` | `myDirectory` | Str | 719 | 31 |  |  |  |
| `ACTION_Talk` | `SubTitleText` | Str | 718 | 546 |  |  |  |
| `ACTION_Talk` | `TalkerTag` | Name | 714 | 19 |  |  |  |
| `ACTION_SetBotScript` | `ScriptTargetTag` | Name | 556 | 498 |  |  |  |
| `ACTION_SetBotScript` | `TargetTag` | Name | 539 | 73 |  |  |  |
| `ACTION_TeleportToPoint` | `DestinationTag` | Name | 322 | 296 |  |  |  |
| `ACTION_Talk` | `LookAtTag` | Name | 282 | 27 |  |  |  |
| `ACTION_SetIMusic` | `SetState` | Bool | 280 | 1 |  |  |  |

Only counts and ranges are written: a name-typed parameter contributes its distinct count and how often it is `None`, never the names themselves, and a string parameter only its presence. The residue rows name a map, a class and a slot -- enough to find the placement in the dump, and no content. A statement the class chain does not declare (`class_properties.csv`) is refused and counted as a residue rather than published as a parameter: the property reader locates a block by where it ends, so a mis-parsed start can invent a name, and that control is what keeps it out of the class table.

Census: placements=18510;blocked=15441;unblocked=3069;props_stated=21596;classes=83;maps=73;goto_stated=165;goto_unset=415;props_refused=5;wait_stated=2703;wait_unstated=19;timer_stated=2214;timer_sentinel=1;trigger_stated=2657;trigger_unstated=1;conditions=212;wait_completes=4;wait_blocks=10;wait_no_condition=5;wait_order_free=18;refused_before=26;relocated=8;relocated_props=21.
<!-- /generated:action-parameters -->

## What the parameters say that the graph did not

* **A placement either states its arguments or states nothing, and "nothing" is a class story.** 3,069 of the
  18,510 placements carry no property statement at all — `ACTION_EndSection` (279 of 279) and
  `ACTION_LeaveSequence` (301 of 301) are the marker classes whose body *is* the behaviour, and the rest are
  placements that override nothing and take every default. The census counts both halves, so an empty block is a
  measured property of a class rather than a failed parse; the five placements the re-location moved off a fully
  refused parse are the exception that proves it, because they only *looked* empty.
* **`ACTION_Talk` ships its subtitle in the map.** 718 of its 719 placements state `SubTitleText`, a localized
  `string` in the property block, and all 719 state `myDirectory`; 256 state `DialogueFinishedEvent`. The subtitle
  text a scene shows is placed data, which is why `docs/DIALOGUE.md`'s text census and the voice packages do not
  have to agree one for one. (`M07B`'s placement is the one the re-location recovered: the long parse read five
  scene names off a start three bytes into the block and dropped the four subtitle fields it was sitting on.)
* **`Action_PLAYANIM` is a full action, not a name accident.** All 227 of its placements state `BaseAnim`, and 89
  state `AnimIterations` — the class the `ACTION_` prefix cannot see, and the reason `docs/MAP_ACTION_WIRING.md`
  counts the family by the `super` chain.
* **The 19 waits that state no event are a resolved list.** The `MAP_EVENT_WIRING` count of 2,703 wait sites is
  exactly the placements that state `ExternalEvent`; the other 19 are named by map, class and slot, and the level's
  first `TriggeredCondition` settles each one. **Four complete at once** — an enabled condition makes
  `InitActionFor` return false — and **fifteen block**: ten behind a condition that states no `bEnabled` and so
  takes the class's false default, five in maps that place no condition at all and leave `t` at `None`. Eighteen of
  the 19 would answer the same under any actor order; `M15A`'s is the one that would not, and the package's own
  order puts its enabled condition first. 207 placements also state `bCheckRecentEvents`, the class's own
  lost-wakeup guard — inert on these 19, because the arm is guarded by `ExternalEvent != 'None'`.
* **The fire with no name fires nothing.** `Actor.TriggerEvent` returns on `EventName == 'None'` before it iterates
  and before `Level.AddEventToCache`, so the one `ACTION_TriggerEvent` placement that states no `Event` is a no-op
  rather than a broadcast: the same `None` that means "no filter" to the wait's iterator is an early return here.
* **The random branch is still `FRand()`'s.** All 39 `ACTION_IfRandomPct` placements state a `Probability` between
  0.15 and 0.5 (median 0.3), so no placement has already called its coin — the same conclusion
  `docs/MAP_ACTION_DEFECTS.md` reached by a different walk, and the one place 5.2 still ends in a runtime value.

## What this does not claim

* **That a default is a bug.** A goto to slot 0, a wait on the level's first condition and a 100,000-second timer
  are all legal script; the page says what the class does with the value, not whether the author meant it.
* **That every parameter is read by the class's body.** A `var(Action)` the class never reads (an editor leftover)
  still appears in the census; only the parameters the class's own text names are described above.
* **That a name-typed parameter's identity is known.** Distinct counts and the `None` share are the measurement;
  which names they are is content, and stays in the dump.
* **That a refused statement is a parser bug.** The class-property control refuses 26 statements across the corpus
  whose name the chain does not declare; 21 of them are a block start the locator picked too early and the
  re-location recovers them, and the last five are a real tag the shipped class does not declare (below). Neither
  kind is editable: the block is where the bytes say it is, and the control's job is to keep the data out of the
  class table, not to make the corpus cleaner than it is.

## Next instrument

The residue rows are the live list, and three of its kinds are now lookups rather than caveats: `wait_unstated`
carries the condition its map offers and what the wait does with it, `trigger_unstated` and `timer_unstated` name
the placements whose default is a no-op and a zero-second timer. `block_moved` is the re-location's own record: the
map, the class, the slot, both starts and the names it recovered.

What is left of the refusal list is answered, and it is a fact about `GamePlay.u` rather than about the maps: `Engine`
holds the name in its table, `Engine.IMusicActor` declares it, and three of `Engine`'s class texts spell it, while
`GamePlay.u` holds neither the string nor a way to spell it -- so no class that package ships can declare the
property. The only other packages whose tables hold `EndType` are the two maps that state the tag, which is the
shape of a placement written against a revision that had the property. The engine does not refuse it: the tagged
walk logs it as not found, skips its one-byte size and reads the next tag (`docs/NATIVE_PROPERTIES.md` §4), so a
map saved that way still loads and the placement's other four parameters still apply. `UNKNOWNS.md` U29 carries
the reading; the block below is `inventory/tag_origin.csv`, and `make tag-origin` regenerates it.

<!-- generated:tag-origin -->
**`EndType` is a name `Engine.IMusicActor` declares, no `GamePlay` class can even spell, and the two maps wrote it themselves: the five refused tags are a placement against a class revision this build no longer ships, and the engine reads them as a skip, not a refusal.**

`inventory/tag_origin.csv` walks every `System/*.u` name table (18 packages, 37094 names), every `Maps/*.unr` table (73 maps) and every class text in the former. **1 of 18** packages hold the tag's name, **1** spell it in a class text, and **2 of 73** maps hold it -- the two the refused tags sit in. A map can only write a name its own table holds, and a class can only declare one its package's table holds, so `GamePlay.u`'s table is the whole answer: the property is not there.

| package | names | holds `EndType` | classes declaring | class texts spelling it |
|---|---|---|---|---|
| `AmbientCreatures` | 313 | no | - | 0 |
| `Core` | 472 | no | - | 0 |
| `Editor` | 299 | no | - | 0 |
| `Engine` | 10406 | index 5959 | IMusicActor | 3 |
| `Fire` | 173 | no | - | 0 |
| `GUI` | 2116 | no | - | 0 |
| `GamePlay` | 1201 | no | - | 0 |
| `IpDrv` | 445 | no | - | 0 |
| `Sharpeffects` | 856 | no | - | 0 |
| `StargateContent` | 6970 | no | - | 0 |
| `StargateGame` | 8029 | no | - | 0 |
| `StargateMenus` | 1016 | no | - | 0 |
| `StargateVehicles` | 1848 | no | - | 0 |
| `Stargateeffects` | 585 | no | - | 0 |
| `UDebugMenu` | 652 | no | - | 0 |
| `UPreview` | 164 | no | - | 0 |
| `UWindow` | 1388 | no | - | 0 |
| `UnrealEd` | 161 | no | - | 0 |

The maps that hold the name in their own table:

| map | names | `EndType` index |
|---|---|---|
| `M02A` | 4782 | 702 |
| `M14A` | 4527 | 302 |

**The five placements, read with the shipped locator and its declared-name repair.**

| map | slot | class | tag type | size | value |
|---|---|---|---|---|---|
| `M02A` | 1 | `ACTION_SetIMusic` | Byte | 1 | IMEND_ONESHOT |
| `M14A` | 1 | `ACTION_SetIMusic` | Byte | 1 | IMEND_ONESHOT |
| `M14A` | 1 | `ACTION_SetIMusic` | Byte | 1 | IMEND_ONESHOT |
| `M14A` | 3 | `ACTION_SetIMusic` | Byte | 1 | IMEND_ONESHOT |
| `M14A` | 4 | `ACTION_SetIMusic` | Byte | 1 | IMEND_ONESHOT |

`LevelInfo.EIMusicEndType` declares `IMEND_LOOP`, `IMEND_ONESHOT`; the five tags read IMEND_ONESHOT=5, so every one names a member of the enum the *declaring* class carries, and none is out of range. Each tag's `others_refused` is 0: the block start is the shipped one, and the tag is the only name the shipped `GamePlay.u` cannot declare. What the engine does with it is `docs/NATIVE_PROPERTIES.md` §4/§6.
<!-- /generated:tag-origin -->
