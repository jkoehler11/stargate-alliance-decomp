# What a placed action says about itself — its parameters, and the defaults that decide a run

`docs/MAP_ACTION_WIRING.md` through `docs/MAP_ACTION_DEFECTS.md` read the graph a map's placed actions sit in:
which sequence names which action, in what order, on which path, whether the sequence starts, and which branches
its own data cannot decide. Every one of those pages reads *one* parameter per branch — `ActionNumber`,
`Probability`, `TriggeredConditionTag`. None of them asked what the rest of a placement carries, and the answer
turns out to be the missing half of "placed data": an action's property block is the action's argument list.

A placement states what it overrides; **a parameter it does not state is its class's own `var(Action)` default**, so
the package decides every parameter either way. This page is the census of that surface — one row per class and
parameter, with how many placements state it — and the three defaults that decide a run on their own:

* `ACTION_GotoAction.ProceedToNextAction` assigns `C.ActionNum = Max(0, ActionNumber)`. A placement that states no
  `ActionNumber` therefore does not fall through: it jumps the run back to **slot 0**.
* `ACTION_WaitForEvent.InitActionFor` resolves a `TriggeredCondition` with
  `foreach C.DynamicActors(Class'Gameplay.TriggeredCondition', t, ExternalEvent)`. A placement that states no
  `ExternalEvent` passes the name `None`, which the iterator does not filter on, so the **first** `TriggeredCondition`
  in the level is the one whose `bEnabled` decides whether the wait completes immediately.
* `ACTION_WaitForTimer` hands `PauseTime` straight to `C.SetTimer(PauseTime, false)`. The value is in seconds and
  the largest placed value is how long a sequence can sit still; 100,000 seconds is the corpus's way of saying "not
  in this play" without ending the run.

    python3 tools/action-parameters.py "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15" --sweep -o inventory
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

**No parameter value that is content is written.** A name-typed parameter contributes its distinct count and how
often it is `None`; a string parameter only whether it is stated; a residue row names a map, a class and a slot.
That is enough to find a placement in the dump and not enough to carry game data into the repository.

<!-- generated:action-parameters -->
**18510 placed actions over 83 classes carry 21584 stated properties in 73 of the 73 maps' packages; every other parameter is the class default, and three of those defaults decide a run on their own.**

The wiring, order, path, reach and defect pages read the graph the actions sit in. This page reads what each placement says about itself: its property block, parsed with the shared reader (`tools/prop-stream.py`, the same one `actor-properties` and the checkpoint work use). A property the block does not state is the class's own `var(Action)` default, so a parameter is decided by the package either way -- and for three classes the default is enough to change what a play does.

| population | count |
|---|---:|
| placed actions (the `ScriptedAction` family, minus its two bases) | **18,510** |
| with a property block that terminates exactly at the export's end | **15,441** |
| with no block at all (the class default is the whole placement) | **3,069** |
| property statements read | **21,584** |
| placement classes | **83** |

| control | this walk | committed artifact |
|---|---:|---:|
| placed actions | 18,510 | `action_wiring.csv` totals 18,510 |
| property statements refused because the class chain does not declare them | 26 | `class_properties.csv` |

### The three defaults that decide a run

| parameter | placements | stated | default decides | what the class's own text does with it |
|---|---:|---:|---:|---|
| `ACTION_GotoAction.ActionNumber` | 580 | 165 | **415** | `C.ActionNum = Max(0, ActionNumber)`: a placement that states none sends the run back to slot 0 |
| `ACTION_WaitForEvent.ExternalEvent` | 2722 | 2703 | **19** | the tag is looked up under `None`, which the engine treats as no filter, so the level's first `TriggeredCondition` decides whether the wait completes at once |
| `ACTION_WaitForTimer.PauseTime` | 2216 | 2214 | 2 | handed straight to `SetTimer(PauseTime, false)`, so the largest placed value is how long a sequence can sit still |

**A goto with no `ActionNumber` is a jump to the first element, and none of the 415 sits at the first element already -- each one re-runs its own sequence from the top. `ProceedToNextAction` clamps the value (`Max(0, ActionNumber)`), so the default is slot 0 rather than a no-op. 385 of them sit on a slot of some sequence's array -- the population `MAP_ACTION_ORDER` counts as gotos with no target (its 377 is by slot on its own walk) -- and 30 in none. Of the 165 that state one, 111 target a slot behind themselves, 40 a slot ahead, 0 their own slot, and 14 sit in no sequence's array at all.

**`PauseTime` is the sequence's clock, and the top of its range is a park.** The class hands the value straight to `SetTimer(..., false)`, so 2214 placements state a pause and 2 state none at all -- a zero-second timer that fires as soon as the action starts. The stated values run from 0.001 to 1e+05 seconds (median 2); the top is 27.8 hours, the corpus's way of holding a sequence where it is for a whole play, and 0 placements sit at or below zero.

**The wait with no `ExternalEvent` is not a wait on nothing.** `InitActionFor` runs `foreach C.DynamicActors(Class'Gameplay.TriggeredCondition', t, ExternalEvent)` with the name the placement states; a placement that states none passes `None`, which the iterator does not filter on, so the first `TriggeredCondition` in the level is the one whose `bEnabled` decides the branch. 2703 of the 2722 `ACTION_WaitForEvent` placements state it and 19 do not -- the same 19 the `MAP_EVENT_WIRING` wait count was short of this placement total -- and 207 placements state `bCheckRecentEvents`, which completes a wait immediately when the event already fired.

**`ACTION_Talk` carries its subtitle in the package.** 717 of its 719 placements state `SubTitleText` (a localized `string`, so the text ships in the map, not only in a voice package), 256 state `DialogueFinishedEvent`, and the parameter surface is otherwise `TalkerTag`/`DialogueName`/`myDirectory`. `Action_PLAYANIM` -- the class whose name the `ACTION_` prefix cannot see -- states `BaseAnim` on 227 of its 227 placements, which is what makes it a real action rather than a marker.

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
| `ACTION_Talk` | `myDirectory` | Str | 719 | 31 |  |  |  |
| `ACTION_Talk` | `DialogueName` | Name | 718 | 703 |  |  |  |
| `ACTION_Talk` | `SubTitleText` | Str | 717 | 545 |  |  |  |
| `ACTION_Talk` | `TalkerTag` | Name | 713 | 19 |  |  |  |
| `ACTION_SetBotScript` | `ScriptTargetTag` | Name | 556 | 498 |  |  |  |
| `ACTION_SetBotScript` | `TargetTag` | Name | 539 | 73 |  |  |  |
| `ACTION_TeleportToPoint` | `DestinationTag` | Name | 322 | 296 |  |  |  |
| `ACTION_Talk` | `LookAtTag` | Name | 282 | 27 |  |  |  |
| `ACTION_SetIMusic` | `SetState` | Bool | 280 | 1 |  |  |  |

Only counts and ranges are written: a name-typed parameter contributes its distinct count and how often it is `None`, never the names themselves, and a string parameter only its presence. The residue rows name a map, a class and a slot -- enough to find the placement in the dump, and no content. A statement the class chain does not declare (`class_properties.csv`) is refused and counted as a residue rather than published as a parameter: the property reader locates a block by where it ends, so a mis-parsed start can invent a name, and that control is what keeps it out of the class table.

Census: placements=18510;blocked=15441;unblocked=3069;props_stated=21584;classes=83;maps=73;goto_stated=165;goto_unset=415;props_refused=26;wait_stated=2703;wait_unstated=19;timer_stated=2214;timer_sentinel=1.
<!-- /generated:action-parameters -->

## What the parameters say that the graph did not

* **A placement either states its arguments or states nothing, and "nothing" is a class story.** 3,074 of the
  18,510 placements carry no property statement at all — `ACTION_EndSection` (279 of 279) and
  `ACTION_LeaveSequence` (301 of 301) are the marker classes whose body *is* the behaviour, and the rest are
  placements that override nothing and take every default. The census counts both halves, so an empty block is a
  measured property of a class rather than a failed parse.
* **`ACTION_Talk` ships its subtitle in the map.** 717 of its 719 placements state `SubTitleText`, a localized
  `string` in the property block, and all 719 state `myDirectory`; 256 state `DialogueFinishedEvent`. The subtitle
  text a scene shows is placed data, which is why `docs/DIALOGUE.md`'s text census and the voice packages do not
  have to agree one for one.
* **`Action_PLAYANIM` is a full action, not a name accident.** All 227 of its placements state `BaseAnim`, and 89
  state `AnimIterations` — the class the `ACTION_` prefix cannot see, and the reason `docs/MAP_ACTION_WIRING.md`
  counts the family by the `super` chain.
* **The 19 waits that state no event are a named list.** The `MAP_EVENT_WIRING` count of 2,703 wait sites is exactly
  the placements that state `ExternalEvent`; the residue rows name the other 19 by map, class and slot, so whether
  each one hangs or completes depends on the level's first `TriggeredCondition` — a lookup the event pages can
  make, not an unknown here. 207 placements also state `bCheckRecentEvents`, the class's own lost-wakeup guard.
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
* **That a refused statement is a parser bug to fix.** The class-property control refuses 26 statements across the
  corpus whose name the chain does not declare; each is a block start the locator picked too early, the same
  ambiguity `docs/ACTOR_PROPERTIES.md` measured on actors, and the residue row is the record of it.

## Next instrument

The residue rows are the live list: for each `wait_unstated` placement, resolve the level's first
`TriggeredCondition` and its placed `bEnabled` — the event pages already index that class by tag — and the last
unread parameter of a waited sequence becomes a lookup rather than a caveat. For a refused-property placement, the
locator's candidate start with the most declared names would recover its true block; the actor-side version of that
rule is already in `tools/actor-prefix-anchor.py`.
